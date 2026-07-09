import os, sys
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session

DATABASE_URL = os.environ.get('DATABASE_URL')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())
PASSWORD = os.environ.get('APP_PASSWORD', 'wans123')
COMPANY_NAME = os.environ.get('COMPANY_NAME', 'WANS COLLECTION')
CURRENCY = os.environ.get('CURRENCY', 'UGX')
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inventory.db'))

IS_PG = bool(DATABASE_URL)

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
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, author TEXT, isbn TEXT, publisher TEXT, category TEXT,
        quantity INTEGER DEFAULT 0, buying_price REAL DEFAULT 0, selling_price REAL DEFAULT 0, notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL, quantity_sold INTEGER NOT NULL, unit_price REAL NOT NULL,
        total_amount REAL NOT NULL, profit REAL NOT NULL, customer_name TEXT,
        sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT NOT NULL, amount REAL NOT NULL, category TEXT,
        expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
'''

SCHEMA_PG = '''
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL, author TEXT, isbn TEXT, publisher TEXT, category TEXT,
        quantity INTEGER DEFAULT 0, buying_price REAL DEFAULT 0, selling_price REAL DEFAULT 0, notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity_sold INTEGER NOT NULL, unit_price REAL NOT NULL,
        total_amount REAL NOT NULL, profit REAL NOT NULL, customer_name TEXT,
        sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY,
        description TEXT NOT NULL, amount REAL NOT NULL, category TEXT,
        expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
'''

def init_db():
    conn = get_db()
    try:
        if IS_PG:
            for stmt in SCHEMA_PG.split(';'):
                stmt = stmt.strip()
                if stmt:
                    query(conn, stmt)
        else:
            conn.executescript(SCHEMA_SQLITE)
            db_commit(conn)
    except Exception as e:
        print(f'[DB INIT ERROR] {e}', file=sys.stderr)
    db_close(conn)

init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['password'] == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        flash('Wrong password', 'danger')
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
    from datetime import date
    first = date.today().replace(day=1)
    next_month = first.replace(month=first.month % 12 + 1, year=first.year + (first.month // 12))
    monthly_profit = query(conn, 'SELECT COALESCE(SUM(profit),0) as t FROM sales WHERE sale_date >= ? AND sale_date < ?',
                           (first.isoformat(), next_month.isoformat())).fetchone()['t']
    monthly_expenses = query(conn, 'SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE expense_date >= ? AND expense_date < ?',
                              (first.isoformat(), next_month.isoformat())).fetchone()['t']
    low_stock = query(conn, 'SELECT * FROM products WHERE quantity <= 5 ORDER BY quantity LIMIT 10').fetchall()
    recent_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
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
                           category_breakdown=category_breakdown, top_products=top_products)

@app.route('/products')
@login_required
def products():
    conn = get_db()
    prods = query(conn, 'SELECT *, (selling_price - buying_price) as profit_margin FROM products ORDER BY created_at DESC').fetchall()
    db_close(conn)
    return render_template('products.html', products=prods)

@app.route('/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        title = request.form['title']
        author = request.form.get('author', '')
        isbn = request.form.get('isbn', '')
        publisher = request.form.get('publisher', '')
        category = request.form.get('category', '')
        quantity = int(request.form.get('quantity', 0))
        buying_price = float(request.form.get('buying_price', 0))
        selling_price = float(request.form.get('selling_price', 0))
        notes = request.form.get('notes', '')
        conn = get_db()
        query(conn, 'INSERT INTO products (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes) VALUES (?,?,?,?,?,?,?,?,?)',
              (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes))
        db_commit(conn)
        db_close(conn)
        flash('Product added successfully', 'success')
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
        title = request.form['title']
        author = request.form.get('author', '')
        isbn = request.form.get('isbn', '')
        publisher = request.form.get('publisher', '')
        category = request.form.get('category', '')
        quantity = int(request.form.get('quantity', 0))
        buying_price = float(request.form.get('buying_price', 0))
        selling_price = float(request.form.get('selling_price', 0))
        notes = request.form.get('notes', '')
        query(conn, '''UPDATE products SET title=?, author=?, isbn=?, publisher=?, category=?,
                        quantity=?, buying_price=?, selling_price=?, notes=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?''',
              (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes, id))
        db_commit(conn)
        db_close(conn)
        flash('Product updated successfully', 'success')
        return redirect(url_for('products'))
    db_close(conn)
    return render_template('edit_product.html', product=product)

@app.route('/products/delete/<int:id>', methods=['POST'])
@login_required
def delete_product(id):
    conn = get_db()
    query(conn, 'DELETE FROM products WHERE id = ?', (id,))
    query(conn, 'DELETE FROM sales WHERE product_id = ?', (id,))
    db_commit(conn)
    db_close(conn)
    flash('Product deleted', 'success')
    return redirect(url_for('products'))

@app.route('/sales')
@login_required
def sales():
    conn = get_db()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        ORDER BY s.sale_date DESC
    ''').fetchall()
    db_close(conn)
    return render_template('sales.html', sales=all_sales)

@app.route('/sales/add', methods=['GET', 'POST'])
@login_required
def add_sale():
    conn = get_db()
    if request.method == 'POST':
        product_id = int(request.form['product_id'])
        quantity_sold = int(request.form['quantity'])
        unit_price = float(request.form['unit_price'])
        customer_name = request.form.get('customer_name', '')
        product = query(conn, 'SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
        if not product:
            flash('Product not found', 'danger')
            db_close(conn)
            return redirect(url_for('add_sale'))
        if product['quantity'] < quantity_sold:
            flash(f'Not enough stock! Available: {product["quantity"]}', 'danger')
            db_close(conn)
            return redirect(url_for('add_sale'))
        total_amount = unit_price * quantity_sold
        profit = (unit_price - product['buying_price']) * quantity_sold
        query(conn, 'INSERT INTO sales (product_id, quantity_sold, unit_price, total_amount, profit, customer_name) VALUES (?,?,?,?,?,?)',
              (product_id, quantity_sold, unit_price, total_amount, profit, customer_name))
        query(conn, 'UPDATE products SET quantity = quantity - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
              (quantity_sold, product_id))
        db_commit(conn)
        db_close(conn)
        flash('Sale recorded successfully', 'success')
        return redirect(url_for('sales'))
    products = query(conn, 'SELECT * FROM products ORDER BY title').fetchall()
    db_close(conn)
    return render_template('add_sale.html', products=products)

@app.route('/sales/delete/<int:id>', methods=['POST'])
@login_required
def delete_sale(id):
    conn = get_db()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ?', (id,)).fetchone()
    if sale:
        query(conn, 'UPDATE products SET quantity = quantity + ? WHERE id = ?',
              (sale['quantity_sold'], sale['product_id']))
        query(conn, 'DELETE FROM sales WHERE id = ?', (id,))
        db_commit(conn)
    db_close(conn)
    flash('Sale deleted', 'success')
    return redirect(url_for('sales'))

@app.route('/expenses')
@login_required
def expenses():
    conn = get_db()
    all_expenses = query(conn, 'SELECT * FROM expenses ORDER BY expense_date DESC').fetchall()
    total = query(conn, 'SELECT COALESCE(SUM(amount),0) as t FROM expenses').fetchone()['t']
    db_close(conn)
    return render_template('expenses.html', expenses=all_expenses, total=total)

@app.route('/expenses/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        description = request.form['description']
        amount = float(request.form['amount'])
        category = request.form.get('category', '')
        conn = get_db()
        query(conn, 'INSERT INTO expenses (description, amount, category) VALUES (?,?,?)',
              (description, amount, category))
        db_commit(conn)
        db_close(conn)
        flash('Expense added successfully', 'success')
        return redirect(url_for('expenses'))
    return render_template('add_expense.html')

@app.route('/expenses/delete/<int:id>', methods=['POST'])
@login_required
def delete_expense(id):
    conn = get_db()
    query(conn, 'DELETE FROM expenses WHERE id = ?', (id,))
    db_commit(conn)
    db_close(conn)
    flash('Expense deleted', 'success')
    return redirect(url_for('expenses'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=not IS_PG)
