from datetime import date
import io
import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
import httpx
from fastapi import Request
from nicegui import ui

DEF_TAX_CODE = 'T1'  # adjust for your Sage setup
# TODO: replace with your real key; this hard-coded fallback is for demo convenience only.
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY') or 'AIzaSyB7xgOfX1I-dhdFl9f5UOfcJLBM-lDoewo'

def build_sales_order_xml(form, lines):
    root = ET.Element('SalesOrders')
    so = ET.SubElement(root, 'SalesOrder')

    # Header
    ET.SubElement(so, 'SalesOrderNumber').text = form['order_number'] or ''
    ET.SubElement(so, 'CustomerAccountRef').text = form['customer_account'] or ''
    ET.SubElement(so, 'CustomerOrderNumber').text = form['customer_order_number'] or ''
    ET.SubElement(so, 'OrderDate').text = form['order_date'].isoformat() if form['order_date'] else ''
    ET.SubElement(so, 'Currency').text = form['currency'] or 'GBP'
    ET.SubElement(so, 'Reference').text = form['reference'] or ''
    ET.SubElement(so, 'Notes').text = form['notes'] or ''

    # Addresses (ship-to)
    ship = ET.SubElement(so, 'DeliveryAddress')
    ET.SubElement(ship, 'Company').text = form['ship_company'] or ''
    ET.SubElement(ship, 'Contact').text = form['ship_contact'] or ''
    ET.SubElement(ship, 'Address1').text = form['ship_addr1'] or ''
    ET.SubElement(ship, 'Address2').text = form['ship_addr2'] or ''
    ET.SubElement(ship, 'Town').text = form['ship_town'] or ''
    ET.SubElement(ship, 'PostCode').text = form['ship_postcode'] or ''
    ET.SubElement(ship, 'Country').text = form['ship_country'] or ''
    ET.SubElement(ship, 'Telephone').text = form['ship_phone'] or ''

    # Lines
    lines_el = ET.SubElement(so, 'Lines')
    for idx, line in enumerate(lines, start=1):
        line_el = ET.SubElement(lines_el, 'Line')
        ET.SubElement(line_el, 'Number').text = str(idx)
        ET.SubElement(line_el, 'ProductCode').text = line['product_code'] or ''
        ET.SubElement(line_el, 'Description').text = line['description'] or ''
        ET.SubElement(line_el, 'Quantity').text = str(line['qty'] or 0)
        ET.SubElement(line_el, 'UnitPrice').text = str(line['unit_price'] or 0)
        ET.SubElement(line_el, 'TaxCode').text = line['tax_code'] or DEF_TAX_CODE
        ET.SubElement(line_el, 'NominalCode').text = line['nominal_code'] or ''
        ET.SubElement(line_el, 'DepartmentCode').text = line['department'] or ''

    # Totals note: Sage calculates totals; omit to let Sage compute.
    return ET.tostring(root, encoding='utf-8', xml_declaration=True).decode()

@ui.page('/')
def index(request: Request):
    ui.colors(primary='#0f766e', secondary='#2563eb')

    params = request.query_params
    get_param = params.get

    def parse_date(value):
        try:
            return date.fromisoformat(value)
        except Exception:
            return date.today()

    def parse_number(value, default):
        try:
            return float(value)
        except Exception:
            return default

    form = {
        'order_number': get_param('order_number', ''),
        'customer_account': get_param('customer_account', ''),
        'customer_order_number': get_param('customer_order_number', ''),
        'order_date': parse_date(get_param('order_date')) if get_param('order_date') else date.today(),
        'currency': get_param('currency', 'GBP'),
        'reference': get_param('reference', ''),
        'notes': get_param('notes', ''),
        # ship-to
        'ship_company': get_param('ship_company', ''),
        'ship_contact': get_param('ship_contact', ''),
        'ship_addr1': get_param('ship_addr1', ''),
        'ship_addr2': get_param('ship_addr2', ''),
        'ship_town': get_param('ship_town', ''),
        'ship_postcode': get_param('ship_postcode', ''),
        'ship_country': get_param('ship_country', 'UK'),
        'ship_phone': get_param('ship_phone', ''),
    }

    def single_line_from_params():
        def first_of(*keys):
            for k in keys:
                val = get_param(k)
                if val is not None:
                    return val
            return None

        has_any = any(first_of(k, f'line0_{k}') is not None for k in [
            'line_product_code', 'line_description', 'line_qty', 'line_unit_price',
            'line_tax_code', 'line_nominal_code', 'line_department',
        ])
        if not has_any:
            return None

        return {
            'product_code': first_of('line_product_code', 'line0_product_code') or '',
            'description': first_of('line_description', 'line0_description') or '',
            'qty': parse_number(first_of('line_qty', 'line0_qty'), 1),
            'unit_price': parse_number(first_of('line_unit_price', 'line0_unit_price'), 0.0),
            'tax_code': first_of('line_tax_code', 'line0_tax_code') or DEF_TAX_CODE,
            'nominal_code': first_of('line_nominal_code', 'line0_nominal_code') or '',
            'department': first_of('line_department', 'line0_department') or '',
        }

    fallback_single_line = single_line_from_params()

    lines = []
    xml_preview = None
    line_container = None
    error_box = None
    map_frame = None
    search_results_box = None
    ship_inputs = {}

    def refresh_xml():
        if xml_preview:
            xml_preview.set_content(build_sales_order_xml(form, lines))

    def set_form(key, value):
        form[key] = value
        # keep UI inputs in sync for address fields when we set them programmatically
        if key in ship_inputs:
            ship_inputs[key].value = value
        refresh_xml()

    def show_errors(errors):
        error_box.clear()
        if not errors:
            return
        with error_box:
            ui.label('Please fix the following before download:').classes('text-red-700 font-semibold')
            for err in errors:
                ui.label(f'- {err}').classes('text-red-600')

    def update_map(lat, lng):
        if not map_frame:
            return
        src = f'https://www.google.com/maps?q={lat},{lng}&z=16&output=embed'
        map_frame.set_content(f'<iframe src=\"{src}\" width=\"100%\" height=\"320\" style=\"border:0;\" loading=\"lazy\" referrerpolicy=\"no-referrer-when-downgrade\"></iframe>')

    def populate_address_from_gmaps(details):
        comps = {}
        for comp in details.get('address_components', []):
            for t in comp.get('types', []):
                comps[t] = comp

        street_number = comps.get('street_number', {}).get('long_name', '')
        route = comps.get('route', {}).get('long_name', '')
        address1 = f'{street_number} {route}'.strip()
        town = comps.get('locality', {}).get('long_name') or comps.get('postal_town', {}).get('long_name') or ''
        postcode = comps.get('postal_code', {}).get('long_name', '')
        country = comps.get('country', {}).get('short_name', form['ship_country'])

        def set_if(key, value):
            if value not in (None, ''):
                set_form(key, value)

        set_if('ship_addr1', address1)
        set_if('ship_addr2', details.get('formatted_address', ''))
        set_if('ship_town', town)
        set_if('ship_postcode', postcode)
        set_if('ship_country', country)

    def search_gmaps(query):
        search_results_box.clear()
        if not GOOGLE_MAPS_API_KEY:
            with search_results_box:
                ui.label('Set GOOGLE_MAPS_API_KEY env var to enable Google address search.').classes('text-red-600')
            return
        if not query:
            return

        try:
            res = httpx.get(
                'https://maps.googleapis.com/maps/api/place/autocomplete/json',
                params={
                    'input': query,
                    'key': GOOGLE_MAPS_API_KEY,
                    'types': 'address',
                },
                timeout=10,
            )
            data = res.json()
            preds = data.get('predictions', [])
        except Exception as exc:
            with search_results_box:
                ui.label(f'Search failed: {exc}').classes('text-red-600')
            return

        if not preds:
            with search_results_box:
                ui.label('No results found.').classes('text-slate-600')
            return

        def select_place(place_id, description):
            try:
                details_res = httpx.get(
                    'https://maps.googleapis.com/maps/api/place/details/json',
                    params={
                        'place_id': place_id,
                        'key': GOOGLE_MAPS_API_KEY,
                    'fields': 'formatted_address,address_components,geometry',
                    },
                    timeout=10,
                )
                details = details_res.json().get('result', {})
            except Exception as exc:
                ui.notify(f'Failed to fetch place details: {exc}', color='red')
                return

            populate_address_from_gmaps(details)
            geo = details.get('geometry', {}).get('location') or {}
            lat, lng = geo.get('lat'), geo.get('lng')
            if lat is not None and lng is not None:
                update_map(lat, lng)
            ui.notify(f'Selected {description}', color='green')

        with search_results_box:
            for pred in preds[:5]:
                ui.button(pred.get('description', 'Result'), on_click=lambda p=pred: select_place(p.get('place_id'), p.get('description', 'Address')), color='secondary').props('flat').classes('w-full justify-start text-left')

    def add_line(prefill=None):
        prefill = prefill or {}
        lines.append({
            'product_code': prefill.get('product_code', ''),
            'description': prefill.get('description', ''),
            'qty': prefill.get('qty', 1),
            'unit_price': prefill.get('unit_price', 0.0),
            'tax_code': prefill.get('tax_code', DEF_TAX_CODE),
            'nominal_code': prefill.get('nominal_code', ''),
            'department': prefill.get('department', ''),
        })
        redraw_lines()

    def remove_line(i):
        if 0 <= i < len(lines):
            lines.pop(i)
            redraw_lines()

    def set_line(i, key, value):
        lines[i][key] = value
        refresh_xml()

    def redraw_lines():
        line_container.clear()
        with line_container:
            for i, line in enumerate(lines):
                with ui.row().classes('items-end w-full gap-2'):
                    ui.input('Product Code', value=line['product_code'], on_change=lambda e, i=i: set_line(i, 'product_code', e.value)).props('outlined dense').classes('w-32')
                    ui.input('Description', value=line['description'], on_change=lambda e, i=i: set_line(i, 'description', e.value)).props('outlined dense').classes('w-64')
                    ui.number('Qty', value=line['qty'], min=0, step=1, on_change=lambda e, i=i: set_line(i, 'qty', e.value)).props('outlined dense').classes('w-24')
                    ui.number('Unit Price', value=line['unit_price'], min=0, step=0.01, format='%.2f', on_change=lambda e, i=i: set_line(i, 'unit_price', e.value)).props('outlined dense').classes('w-28')
                    ui.input('Tax Code', value=line['tax_code'], on_change=lambda e, i=i: set_line(i, 'tax_code', e.value)).props('outlined dense').classes('w-20')
                    ui.input('Nominal Code', value=line['nominal_code'], on_change=lambda e, i=i: set_line(i, 'nominal_code', e.value)).props('outlined dense').classes('w-24')
                    ui.input('Department', value=line['department'], on_change=lambda e, i=i: set_line(i, 'department', e.value)).props('outlined dense').classes('w-24')
                    ui.button('Remove', on_click=lambda i=i: remove_line(i), color='red').props('flat')
        refresh_xml()

    def download_xml():
        errors = []
        if not form['customer_account']:
            errors.append('Customer Account Ref is required.')
        if not lines:
            errors.append('At least one line item is required.')
        for idx, line in enumerate(lines, start=1):
            has_content = any(line.get(k) for k in line.keys())
            if has_content and not line.get('product_code'):
                errors.append(f'Line {idx}: Product Code is required when a line has content.')
            if line.get('qty', 0) <= 0:
                errors.append(f'Line {idx}: Quantity must be greater than zero.')
            if line.get('unit_price', 0) < 0:
                errors.append(f'Line {idx}: Unit Price cannot be negative.')
        if errors:
            show_errors(errors)
            ui.notify('Please fix validation issues before downloading.', color='red')
            return

        show_errors([])
        xml_content = build_sales_order_xml(form, lines)
        return ui.download(io.BytesIO(xml_content.encode()), 'sales_order.xml')

    def build_share_link():
        base_url = str(request.url.replace(query=''))
        data = {}
        for key, value in form.items():
            if value:
                data[key] = value.isoformat() if isinstance(value, date) else value
        for idx, line in enumerate(lines):
            for k, v in line.items():
                if v not in (None, ''):
                    data[f'line{idx}_{k}'] = v
        query = urlencode(data, doseq=True)
        return f'{base_url}?{query}' if query else base_url

    def copy_share_link():
        link = build_share_link()
        safe = json.dumps(link)
        ui.run_javascript(f'navigator.clipboard.writeText({safe});')
        ui.notify('Prefilled link copied to clipboard', color='green')

    with ui.column().classes('min-h-screen items-center bg-slate-50 py-6 gap-4'):
        with ui.row().classes('w-11/12 max-w-6xl items-center justify-between'):
            ui.label('Sales Order Entry').classes('text-3xl font-semibold text-slate-800')
            with ui.row().classes('gap-2'):
                ui.button('Copy Prefilled Link', on_click=copy_share_link, color='secondary').props('flat')
                ui.button('Download XML', on_click=download_xml, color='primary').props('unelevated')

        with ui.row().classes('w-11/12 max-w-6xl gap-4 items-start'):
            with ui.card().classes('w-full shadow-sm'):
                ui.label('Order Details').classes('text-lg font-semibold text-slate-700 mb-2')
                with ui.grid(columns=2).classes('gap-3 w-full md:grid-cols-2 grid-cols-1'):
                    ui.input('Order Number', value=form['order_number'], on_change=lambda e: set_form('order_number', e.value)).props('outlined dense').classes('w-full')
                    ui.input('Customer Account Ref', value=form['customer_account'], on_change=lambda e: set_form('customer_account', e.value)).props('outlined dense').classes('w-full')
                    ui.input('Customer Order Number', value=form['customer_order_number'], on_change=lambda e: set_form('customer_order_number', e.value)).props('outlined dense').classes('w-full')
                    with ui.column().classes('w-full'):
                        ui.label('Order Date').classes('text-sm text-slate-600')
                        ui.date(value=form['order_date'], on_change=lambda e: set_form('order_date', e.value)).classes('w-full').props('outlined dense')
                    ui.input('Currency', value=form['currency'], on_change=lambda e: set_form('currency', e.value)).props('outlined dense').classes('w-full')
                    ui.input('Reference', value=form['reference'], on_change=lambda e: set_form('reference', e.value)).props('outlined dense').classes('w-full')
                    ui.textarea('Notes', value=form['notes'], on_change=lambda e: set_form('notes', e.value)).props('outlined autogrow').classes('md:col-span-2 w-full')

            with ui.card().classes('w-full shadow-sm'):
                ui.label('Delivery Location (Google Search)').classes('text-lg font-semibold text-slate-700 mb-2')
                with ui.row().classes('gap-2 items-center'):
                    search_input = ui.input('Search address...', placeholder='e.g. 10 Downing Street, London', on_change=lambda e: search_gmaps(e.value)).props('outlined dense').classes('grow')
                    ui.button('Search', on_click=lambda: search_gmaps(search_input.value), color='primary').props('unelevated')
                if not GOOGLE_MAPS_API_KEY:
                    ui.label('Set GOOGLE_MAPS_API_KEY in your environment to enable Google search.').classes('text-amber-700 text-sm')
                map_frame = ui.html('<div class=\"w-full h-80 flex items-center justify-center text-slate-500\">Select a result to preview the map</div>', sanitize=False).classes('w-full mt-2')
                search_results_box = ui.column().classes('w-full gap-1 mt-1 border rounded bg-white shadow-sm p-2').style('max-height:220px; overflow-y:auto;')

            with ui.card().classes('w-full shadow-sm'):
                ui.label('Delivery Address').classes('text-lg font-semibold text-slate-700 mb-2')
                with ui.grid(columns=2).classes('gap-3 w-full md:grid-cols-2 grid-cols-1'):
                    ship_inputs['ship_company'] = ui.input('Company', value=form['ship_company'], on_change=lambda e: set_form('ship_company', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_contact'] = ui.input('Contact', value=form['ship_contact'], on_change=lambda e: set_form('ship_contact', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_addr1'] = ui.input('Address 1', value=form['ship_addr1'], on_change=lambda e: set_form('ship_addr1', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_addr2'] = ui.input('Address 2', value=form['ship_addr2'], on_change=lambda e: set_form('ship_addr2', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_town'] = ui.input('Town/City', value=form['ship_town'], on_change=lambda e: set_form('ship_town', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_postcode'] = ui.input('Post Code', value=form['ship_postcode'], on_change=lambda e: set_form('ship_postcode', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_country'] = ui.input('Country', value=form['ship_country'], on_change=lambda e: set_form('ship_country', e.value)).props('outlined dense').classes('w-full')
                    ship_inputs['ship_phone'] = ui.input('Telephone', value=form['ship_phone'], on_change=lambda e: set_form('ship_phone', e.value)).props('outlined dense').classes('w-full')

        with ui.card().classes('w-11/12 max-w-6xl shadow-sm'):
            with ui.row().classes('items-center justify-between w-full'):
                ui.label('Line Items').classes('text-lg font-semibold text-slate-700')
                ui.button('Add Line Item', on_click=add_line, color='secondary').props('flat')
            line_container = ui.column().classes('w-full gap-2 mt-2')
            if fallback_single_line:
                add_line(fallback_single_line)
            else:
                add_line()

        with ui.expansion('XML Preview', value=False).classes('w-11/12 max-w-6xl shadow-sm bg-white'):
            xml_preview = ui.code(language='xml').classes('w-full max-h-80')

        error_box = ui.column().classes('w-11/12 max-w-6xl gap-1')

        refresh_xml()

ui.run(reload=False, port=8081)
