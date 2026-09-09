#!/usr/bin/env python3
"""WANPLAN features patch — bash on VPS to apply:
   - Add schema (locations, suppliers, purchase_orders, purchase_order_items) + product columns
   - Register wanplan_addon module with app.py
   - Add nav links in base.html
Usage: run on VPS: python3 apply_features.py
"""
import re

def apply_app_py():
    src = open('app.py').read()

    # 1. Add product columns to SQLITE migration list
    add_cols_sqlite = [
        '    "ALTER TABLE products ADD COLUMN barcode TEXT",\n',
        '    "ALTER TABLE products ADD COLUMN location_id INTEGER",\n',
        '    "ALTER TABLE products ADD COLUMN supplier_id INTEGER",\n',
        '    "ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER DEFAULT 5",\n',
    ]

    # 2. Add new table definitions to SCHEMA_SQLITE (before closing ''')
    schema_anchor = "    );\n'''"
    new_tables = '''    );
    CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL DEFAULT 1,
        name TEXT NOT NULL, address TEXT, phone TEXT,
        is_default INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL DEFAULT 1,
        name TEXT NOT NULL, phone TEXT, email TEXT, address TEXT, notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL DEFAULT 1,
        supplier_id INTEGER,
        status TEXT DEFAULT 'pending', total REAL DEFAULT 0, notes TEXT, expected_date TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_id INTEGER NOT NULL, product_id INTEGER, product_title TEXT,
        quantity INTEGER NOT NULL DEFAULT 0, received_qty INTEGER DEFAULT 0, unit_cost REAL DEFAULT 0
    );
'''

    # Insert new tables into SCHEMA_SQLITE: it ends with categories );\n'''
    # The first occurrence is the sqlite schema. Insert before the closing quotes.
    idx = src.index(schema_anchor)
    src = src[:idx] + new_tables[0] + src[idx + len('    );\n'):]  # careful
    # Simpler: replace the anchor to include new tables before closing '''
    src = src.replace(schema_anchor, new_tables, 1)

    # 3. Add migration columns at start of MIGRATION_SQLITE list
    mig_anchor = "MIGRATION_SQLITE = [\n"
    src = src.replace(mig_anchor, mig_anchor + ''.join(add_cols_sqlite), 1)

    # 4. Register addon before `if __name__ == '__main__':`
    reg_code = '''
# ---- WANPLAN feature addon (barcode, locations, suppliers, POs, charts, print, WhatsApp) ----
try:
    import wanplan_addon
    wanplan_addon.register(app)
except Exception as e:
    import sys
    print('[ADDON ERROR] %s' % e, file=sys.stderr)

if __name__ == '__main__':'''
    src = src.replace("if __name__ == '__main__':", reg_code, 1)

    open('app.py', 'w').write(src)
    print('app.py patched')

def apply_base_html():
    src = open('templates/base.html').read()
    # Add nav items: Scan, Suppliers, Locations, Purchase Orders, Charts
    links = '''
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('scan_page') }}"><i class="bi bi-upc-scan"></i>Scan</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('suppliers') }}"><i class="bi bi-truck"></i>Suppliers</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('purchase_orders') }}"><i class="bi bi-cart-check"></i>Orders</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('locations') }}"><i class="bi bi-geo-alt"></i>Locations</a></li>'''
    anchor = '                    <li class="nav-item"><a class="nav-link" href="{{ url_for(\'products\') }}"><i class="bi bi-box-seam"></i>Products</a></li>'
    if 'scan_page' not in src:
        src = src.replace(anchor, anchor + links, 1)
    # Add charts to reports dropdown
    chart_item = '                            <li><a class="dropdown-item" href="{{ url_for(\'pnl_report\') }}"><i class="bi bi-cash-stack"></i>Profit & Loss</a></li>'
    if 'charts_report' not in src:
        src = src.replace(chart_item, chart_item + '\n                            <li><a class="dropdown-item" href="{{ url_for(\'charts_report\') }}"><i class="bi bi-graph-up"></i>Profit Charts</a></li>', 1)
    open('templates/base.html', 'w').write(src)
    print('base.html patched')

# Add thermal printer + whatsapp to sales page indirectly via new template blocks
def add_sales_actions():
    src = open('templates/sales.html').read()
    if 'WanPrint' not in src:
        src = src.replace('{% block scripts %}{% endblock %}',
                          '''{% block scripts %}
<script src="{{ url_for('static', filename='thermal-printer.js') }}"></script>
<script>
function printReceipt(saleId) {
    fetch('{{ url_for('api_print_receipt', sale_id=0) }}'.replace('/0', '/' + saleId))
        .then(r => r.json())
        .then(data => WanPrint.printReceipt(data));
}
function whatsappReceipt(saleId) {
    fetch('{{ url_for('api_whatsapp_send', sale_id=0) }}'.replace('/0', '/' + saleId))
        .then(r => r.json())
        .then(data => {
            if (data.whatsapp_url) {
                const url = data.whatsapp_url + '?text=' + encodeURIComponent(data.message);
                window.open(url, '_blank');
            } else {
                alert('No phone number on file for this customer.');
            }
        });
}
</script>
{% endblock %}''', 1)
        open('templates/sales.html', 'w').write(src)
        print('sales.html patched')

apply_app_py()
apply_base_html()
add_sales_actions()
