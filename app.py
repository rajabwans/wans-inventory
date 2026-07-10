import os, sys, csv, io, re, secrets
import sqlite3
from functools import wraps
from datetime import date, datetime, timedelta
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, send_file, abort, g)
from flask_wtf.csrf import CSRF
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.environ.get('DATABASE_URL')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
csrf = CSRF(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["500 per day"])

PASSWORD = os.environ.get('APP_PASSWORD', 'wans123')
COMPANY_NAME = os.environ.get('COMPANY_NAME', 'WANS COLLECTION')
CURRENCY = os.environ.get('CURRENCY', 'UGX')
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inventory.db'))

IS_PG = bool(DATABASE_URL)

PER_PAGE = 20

@app.errorhandler(500)
def internal_error(e):
    return render_template('error.html', error='Something went wrong. Please try again.'), 500

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error='Page not found.'), 404

@app.errorhandler(429)
def rate_limited(e):
    return render_template('error.html', error='Too many requests. Please slow down.'), 429

@app.context_processor
def inject_globals():
    return dict(COMPANY_NAME=COMPANY_NAME, CURRENCY=CURRENCY)

if IS_PG:
    import psycopg2
    import psycopg2.extras

def get_db():
    if IS_PG:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q(sql, params=None):
    if IS_PG:
        return sql.replace('?', '%s'), params
    return sql, params

def query(conn, sql, params=None):
    sql, params = q(sql, params)
    if IS_PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or [])
        return cur
    return conn.execute(sql, params or [])

def db_commit(conn):
    if not IS_PG:
        conn.commit()

def db_close(conn):
    conn.close()

SCHEMA_SQLITE = '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        full_name TEXT, role TEXT DEFAULT 'staff',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, author TEXT, isbn TEXT, publisher TEXT, category TEXT,
        quantity INTEGER DEFAULT 0, buying_price REAL DEFAULT 0, selling_price REAL DEFAULT 0, notes TEXT,
        version INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, phone TEXT, email TEXT, address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL, customer_id INTEGER, customer_name TEXT,
        quantity_sold INTEGER NOT NULL, unit_price REAL NOT NULL,
        total_amount REAL NOT NULL, profit REAL NOT NULL,
        sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT NOT NULL, amount REAL NOT NULL, category TEXT,
        user_id INTEGER,
        expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS stock_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL, adjustment_type TEXT NOT NULL,
        quantity INTEGER NOT NULL, reason TEXT, user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, action TEXT NOT NULL,
        table_name TEXT NOT NULL, record_id INTEGER, details TEXT,
        ip_address TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
'''

SCHEMA_PG = '''
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, full_name TEXT,
        role TEXT DEFAULT 'staff', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY, title TEXT NOT NULL,
        author TEXT, isbn TEXT, publisher TEXT, category TEXT,
        quantity INTEGER DEFAULT 0, buying_price REAL DEFAULT 0, selling_price REAL DEFAULT 0, notes TEXT,
        version INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL,
        phone TEXT, email TEXT, address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id),
        customer_id INTEGER REFERENCES customers(id),
        customer_name TEXT,
        quantity_sold INTEGER NOT NULL, unit_price REAL NOT NULL,
        total_amount REAL NOT NULL, profit REAL NOT NULL,
        sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY, description TEXT NOT NULL,
        amount REAL NOT NULL, category TEXT, user_id INTEGER,
        expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stock_adjustments (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id),
        adjustment_type TEXT NOT NULL, quantity INTEGER NOT NULL,
        reason TEXT, user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY, user_id INTEGER, username TEXT,
        action TEXT NOT NULL, table_name TEXT NOT NULL, record_id INTEGER,
        details TEXT, ip_address TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
'''

MIGRATION_SQLITE = [
    "ALTER TABLE products ADD COLUMN version INTEGER DEFAULT 1",
    "ALTER TABLE sales ADD COLUMN customer_id INTEGER",
    "ALTER TABLE expenses ADD COLUMN user_id INTEGER",
]

MIGRATION_PG = [
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1",
    "ALTER TABLE sales ADD COLUMN IF NOT EXISTS customer_id INTEGER",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS user_id INTEGER",
]

def init_db():
    conn = get_db()
    try:
        if IS_PG:
            for stmt in SCHEMA_PG.split(';'):
                stmt = stmt.strip()
                if stmt:
                    query(conn, stmt)
            for stmt in MIGRATION_PG:
                try:
                    query(conn, stmt)
                except Exception:
                    pass
        else:
            conn.executescript(SCHEMA_SQLITE)
            for stmt in MIGRATION_SQLITE:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
            db_commit(conn)
    except Exception as e:
        print(f'[DB INIT ERROR] {e}', file=sys.stderr)
    db_close(conn)

def create_default_admin():
    conn = get_db()
    try:
        existing = query(conn, "SELECT id FROM users WHERE username = 'admin'").fetchone()
        if not existing:
            pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
            pw_hash = generate_password_hash(pw)
            query(conn, "INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
                  ('admin', pw_hash, 'Administrator', 'admin'))
            db_commit(conn)
            print(f'[INFO] Default admin created. Username: admin, Password: {pw}', file=sys.stderr)
    except Exception as e:
        print(f'[ADMIN INIT ERROR] {e}', file=sys.stderr)
    db_close(conn)

init_db()
create_default_admin()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access required', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if not session.get('user_id'):
        return None
    return {
        'id': session['user_id'],
        'username': session.get('username', ''),
        'role': session.get('role', 'staff'),
        'full_name': session.get('full_name', ''),
    }

def log_audit(conn, action, table_name, record_id=None, details=None):
    try:
        ip = request.remote_addr or 'unknown'
        user_id = session.get('user_id')
        username = session.get('username', 'system')
        query(conn, '''INSERT INTO audit_log (user_id, username, action, table_name, record_id, details, ip_address)
                        VALUES (?,?,?,?,?,?,?)''',
              (user_id, username, action, table_name, record_id, details, ip))
        db_commit(conn)
    except Exception:
        pass

def sanitize_input(value):
    if value is None:
        return ''
    return re.sub(r'<[^>]+>', '', str(value)).strip()

def paginate_query(conn, sql, params, page, per_page=PER_PAGE):
    count_sql = f"SELECT COUNT(*) as c FROM ({sql}) as sub"
    count_sql, count_params = q(count_sql, params)
    if IS_PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(count_sql, count_params or [])
        total = cur.fetchone()['c']
    else:
        total = conn.execute(count_sql, count_params or []).fetchone()['c']

    limit_sql = f"{sql} LIMIT ? OFFSET ?"
    all_params = list(params or []) + [per_page, (page - 1) * per_page]
    rows = query(conn, limit_sql, all_params).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return rows, total, total_pages

def export_csv(filename, headers, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    mem = io.BytesIO()
    mem.write(buf.getvalue().encode('utf-8-sig'))
    mem.seek(0)
    return send_file(mem, mimetype='text/csv',
                     download_name=filename, as_attachment=True)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        username = sanitize_input(request.form.get('username', ''))
        password = request.form.get('password', '')
        if not username or not password:
            flash('Username and password required', 'danger')
            return render_template('login.html')
        conn = get_db()
        user = query(conn, 'SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db_close(conn)
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name'] or user['username']
            session.permanent = True
            app.permanent_session_lifetime = timedelta(hours=12)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    total_products = query(conn, 'SELECT COUNT(*) as c FROM products').fetchone()['c']
    total_stock = query(conn, 'SELECT COALESCE(SUM(quantity),0) as s FROM products').fetchone()['s']
    total_invested = query(conn, 'SELECT COALESCE(SUM(buying_price * quantity),0) as t FROM products').fetchone()['t']
    total_sales_amount = query(conn, 'SELECT COALESCE(SUM(total_amount),0) as t FROM sales').fetchone()['t']
    total_profit = query(conn, 'SELECT COALESCE(SUM(profit),0) as t FROM sales').fetchone()['t']
    total_possible_profit = query(conn, 'SELECT COALESCE(SUM((selling_price - buying_price) * quantity),0) as t FROM products').fetchone()['t']
    total_customers = query(conn, 'SELECT COUNT(*) as c FROM customers').fetchone()['c']
    first = date.today().replace(day=1)
    next_month = first.replace(month=first.month % 12 + 1, year=first.year + (first.month // 12))
    monthly_profit = query(conn, 'SELECT COALESCE(SUM(profit),0) as t FROM sales WHERE sale_date >= ? AND sale_date < ?',
                           (first.isoformat(), next_month.isoformat())).fetchone()['t']
    monthly_expenses = query(conn, 'SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE expense_date >= ? AND expense_date < ?',
                              (first.isoformat(), next_month.isoformat())).fetchone()['t']
    low_stock = query(conn, 'SELECT * FROM products WHERE quantity <= 5 ORDER BY quantity LIMIT 10').fetchall()
    recent_sales = query(conn, '''
        SELECT s.*, p.title, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_display
        FROM sales s JOIN products p ON s.product_id = p.id
        LEFT JOIN customers c ON s.customer_id = c.id
        ORDER BY s.sale_date DESC LIMIT 10
    ''').fetchall()
    recent_sales_count = query(conn, 'SELECT COUNT(*) as c FROM sales').fetchone()['c']
    category_breakdown = query(conn, '''
        SELECT COALESCE(category,'Uncategorized') as category,
               COUNT(*) as count, COALESCE(SUM(quantity),0) as stock,
               COALESCE(SUM(buying_price * quantity),0) as value
        FROM products GROUP BY category ORDER BY value DESC
    ''').fetchall()
    top_products = query(conn, '''
        SELECT p.title, SUM(s.quantity_sold) as total_sold, COALESCE(SUM(s.total_amount),0) as revenue
        FROM sales s JOIN products p ON s.product_id = p.id
        GROUP BY p.id ORDER BY revenue DESC LIMIT 5
    ''').fetchall()
    db_close(conn)
    return render_template('dashboard.html', total_products=total_products, total_stock=total_stock,
                           total_invested=total_invested, total_sales_amount=total_sales_amount,
                           total_profit=total_profit, total_possible_profit=total_possible_profit,
                           monthly_profit=monthly_profit, monthly_expenses=monthly_expenses,
                           low_stock=low_stock, recent_sales=recent_sales, recent_sales_count=recent_sales_count,
                           category_breakdown=category_breakdown, top_products=top_products,
                           total_customers=total_customers)

@app.route('/products')
@login_required
def products():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    rows, total, total_pages = paginate_query(conn,
        'SELECT *, (selling_price - buying_price) as profit_margin FROM products ORDER BY created_at DESC', [], page)
    db_close(conn)
    return render_template('products.html', products=rows, page=page, total_pages=total_pages, total=total)

@app.route('/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        title = sanitize_input(request.form.get('title', ''))
        if not title:
            flash('Product title is required', 'danger')
            return redirect(url_for('add_product'))
        author = sanitize_input(request.form.get('author', ''))
        isbn = sanitize_input(request.form.get('isbn', ''))
        publisher = sanitize_input(request.form.get('publisher', ''))
        category = sanitize_input(request.form.get('category', ''))
        try:
            quantity = max(0, int(request.form.get('quantity', 0)))
            buying_price = max(0, float(request.form.get('buying_price', 0)))
            selling_price = max(0, float(request.form.get('selling_price', 0)))
        except (ValueError, TypeError):
            flash('Invalid number values', 'danger')
            return redirect(url_for('add_product'))
        notes = sanitize_input(request.form.get('notes', ''))
        conn = get_db()
        try:
            query(conn, 'INSERT INTO products (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes) VALUES (?,?,?,?,?,?,?,?,?)',
                  (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes))
            db_commit(conn)
            log_audit(conn, 'create', 'products', None, f'Added: {title}')
            flash('Product added successfully', 'success')
        except Exception as e:
            flash(f'Error adding product: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('products'))
    return render_template('add_product.html')

@app.route('/products/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_product(id):
    conn = get_db()
    product = query(conn, 'SELECT * FROM products WHERE id = ?', (id,)).fetchone()
    if not product:
        flash('Product not found', 'danger')
        db_close(conn)
        return redirect(url_for('products'))
    if request.method == 'POST':
        title = sanitize_input(request.form.get('title', ''))
        if not title:
            flash('Product title is required', 'danger')
            db_close(conn)
            return redirect(url_for('edit_product', id=id))
        author = sanitize_input(request.form.get('author', ''))
        isbn = sanitize_input(request.form.get('isbn', ''))
        publisher = sanitize_input(request.form.get('publisher', ''))
        category = sanitize_input(request.form.get('category', ''))
        try:
            quantity = max(0, int(request.form.get('quantity', 0)))
            buying_price = max(0, float(request.form.get('buying_price', 0)))
            selling_price = max(0, float(request.form.get('selling_price', 0)))
        except (ValueError, TypeError):
            flash('Invalid number values', 'danger')
            db_close(conn)
            return redirect(url_for('edit_product', id=id))
        notes = sanitize_input(request.form.get('notes', ''))
        old_version = product['version'] if 'version' in product.keys() else 1
        try:
            result = query(conn, '''UPDATE products SET title=?, author=?, isbn=?, publisher=?, category=?,
                            quantity=?, buying_price=?, selling_price=?, notes=?,
                            version=version+1, updated_at=CURRENT_TIMESTAMP
                            WHERE id=? AND version=?''',
                  (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes, id, old_version))
            db_commit(conn)
            if IS_PG:
                if result.rowcount == 0:
                    flash('Product was modified by another user. Please refresh and try again.', 'danger')
                    db_close(conn)
                    return redirect(url_for('products'))
            log_audit(conn, 'update', 'products', id, f'Updated: {title}')
            flash('Product updated successfully', 'success')
        except Exception as e:
            flash(f'Error updating product: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('products'))
    db_close(conn)
    return render_template('edit_product.html', product=product)

@app.route('/products/delete/<int:id>', methods=['POST'])
@login_required
def delete_product(id):
    conn = get_db()
    try:
        product = query(conn, 'SELECT title FROM products WHERE id = ?', (id,)).fetchone()
        query(conn, 'DELETE FROM sales WHERE product_id = ?', (id,))
        query(conn, 'DELETE FROM stock_adjustments WHERE product_id = ?', (id,))
        query(conn, 'DELETE FROM products WHERE id = ?', (id,))
        db_commit(conn)
        log_audit(conn, 'delete', 'products', id, f'Deleted: {product["title"] if product else id}')
        flash('Product deleted', 'success')
    except Exception as e:
        flash(f'Error deleting product: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('products'))

@app.route('/products/adjust/<int:id>', methods=['POST'])
@login_required
def adjust_stock(id):
    conn = get_db()
    try:
        product = query(conn, 'SELECT * FROM products WHERE id = ?', (id,)).fetchone()
        if not product:
            flash('Product not found', 'danger')
            db_close(conn)
            return redirect(url_for('products'))
        adj_type = sanitize_input(request.form.get('adjustment_type', ''))
        try:
            quantity = int(request.form.get('quantity', 0))
        except (ValueError, TypeError):
            flash('Invalid quantity', 'danger')
            db_close(conn)
            return redirect(url_for('products'))
        reason = sanitize_input(request.form.get('reason', ''))
        if adj_type not in ('damaged', 'stolen', 'returned', 'correction', 'restock'):
            flash('Invalid adjustment type', 'danger')
            db_close(conn)
            return redirect(url_for('products'))
        if quantity <= 0:
            flash('Quantity must be positive', 'danger')
            db_close(conn)
            return redirect(url_for('products'))
        if adj_type == 'correction':
            new_qty = quantity
        elif adj_type in ('damaged', 'stolen'):
            new_qty = product['quantity'] - quantity
            if new_qty < 0:
                flash(f'Cannot remove {quantity} units. Only {product["quantity"]} in stock.', 'danger')
                db_close(conn)
                return redirect(url_for('products'))
        elif adj_type == 'returned':
            new_qty = product['quantity'] + quantity
        else:
            new_qty = product['quantity'] + quantity
        query(conn, 'INSERT INTO stock_adjustments (product_id, adjustment_type, quantity, reason, user_id) VALUES (?,?,?,?,?)',
              (id, adj_type, quantity, reason, session.get('user_id')))
        query(conn, 'UPDATE products SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (new_qty, id))
        db_commit(conn)
        log_audit(conn, 'adjust', 'products', id, f'{adj_type}: {quantity} units of {product["title"]} (reason: {reason})')
        flash(f'Stock adjusted: {adj_type} {quantity} units', 'success')
    except Exception as e:
        flash(f'Error adjusting stock: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('products'))

@app.route('/sales')
@login_required
def sales():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    sql = '''SELECT s.*, p.title, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_display
             FROM sales s JOIN products p ON s.product_id = p.id
             LEFT JOIN customers c ON s.customer_id = c.id
             ORDER BY s.sale_date DESC'''
    rows, total, total_pages = paginate_query(conn, sql, [], page)
    db_close(conn)
    return render_template('sales.html', sales=rows, page=page, total_pages=total_pages, total=total)

@app.route('/sales/add', methods=['GET', 'POST'])
@login_required
def add_sale():
    conn = get_db()
    if request.method == 'POST':
        try:
            product_id = int(request.form['product_id'])
            quantity_sold = int(request.form['quantity'])
            unit_price = float(request.form['unit_price'])
        except (ValueError, TypeError, KeyError):
            flash('Invalid input values', 'danger')
            db_close(conn)
            return redirect(url_for('add_sale'))
        customer_id = request.form.get('customer_id')
        customer_id = int(customer_id) if customer_id else None
        customer_name = sanitize_input(request.form.get('customer_name', ''))
        product = query(conn, 'SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
        if not product:
            flash('Product not found', 'danger')
            db_close(conn)
            return redirect(url_for('add_sale'))
        if product['quantity'] < quantity_sold:
            flash(f'Not enough stock! Available: {product["quantity"]}', 'danger')
            db_close(conn)
            return redirect(url_for('add_sale'))
        if unit_price <= 0:
            flash('Unit price must be positive', 'danger')
            db_close(conn)
            return redirect(url_for('add_sale'))
        total_amount = unit_price * quantity_sold
        profit = (unit_price - product['buying_price']) * quantity_sold
        try:
            query(conn, '''INSERT INTO sales (product_id, customer_id, customer_name, quantity_sold, unit_price, total_amount, profit)
                           VALUES (?,?,?,?,?,?,?)''',
                  (product_id, customer_id, customer_name, quantity_sold, unit_price, total_amount, profit))
            query(conn, 'UPDATE products SET quantity = quantity - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                  (quantity_sold, product_id))
            db_commit(conn)
            log_audit(conn, 'create', 'sales', None, f'Sale: {quantity_sold}x {product["title"]} for {CURRENCY} {total_amount:,.0f}')
            flash('Sale recorded successfully', 'success')
        except Exception as e:
            flash(f'Error recording sale: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('sales'))
    products = query(conn, 'SELECT * FROM products WHERE quantity > 0 ORDER BY title').fetchall()
    customers = query(conn, 'SELECT * FROM customers ORDER BY name').fetchall()
    db_close(conn)
    return render_template('add_sale.html', products=products, customers=customers)

@app.route('/sales/delete/<int:id>', methods=['POST'])
@login_required
def delete_sale(id):
    conn = get_db()
    try:
        sale = query(conn, 'SELECT * FROM sales WHERE id = ?', (id,)).fetchone()
        if sale:
            query(conn, 'UPDATE products SET quantity = quantity + ? WHERE id = ?',
                  (sale['quantity_sold'], sale['product_id']))
            query(conn, 'DELETE FROM sales WHERE id = ?', (id,))
            db_commit(conn)
            log_audit(conn, 'delete', 'sales', id, f'Deleted sale #{id}')
        flash('Sale deleted', 'success')
    except Exception as e:
        flash(f'Error deleting sale: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('sales'))

@app.route('/expenses')
@login_required
def expenses():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    rows, total, total_pages = paginate_query(conn,
        'SELECT * FROM expenses ORDER BY expense_date DESC', [], page)
    total_all = query(conn, 'SELECT COALESCE(SUM(amount),0) as t FROM expenses').fetchone()['t']
    db_close(conn)
    return render_template('expenses.html', expenses=rows, total=total_all,
                           page=page, total_pages=total_pages, total_count=total)

@app.route('/expenses/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        description = sanitize_input(request.form.get('description', ''))
        if not description:
            flash('Description is required', 'danger')
            return redirect(url_for('add_expense'))
        try:
            amount = float(request.form.get('amount', 0))
        except (ValueError, TypeError):
            flash('Invalid amount', 'danger')
            return redirect(url_for('add_expense'))
        if amount <= 0:
            flash('Amount must be positive', 'danger')
            return redirect(url_for('add_expense'))
        category = sanitize_input(request.form.get('category', ''))
        conn = get_db()
        try:
            query(conn, 'INSERT INTO expenses (description, amount, category, user_id) VALUES (?,?,?,?)',
                  (description, amount, category, session.get('user_id')))
            db_commit(conn)
            log_audit(conn, 'create', 'expenses', None, f'Expense: {description} - {CURRENCY} {amount:,.0f}')
            flash('Expense added successfully', 'success')
        except Exception as e:
            flash(f'Error adding expense: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('expenses'))
    return render_template('add_expense.html')

@app.route('/expenses/delete/<int:id>', methods=['POST'])
@login_required
def delete_expense(id):
    conn = get_db()
    try:
        query(conn, 'DELETE FROM expenses WHERE id = ?', (id,))
        db_commit(conn)
        log_audit(conn, 'delete', 'expenses', id, f'Deleted expense #{id}')
        flash('Expense deleted', 'success')
    except Exception as e:
        flash(f'Error deleting expense: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('expenses'))

@app.route('/customers')
@login_required
def customers():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    rows, total, total_pages = paginate_query(conn,
        'SELECT * FROM customers ORDER BY name', [], page)
    db_close(conn)
    return render_template('customers/list.html', customers=rows,
                           page=page, total_pages=total_pages, total=total)

@app.route('/customers/add', methods=['GET', 'POST'])
@login_required
def add_customer():
    if request.method == 'POST':
        name = sanitize_input(request.form.get('name', ''))
        if not name:
            flash('Customer name is required', 'danger')
            return redirect(url_for('add_customer'))
        phone = sanitize_input(request.form.get('phone', ''))
        email = sanitize_input(request.form.get('email', ''))
        address = sanitize_input(request.form.get('address', ''))
        conn = get_db()
        try:
            query(conn, 'INSERT INTO customers (name, phone, email, address) VALUES (?,?,?,?)',
                  (name, phone, email, address))
            db_commit(conn)
            log_audit(conn, 'create', 'customers', None, f'Added customer: {name}')
            flash('Customer added successfully', 'success')
        except Exception as e:
            flash(f'Error adding customer: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('customers'))
    return render_template('customers/form.html', customer=None)

@app.route('/customers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_customer(id):
    conn = get_db()
    customer = query(conn, 'SELECT * FROM customers WHERE id = ?', (id,)).fetchone()
    if not customer:
        flash('Customer not found', 'danger')
        db_close(conn)
        return redirect(url_for('customers'))
    if request.method == 'POST':
        name = sanitize_input(request.form.get('name', ''))
        if not name:
            flash('Customer name is required', 'danger')
            db_close(conn)
            return redirect(url_for('edit_customer', id=id))
        phone = sanitize_input(request.form.get('phone', ''))
        email = sanitize_input(request.form.get('email', ''))
        address = sanitize_input(request.form.get('address', ''))
        try:
            query(conn, 'UPDATE customers SET name=?, phone=?, email=?, address=? WHERE id=?',
                  (name, phone, email, address, id))
            db_commit(conn)
            log_audit(conn, 'update', 'customers', id, f'Updated customer: {name}')
            flash('Customer updated successfully', 'success')
        except Exception as e:
            flash(f'Error updating customer: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('customers'))
    db_close(conn)
    return render_template('customers/form.html', customer=customer)

@app.route('/customers/delete/<int:id>', methods=['POST'])
@login_required
def delete_customer(id):
    conn = get_db()
    try:
        query(conn, 'UPDATE sales SET customer_id = NULL WHERE customer_id = ?', (id,))
        query(conn, 'DELETE FROM customers WHERE id = ?', (id,))
        db_commit(conn)
        log_audit(conn, 'delete', 'customers', id, f'Deleted customer #{id}')
        flash('Customer deleted', 'success')
    except Exception as e:
        flash(f'Error deleting customer: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('customers'))

@app.route('/customers/<int:id>')
@login_required
def customer_detail(id):
    conn = get_db()
    customer = query(conn, 'SELECT * FROM customers WHERE id = ?', (id,)).fetchone()
    if not customer:
        flash('Customer not found', 'danger')
        db_close(conn)
        return redirect(url_for('customers'))
    sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.customer_id = ? ORDER BY s.sale_date DESC
    ''', (id,)).fetchall()
    total_spent = sum(s['total_amount'] for s in sales)
    total_items = sum(s['quantity_sold'] for s in sales)
    db_close(conn)
    return render_template('customers/detail.html', customer=customer, sales=sales,
                           total_spent=total_spent, total_items=total_items)

@app.route('/stock/adjustments')
@login_required
def stock_adjustments():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    sql = '''SELECT sa.*, p.title, u.username FROM stock_adjustments sa
             JOIN products p ON sa.product_id = p.id
             LEFT JOIN users u ON sa.user_id = u.id
             ORDER BY sa.created_at DESC'''
    rows, total, total_pages = paginate_query(conn, sql, [], page)
    db_close(conn)
    return render_template('stock/history.html', adjustments=rows,
                           page=page, total_pages=total_pages, total=total)

@app.route('/invoices/<int:sale_id>')
@login_required
def invoice(sale_id):
    conn = get_db()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ?', (sale_id,)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ?', (sale['product_id'],)).fetchone()
    customer = None
    if sale['customer_id']:
        customer = query(conn, 'SELECT * FROM customers WHERE id = ?', (sale['customer_id'],)).fetchone()
    db_close(conn)
    sale_date = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
    return render_template('invoice.html', sale=sale, product=product, sale_date=sale_date, customer=customer)

@app.route('/invoices/<int:sale_id>/pdf')
@login_required
def invoice_pdf(sale_id):
    from utils.pdf import generate_invoice_pdf
    conn = get_db()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ?', (sale_id,)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ?', (sale['product_id'],)).fetchone()
    db_close(conn)
    sale_dict = dict(sale)
    if sale_dict.get('customer_id') and not sale_dict.get('customer_name'):
        sale_dict['customer_name'] = f'Customer #{sale_dict["customer_id"]}'
    buf = generate_invoice_pdf(COMPANY_NAME, CURRENCY, sale_dict, dict(product))
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'invoice_{sale_id}.pdf', as_attachment=True)

@app.route('/receipts/<int:sale_id>')
@login_required
def receipt(sale_id):
    conn = get_db()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ?', (sale_id,)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ?', (sale['product_id'],)).fetchone()
    customer = None
    if sale['customer_id']:
        customer = query(conn, 'SELECT * FROM customers WHERE id = ?', (sale['customer_id'],)).fetchone()
    db_close(conn)
    sale_date = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
    return render_template('receipt.html', sale=sale, product=product, sale_date=sale_date, customer=customer)

@app.route('/receipts/<int:sale_id>/pdf')
@login_required
def receipt_pdf(sale_id):
    from utils.pdf import generate_receipt_pdf
    conn = get_db()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ?', (sale_id,)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ?', (sale['product_id'],)).fetchone()
    db_close(conn)
    sale_dict = dict(sale)
    if sale_dict.get('customer_id') and not sale_dict.get('customer_name'):
        sale_dict['customer_name'] = f'Customer #{sale_dict["customer_id"]}'
    buf = generate_receipt_pdf(COMPANY_NAME, CURRENCY, sale_dict, dict(product))
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'receipt_{sale_id}.pdf', as_attachment=True)

@app.route('/reports/stock')
@login_required
def stock_report():
    conn = get_db()
    prods = query(conn, 'SELECT * FROM products ORDER BY category, title').fetchall()
    db_close(conn)
    total_value = sum(p['buying_price'] * p['quantity'] for p in prods)
    potential_revenue = sum(p['selling_price'] * p['quantity'] for p in prods)
    totals = {
        'total_products': len(prods),
        'total_units': sum(p['quantity'] for p in prods),
        'total_value': total_value,
        'potential_revenue': potential_revenue,
        'potential_profit': potential_revenue - total_value,
    }
    return render_template('reports/stock.html', products=prods, totals=totals)

@app.route('/reports/stock/pdf')
@login_required
def stock_report_pdf():
    from utils.pdf import generate_stock_report_pdf
    conn = get_db()
    prods = query(conn, 'SELECT * FROM products ORDER BY category, title').fetchall()
    db_close(conn)
    total_value = sum(p['buying_price'] * p['quantity'] for p in prods)
    potential_revenue = sum(p['selling_price'] * p['quantity'] for p in prods)
    totals = {
        'total_products': len(prods),
        'total_units': sum(p['quantity'] for p in prods),
        'total_value': total_value,
        'potential_revenue': potential_revenue,
        'potential_profit': potential_revenue - total_value,
    }
    buf = generate_stock_report_pdf(COMPANY_NAME, CURRENCY, [dict(p) for p in prods], totals)
    return send_file(buf, mimetype='application/pdf',
                     download_name='stock_valuation_report.pdf', as_attachment=True)

@app.route('/reports/stock/csv')
@login_required
def stock_report_csv():
    conn = get_db()
    prods = query(conn, 'SELECT * FROM products ORDER BY category, title').fetchall()
    db_close(conn)
    headers = ['Product', 'Category', 'Qty', 'Buy Price', 'Sell Price', 'Stock Value', 'Retail Value']
    rows = [[p['title'], p['category'] or '', p['quantity'],
             p['buying_price'], p['selling_price'],
             p['buying_price'] * p['quantity'], p['selling_price'] * p['quantity']] for p in prods]
    return export_csv('stock_valuation.csv', headers, rows)

@app.route('/reports/sales')
@login_required
def sales_report():
    today = date.today()
    date_from = request.args.get('date_from', today.replace(day=1).isoformat())
    date_to = request.args.get('date_to', today.isoformat())
    conn = get_db()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.sale_date >= ? AND s.sale_date <= ?
        ORDER BY s.sale_date DESC
    ''', (date_from, date_to + ' 23:59:59')).fetchall()
    db_close(conn)
    total_amount = sum(s['total_amount'] for s in all_sales)
    total_profit = sum(s['profit'] for s in all_sales)
    total_qty = sum(s['quantity_sold'] for s in all_sales)
    totals = {
        'total_count': len(all_sales), 'total_amount': total_amount,
        'total_profit': total_profit, 'total_qty': total_qty,
    }
    return render_template('reports/sales.html', sales=all_sales, totals=totals,
                           date_from=date_from, date_to=date_to)

@app.route('/reports/sales/pdf')
@login_required
def sales_report_pdf():
    from utils.pdf import generate_sales_report_pdf
    today = date.today()
    date_from = request.args.get('date_from', today.replace(day=1).isoformat())
    date_to = request.args.get('date_to', today.isoformat())
    conn = get_db()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.sale_date >= ? AND s.sale_date <= ?
        ORDER BY s.sale_date DESC
    ''', (date_from, date_to + ' 23:59:59')).fetchall()
    db_close(conn)
    total_amount = sum(s['total_amount'] for s in all_sales)
    total_profit = sum(s['profit'] for s in all_sales)
    total_qty = sum(s['quantity_sold'] for s in all_sales)
    totals = {'total_count': len(all_sales), 'total_amount': total_amount,
              'total_profit': total_profit, 'total_qty': total_qty}
    buf = generate_sales_report_pdf(COMPANY_NAME, CURRENCY, [dict(s) for s in all_sales],
                                    totals, date_from, date_to)
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'sales_report_{date_from}_to_{date_to}.pdf', as_attachment=True)

@app.route('/reports/sales/csv')
@login_required
def sales_report_csv():
    today = date.today()
    date_from = request.args.get('date_from', today.replace(day=1).isoformat())
    date_to = request.args.get('date_to', today.isoformat())
    conn = get_db()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.sale_date >= ? AND s.sale_date <= ?
        ORDER BY s.sale_date DESC
    ''', (date_from, date_to + ' 23:59:59')).fetchall()
    db_close(conn)
    headers = ['Date', 'Product', 'Customer', 'Qty', 'Unit Price', 'Total', 'Profit']
    rows = [[(s['sale_date'][:10] if isinstance(s['sale_date'], str) else s['sale_date'].strftime('%Y-%m-%d')),
             s['title'], s['customer_name'] or '-', s['quantity_sold'],
             s['unit_price'], s['total_amount'], s['profit']] for s in all_sales]
    return export_csv(f'sales_{date_from}_to_{date_to}.csv', headers, rows)

@app.route('/reports/profit-loss')
@login_required
def pnl_report():
    today = date.today()
    date_from = request.args.get('date_from', today.replace(day=1).isoformat())
    date_to = request.args.get('date_to', today.isoformat())
    conn = get_db()
    sales_data = query(conn, '''
        SELECT COALESCE(SUM(total_amount), 0) as revenue,
               COALESCE(SUM(profit), 0) as gross_profit
        FROM sales WHERE sale_date >= ? AND sale_date <= ?
    ''', (date_from, date_to + ' 23:59:59')).fetchone()
    revenue = sales_data['revenue']
    gross_profit = sales_data['gross_profit']
    cogs = revenue - gross_profit
    expenses = query(conn, '''
        SELECT COALESCE(category, 'Uncategorized') as category, SUM(amount) as amount
        FROM expenses WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY category ORDER BY amount DESC
    ''', (date_from, date_to + ' 23:59:59')).fetchall()
    total_expenses = sum(e['amount'] for e in expenses)
    db_close(conn)
    data = {
        'revenue': revenue, 'cogs': cogs, 'gross_profit': gross_profit,
        'total_expenses': total_expenses, 'net_profit': gross_profit - total_expenses,
        'expense_breakdown': expenses,
    }
    return render_template('reports/profit_loss.html', data=data,
                           date_from=date_from, date_to=date_to)

@app.route('/reports/profit-loss/pdf')
@login_required
def pnl_report_pdf():
    from utils.pdf import generate_pnl_report_pdf
    today = date.today()
    date_from = request.args.get('date_from', today.replace(day=1).isoformat())
    date_to = request.args.get('date_to', today.isoformat())
    conn = get_db()
    sales_data = query(conn, '''
        SELECT COALESCE(SUM(total_amount), 0) as revenue,
               COALESCE(SUM(profit), 0) as gross_profit
        FROM sales WHERE sale_date >= ? AND sale_date <= ?
    ''', (date_from, date_to + ' 23:59:59')).fetchone()
    revenue = sales_data['revenue']
    gross_profit = sales_data['gross_profit']
    cogs = revenue - gross_profit
    expenses = query(conn, '''
        SELECT COALESCE(category, 'Uncategorized') as category, SUM(amount) as amount
        FROM expenses WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY category ORDER BY amount DESC
    ''', (date_from, date_to + ' 23:59:59')).fetchall()
    total_expenses = sum(e['amount'] for e in expenses)
    db_close(conn)
    data = {
        'revenue': revenue, 'cogs': cogs, 'gross_profit': gross_profit,
        'total_expenses': total_expenses, 'net_profit': gross_profit - total_expenses,
        'expense_breakdown': [dict(e) for e in expenses],
    }
    buf = generate_pnl_report_pdf(COMPANY_NAME, CURRENCY, data, date_from, date_to)
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'pnl_report_{date_from}_to_{date_to}.pdf', as_attachment=True)

@app.route('/reports/profit-loss/csv')
@login_required
def pnl_report_csv():
    today = date.today()
    date_from = request.args.get('date_from', today.replace(day=1).isoformat())
    date_to = request.args.get('date_to', today.isoformat())
    conn = get_db()
    sales_data = query(conn, '''
        SELECT COALESCE(SUM(total_amount), 0) as revenue,
               COALESCE(SUM(profit), 0) as gross_profit
        FROM sales WHERE sale_date >= ? AND sale_date <= ?
    ''', (date_from, date_to + ' 23:59:59')).fetchone()
    revenue = sales_data['revenue']
    gross_profit = sales_data['gross_profit']
    cogs = revenue - gross_profit
    expenses = query(conn, '''
        SELECT COALESCE(category, 'Uncategorized') as category, SUM(amount) as amount
        FROM expenses WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY category ORDER BY amount DESC
    ''', (date_from, date_to + ' 23:59:59')).fetchall()
    total_expenses = sum(e['amount'] for e in expenses)
    db_close(conn)
    headers = ['Item', 'Amount']
    rows = [['Revenue', revenue], ['COGS', cogs], ['Gross Profit', gross_profit]]
    for e in expenses:
        rows.append([f'Expense: {e["category"]}', e['amount']])
    rows.append(['Total Expenses', total_expenses])
    rows.append(['NET PROFIT', gross_profit - total_expenses])
    return export_csv(f'pnl_{date_from}_to_{date_to}.csv', headers, rows)

@app.route('/audit')
@admin_required
def audit_log():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    sql = 'SELECT * FROM audit_log ORDER BY created_at DESC'
    rows, total, total_pages = paginate_query(conn, sql, [], page)
    db_close(conn)
    return render_template('audit/log.html', logs=rows,
                           page=page, total_pages=total_pages, total=total)

@app.route('/admin/users')
@admin_required
def manage_users():
    conn = get_db()
    users = query(conn, 'SELECT * FROM users ORDER BY created_at').fetchall()
    db_close(conn)
    return render_template('auth/manage_users.html', users=users)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@admin_required
def add_user():
    if request.method == 'POST':
        username = sanitize_input(request.form.get('username', ''))
        password = request.form.get('password', '')
        full_name = sanitize_input(request.form.get('full_name', ''))
        role = request.form.get('role', 'staff')
        if not username or not password:
            flash('Username and password are required', 'danger')
            return redirect(url_for('add_user'))
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'danger')
            return redirect(url_for('add_user'))
        if role not in ('admin', 'manager', 'staff'):
            role = 'staff'
        conn = get_db()
        try:
            existing = query(conn, 'SELECT id FROM users WHERE username = ?', (username,)).fetchone()
            if existing:
                flash('Username already exists', 'danger')
                db_close(conn)
                return redirect(url_for('add_user'))
            pw_hash = generate_password_hash(password)
            query(conn, 'INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)',
                  (username, pw_hash, full_name, role))
            db_commit(conn)
            log_audit(conn, 'create', 'users', None, f'Created user: {username} (role: {role})')
            flash(f'User {username} created successfully', 'success')
        except Exception as e:
            flash(f'Error creating user: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('manage_users'))
    return render_template('auth/form.html', user=None)

@app.route('/admin/users/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_user(id):
    conn = get_db()
    user = query(conn, 'SELECT * FROM users WHERE id = ?', (id,)).fetchone()
    if not user:
        flash('User not found', 'danger')
        db_close(conn)
        return redirect(url_for('manage_users'))
    if request.method == 'POST':
        full_name = sanitize_input(request.form.get('full_name', ''))
        role = request.form.get('role', 'staff')
        password = request.form.get('password', '')
        if role not in ('admin', 'manager', 'staff'):
            role = 'staff'
        try:
            if password:
                if len(password) < 6:
                    flash('Password must be at least 6 characters', 'danger')
                    db_close(conn)
                    return redirect(url_for('edit_user', id=id))
                pw_hash = generate_password_hash(password)
                query(conn, 'UPDATE users SET full_name=?, role=?, password_hash=? WHERE id=?',
                      (full_name, role, pw_hash, id))
            else:
                query(conn, 'UPDATE users SET full_name=?, role=? WHERE id=?',
                      (full_name, role, id))
            db_commit(conn)
            log_audit(conn, 'update', 'users', id, f'Updated user: {user["username"]}')
            flash('User updated successfully', 'success')
        except Exception as e:
            flash(f'Error updating user: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('manage_users'))
    db_close(conn)
    return render_template('auth/form.html', user=user)

@app.route('/admin/users/delete/<int:id>', methods=['POST'])
@admin_required
def delete_user(id):
    conn = get_db()
    try:
        user = query(conn, 'SELECT * FROM users WHERE id = ?', (id,)).fetchone()
        if user and user['username'] == 'admin':
            flash('Cannot delete the admin account', 'danger')
            db_close(conn)
            return redirect(url_for('manage_users'))
        query(conn, 'DELETE FROM users WHERE id = ?', (id,))
        db_commit(conn)
        log_audit(conn, 'delete', 'users', id, f'Deleted user: {user["username"] if user else id}')
        flash('User deleted', 'success')
    except Exception as e:
        flash(f'Error deleting user: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('manage_users'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=not IS_PG)
