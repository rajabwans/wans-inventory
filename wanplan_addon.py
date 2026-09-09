"""WANPLAN feature add-on: barcode, locations, suppliers, POs, charts, WhatsApp, print, SMS."""
import os
from datetime import date, datetime, timedelta

def register(app):
    from flask import render_template, request, redirect, url_for, flash, session, jsonify

    # pull helpers from the app module namespace
    from app import (query, insert_row, db_commit, db_close, get_db, get_business_id,
                     login_required, admin_required, sanitize_input, log_audit,
                     CURRENCY, COMPANY_NAME, get_effective_plan, check_limit, PRODUCT_NAME,
                     PAYMENT_PHONE)

    # ---- Barcode ----
    @app.route('/scan')
    @login_required
    def scan_page():
        return render_template('scan.html')

    @app.route('/api/scan-barcode')
    @login_required
    def api_scan_barcode():
        code = sanitize_input(request.args.get('barcode', '')).strip()
        if not code:
            return jsonify({'found': False, 'error': 'No barcode'}), 400
        conn = get_db(); bid = get_business_id()
        p = query(conn, 'SELECT * FROM products WHERE barcode = ? AND business_id = ?', (code, bid)).fetchone()
        if not p:
            p = query(conn, 'SELECT * FROM products WHERE CAST(id AS TEXT) = ? AND business_id = ?', (code, bid)).fetchone()
        db_close(conn)
        if not p:
            return jsonify({'found': False})
        d = dict(p)
        return jsonify({'found': True, 'product': d})

    # ---- Locations ----
    @app.route('/locations')
    @login_required
    def locations():
        conn = get_db(); bid = get_business_id()
        rows = query(conn, '''SELECT loc.*, (SELECT COUNT(*) FROM products p WHERE p.location_id = loc.id) as product_count
                              FROM locations loc WHERE loc.business_id = ? ORDER BY loc.name''', (bid,)).fetchall()
        db_close(conn)
        return render_template('locations/list.html', locations=rows, total=len(rows))

    @app.route('/locations/add', methods=['GET', 'POST'])
    @login_required
    def add_location():
        if request.method == 'POST':
            name = sanitize_input(request.form.get('name', ''))
            address = sanitize_input(request.form.get('address', ''))
            phone = sanitize_input(request.form.get('phone', ''))
            is_default = 1 if request.form.get('is_default') else 0
            if not name:
                flash('Location name is required', 'danger'); return redirect(url_for('add_location'))
            conn = get_db(); bid = get_business_id()
            loc_id = insert_row(conn, 'INSERT INTO locations (business_id, name, address, phone, is_default) VALUES (?,?,?,?,?)',
                                (bid, name, address, phone, is_default))
            if is_default:
                query(conn, 'UPDATE locations SET is_default = 0 WHERE business_id = ? AND id != ?', (bid, loc_id))
            db_commit(conn); log_audit(conn, 'create', 'locations', loc_id, f'Added location: {name}')
            db_close(conn); flash('Location added', 'success'); return redirect(url_for('locations'))
        return render_template('locations/form.html', location=None)

    @app.route('/locations/edit/<int:id>', methods=['GET', 'POST'])
    @login_required
    def edit_location(id):
        conn = get_db(); bid = get_business_id()
        loc = query(conn, 'SELECT * FROM locations WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        if not loc:
            db_close(conn); flash('Location not found', 'danger'); return redirect(url_for('locations'))
        if request.method == 'POST':
            name = sanitize_input(request.form.get('name', ''))
            if not name:
                flash('Name required', 'danger'); db_close(conn); return redirect(url_for('edit_location', id=id))
            address = sanitize_input(request.form.get('address', ''))
            phone = sanitize_input(request.form.get('phone', ''))
            is_default = 1 if request.form.get('is_default') else 0
            query(conn, 'UPDATE locations SET name=?, address=?, phone=?, is_default=? WHERE id=?', (name, address, phone, is_default, id))
            if is_default:
                query(conn, 'UPDATE locations SET is_default = 0 WHERE business_id = ? AND id != ?', (bid, id))
            db_commit(conn); log_audit(conn, 'update', 'locations', id, f'Updated location: {name}')
            db_close(conn); flash('Location updated', 'success'); return redirect(url_for('locations'))
        db_close(conn)
        return render_template('locations/form.html', location=loc)

    @app.route('/locations/delete/<int:id>', methods=['POST'])
    @login_required
    def delete_location(id):
        conn = get_db(); bid = get_business_id()
        query(conn, 'UPDATE products SET location_id = NULL WHERE location_id = ? AND business_id = ?', (id, bid))
        query(conn, 'DELETE FROM locations WHERE id = ? AND business_id = ?', (id, bid))
        db_commit(conn); db_close(conn); flash('Location deleted', 'success')
        return redirect(url_for('locations'))

    # ---- Suppliers ----
    @app.route('/suppliers')
    @login_required
    def suppliers():
        conn = get_db(); bid = get_business_id()
        rows = query(conn, '''SELECT s.*, (SELECT COUNT(*) FROM products p WHERE p.supplier_id = s.id) as product_count
                              FROM suppliers s WHERE s.business_id = ? ORDER BY s.name''', (bid,)).fetchall()
        db_close(conn)
        return render_template('suppliers/list.html', suppliers=rows, total=len(rows))

    @app.route('/suppliers/add', methods=['GET', 'POST'])
    @login_required
    def add_supplier():
        if request.method == 'POST':
            name = sanitize_input(request.form.get('name', ''))
            if not name:
                flash('Supplier name is required', 'danger'); return redirect(url_for('add_supplier'))
            phone = sanitize_input(request.form.get('phone', ''))
            email = sanitize_input(request.form.get('email', ''))
            address = sanitize_input(request.form.get('address', ''))
            notes = sanitize_input(request.form.get('notes', ''))
            conn = get_db(); bid = get_business_id()
            sid = insert_row(conn, 'INSERT INTO suppliers (business_id, name, phone, email, address, notes) VALUES (?,?,?,?,?,?)',
                             (bid, name, phone, email, address, notes))
            db_commit(conn); log_audit(conn, 'create', 'suppliers', sid, f'Added supplier: {name}')
            db_close(conn); flash('Supplier added', 'success'); return redirect(url_for('suppliers'))
        return render_template('suppliers/form.html', supplier=None)

    @app.route('/suppliers/edit/<int:id>', methods=['GET', 'POST'])
    @login_required
    def edit_supplier(id):
        conn = get_db(); bid = get_business_id()
        s = query(conn, 'SELECT * FROM suppliers WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        if not s:
            db_close(conn); flash('Supplier not found', 'danger'); return redirect(url_for('suppliers'))
        if request.method == 'POST':
            name = sanitize_input(request.form.get('name', ''))
            if not name:
                flash('Name required', 'danger'); db_close(conn); return redirect(url_for('edit_supplier', id=id))
            query(conn, 'UPDATE suppliers SET name=?, phone=?, email=?, address=?, notes=? WHERE id=?',
                  (name, sanitize_input(request.form.get('phone','')), sanitize_input(request.form.get('email','')),
                   sanitize_input(request.form.get('address','')), sanitize_input(request.form.get('notes','')), id))
            db_commit(conn); log_audit(conn, 'update', 'suppliers', id, f'Updated supplier: {name}')
            db_close(conn); flash('Supplier updated', 'success'); return redirect(url_for('suppliers'))
        db_close(conn)
        return render_template('suppliers/form.html', supplier=s)

    @app.route('/suppliers/delete/<int:id>', methods=['POST'])
    @login_required
    def delete_supplier(id):
        conn = get_db(); bid = get_business_id()
        query(conn, 'UPDATE products SET supplier_id = NULL WHERE supplier_id = ? AND business_id = ?', (id, bid))
        query(conn, 'DELETE FROM suppliers WHERE id = ? AND business_id = ?', (id, bid))
        db_commit(conn); db_close(conn); flash('Supplier deleted', 'success')
        return redirect(url_for('suppliers'))

    # ---- Purchase Orders ----
    @app.route('/purchase-orders')
    @login_required
    def purchase_orders():
        conn = get_db(); bid = get_business_id()
        rows = query(conn, '''SELECT po.*, sup.name as supplier_name,
                                   (SELECT COUNT(*) FROM purchase_order_items i WHERE i.po_id = po.id) as item_count
                               FROM purchase_orders po LEFT JOIN suppliers sup ON po.supplier_id = sup.id
                               WHERE po.business_id = ? ORDER BY po.created_at DESC''', (bid,)).fetchall()
        db_close(conn)
        return render_template('purchase_orders/list.html', orders=rows, total=len(rows))

    @app.route('/purchase-orders/add', methods=['GET', 'POST'])
    @login_required
    def add_purchase_order():
        conn = get_db(); bid = get_business_id()
        if request.method == 'POST':
            supplier_id = request.form.get('supplier_id') or None
            if supplier_id: supplier_id = int(supplier_id)
            expected_date = sanitize_input(request.form.get('expected_date', ''))
            status = sanitize_input(request.form.get('status', 'pending'))
            notes = sanitize_input(request.form.get('notes', ''))
            pids = request.form.getlist('product_id[]')
            titles = request.form.getlist('product_title[]')
            qtys = request.form.getlist('quantity[]')
            costs = request.form.getlist('unit_cost[]')
            total = 0
            po_id = insert_row(conn, 'INSERT INTO purchase_orders (business_id, supplier_id, status, total, notes, expected_date) VALUES (?,?,?,?,?,?)',
                               (bid, supplier_id, status, 0, notes, expected_date))
            added = 0
            for i in range(len(qtys)):
                qty = max(0, int(qtys[i] or 0))
                cost = max(0, float(costs[i] or 0))
                if qty <= 0: continue
                pid = pids[i] if i < len(pids) else ''
                ptr = int(pid) if pid else None
                title = titles[i] if i < len(titles) else ''
                query(conn, 'INSERT INTO purchase_order_items (po_id, product_id, product_title, quantity, unit_cost) VALUES (?,?,?,?,?)',
                      (po_id, ptr, title, qty, cost))
                total += qty * cost
                added += 1
            if added == 0:
                flash('Add at least one item with quantity', 'danger')
                query(conn, 'DELETE FROM purchase_orders WHERE id = ?', (po_id,))
                db_commit(conn); db_close(conn); return redirect(url_for('add_purchase_order'))
            query(conn, 'UPDATE purchase_orders SET total = ? WHERE id = ?', (total, po_id))
            db_commit(conn); log_audit(conn, 'create', 'purchase_orders', po_id, f'Created PO #{po_id}')
            db_close(conn); flash('Purchase order created', 'success'); return redirect(url_for('purchase_orders'))
        suppliers = query(conn, 'SELECT * FROM suppliers WHERE business_id = ? ORDER BY name', (bid,)).fetchall()
        products = query(conn, 'SELECT * FROM products WHERE business_id = ? ORDER BY title', (bid,)).fetchall()
        db_close(conn)
        return render_template('purchase_orders/form.html', suppliers=suppliers, products=products)

    @app.route('/purchase-orders/<int:id>')
    @login_required
    def view_purchase_order(id):
        conn = get_db(); bid = get_business_id()
        po = query(conn, 'SELECT po.*, sup.name as supplier_name FROM purchase_orders po LEFT JOIN suppliers sup ON po.supplier_id = sup.id WHERE po.id = ? AND po.business_id = ?', (id, bid)).fetchone()
        if not po:
            db_close(conn); flash('PO not found', 'danger'); return redirect(url_for('purchase_orders'))
        items = query(conn, 'SELECT * FROM purchase_order_items WHERE po_id = ?', (id,)).fetchall()
        db_close(conn)
        return render_template('purchase_orders/detail.html', po=po, items=items)

    @app.route('/purchase-orders/<int:id>/receive', methods=['GET', 'POST'])
    @login_required
    def receive_purchase_order(id):
        conn = get_db(); bid = get_business_id()
        po = query(conn, 'SELECT * FROM purchase_orders WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        if not po:
            db_close(conn); flash('PO not found', 'danger'); return redirect(url_for('purchase_orders'))
        if request.method == 'POST':
            item_rows = query(conn, 'SELECT * FROM purchase_order_items WHERE po_id = ?', (id,)).fetchall()
            all_received = True
            for item in item_rows:
                recv = int(request.form.get('receive_' + str(item['id']), 0) or 0)
                if recv > 0:
                    new_received = item['received_qty'] + recv
                    query(conn, 'UPDATE purchase_order_items SET received_qty = ? WHERE id = ?', (new_received, item['id']))
                    if item['product_id']:
                        query(conn, 'UPDATE products SET quantity = quantity + ? WHERE id = ? AND business_id = ?', (recv, item['product_id'], bid))
                    elif item['product_title']:
                        pid = insert_row(conn, 'INSERT INTO products (business_id, title, quantity, buying_price) VALUES (?,?,?,?)',
                                         (bid, item['product_title'], recv, item['unit_cost']))
                        query(conn, 'UPDATE purchase_order_items SET product_id = ? WHERE id = ?', (pid, item['id']))
                    all_received = all_received and (new_received >= item['quantity'])
            status = 'received' if all_received else 'partial'
            query(conn, 'UPDATE purchase_orders SET status = ? WHERE id = ?', (status, id))
            db_commit(conn); log_audit(conn, 'receive', 'purchase_orders', id, f'Received items for PO #{id}')
            db_close(conn); flash('Items received', 'success')
            return redirect(url_for('view_purchase_order', id=id))
        items = query(conn, 'SELECT * FROM purchase_order_items WHERE po_id = ?', (id,)).fetchall()
        db_close(conn)
        return render_template('purchase_orders/receive.html', po=po, items=items)

    # ---- Charts ----
    @app.route('/reports/charts')
    @login_required
    def charts_report():
        today = date.today()
        period = request.args.get('period', '30')
        if period == 'custom':
            date_from = sanitize_input(request.args.get('date_from', (today - timedelta(days=30)).isoformat()))
            date_to = sanitize_input(request.args.get('date_to', today.isoformat()))
        else:
            days = int(period)
            date_from = (today - timedelta(days=days)).isoformat()
            date_to = today.isoformat()
        conn = get_db(); bid = get_business_id()
        rows = query(conn, '''SELECT DATE(sale_date) as d, COALESCE(SUM(total_amount),0) as rev, COALESCE(SUM(profit),0) as prof
                              FROM sales WHERE business_id = ? AND sale_date >= ? AND sale_date <= ? GROUP BY DATE(sale_date) ORDER BY d''',
                     (bid, date_from, date_to + ' 23:59:59')).fetchall()
        labels = []; rev = []; prof = []
        for r in rows:
            labels.append(str(r['d'])[:10]); rev.append(float(r['rev'])); prof.append(float(r['prof']))
        exps = query(conn, '''SELECT DATE(expense_date) as d, COALESCE(SUM(amount),0) as amt FROM expenses
                              WHERE business_id = ? AND expense_date >= ? AND expense_date <= ? GROUP BY DATE(expense_date) ORDER BY d''',
                     (bid, date_from, date_to + ' 23:59:59')).fetchall()
        exp_map = {str(e['d'])[:10]: float(e['amt']) for e in exps}
        expenses = [exp_map.get(l, 0) for l in labels]
        exp_cat = query(conn, '''SELECT COALESCE(category,'Other') as cat, COALESCE(SUM(amount),0) as amt FROM expenses
                                 WHERE business_id = ? AND expense_date >= ? AND expense_date <= ? GROUP BY category ORDER BY amt DESC LIMIT 8''',
                        (bid, date_from, date_to + ' 23:59:59')).fetchall()
        top = query(conn, '''SELECT p.title, COALESCE(SUM(s.total_amount),0) as rev FROM sales s JOIN products p ON s.product_id = p.id
                             WHERE s.business_id = ? AND p.business_id = ? AND s.sale_date >= ? AND s.sale_date <= ?
                             GROUP BY p.id ORDER BY rev DESC LIMIT 6''',
                    (bid, bid, date_from, date_to + ' 23:59:59')).fetchall()
        rev_total = float(query(conn, 'SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE business_id = ? AND sale_date >= ? AND sale_date <= ?', (bid, date_from, date_to + ' 23:59:59')).fetchone()['t'])
        prof_total = float(query(conn, 'SELECT COALESCE(SUM(profit),0) as t FROM sales WHERE business_id = ? AND sale_date >= ? AND sale_date <= ?', (bid, date_from, date_to + ' 23:59:59')).fetchone()['t'])
        exp_total = float(sum(e['amt'] for e in exp_cat))
        db_close(conn)
        chart = {
            'profit': {'labels': labels, 'revenue': rev, 'profit': prof, 'expenses': expenses},
            'expenses': {'labels': [e['cat'] for e in exp_cat], 'values': [float(e['amt']) for e in exp_cat]},
            'top_products': {'labels': [t['title'][:20] for t in top], 'values': [float(t['rev']) for t in top]},
            'daily_sales': {'labels': labels, 'values': rev},
        }
        totals = {'revenue': rev_total, 'net_profit': prof_total - exp_total, 'expenses': exp_total}
        return render_template('reports/charts.html', chart_data=chart, totals=totals, period=period, date_from=date_from, date_to=date_to)

    # ---- Print ----
    @app.route('/api/print-receipt/<int:sale_id>')
    @login_required
    def api_print_receipt(sale_id):
        conn = get_db(); bid = get_business_id()
        sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (sale_id, bid)).fetchone()
        if not sale:
            db_close(conn); return jsonify({'error': 'sale not found'}), 404
        product = query(conn, 'SELECT * FROM products WHERE id = ?', (sale['product_id'],)).fetchone()
        cust = None
        if sale['customer_id']:
            cust = query(conn, 'SELECT * FROM customers WHERE id = ? AND business_id = ?', (sale['customer_id'], bid)).fetchone()
        biz = query(conn, 'SELECT * FROM businesses WHERE id = ?', (bid,)).fetchone()
        db_close(conn)
        sd = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d %H:%M')
        return jsonify({
            'business_name': biz['name'] if biz else COMPANY_NAME,
            'title': 'SALE RECEIPT', 'receipt_no': str(sale_id), 'date': sd, 'currency': CURRENCY,
            'items': [{'name': product['title'] if product else 'Product', 'qty': sale['quantity_sold'], 'price': sale['unit_price']}],
            'total': sale['total_amount'], 'customer_name': (cust['name'] if cust else 'Walk-in'),
            'product_name': product['title'] if product else None, 'qty': sale['quantity_sold'], 'unit_price': sale['unit_price'],
        })

    # ---- WhatsApp ----
    @app.route('/api/whatsapp-send/<int:sale_id>')
    @login_required
    def api_whatsapp_send(sale_id):
        conn = get_db(); bid = get_business_id()
        sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (sale_id, bid)).fetchone()
        if not sale:
            db_close(conn); return jsonify({'error': 'sale not found'}), 404
        product = query(conn, 'SELECT * FROM products WHERE id = ?', (sale['product_id'],)).fetchone()
        cust = None; phone = ''
        if sale['customer_id']:
            cust = query(conn, 'SELECT * FROM customers WHERE id = ? AND business_id = ?', (sale['customer_id'], bid)).fetchone()
            if cust: phone = cust['phone'] or ''
        biz = query(conn, 'SELECT * FROM businesses WHERE id = ?', (bid,)).fetchone()
        db_close(conn)
        bizname = biz['name'] if biz else COMPANY_NAME
        pd = product['title'] if product else 'Product'
        sd = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
        msg = (f"*{bizname} — Receipt*\n--------------------\nReceipt #{sale_id}\nDate: {sd}\n--------------------\n"
               f"{pd} x{sale['quantity_sold']}\nUnit: {CURRENCY} {sale['unit_price']:,.0f}\n"
               f"*Total: {CURRENCY} {sale['total_amount']:,.0f}*\n--------------------\nThank you! Powered by {PRODUCT_NAME}")
        wa = 'https://wa.me/' + phone.strip().replace('+','').replace(' ','') if phone else ''
        return jsonify({'phone': phone, 'message': msg, 'whatsapp_url': wa, 'customer_name': cust['name'] if cust else 'Walk-in'})
