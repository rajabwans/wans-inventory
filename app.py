import os, sys, csv, io, re, secrets
import sqlite3
from functools import wraps
from datetime import date, datetime, timedelta
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, send_file, send_from_directory, abort, g)
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.environ.get('DATABASE_URL')

class PrefixMiddleware:
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        if environ['PATH_INFO'].startswith(self.prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][len(self.prefix):] or '/'
            environ['SCRIPT_NAME'] = environ.get('SCRIPT_NAME', '') + self.prefix
            return self.app(environ, start_response)
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'404 Not Found']


app = Flask(__name__)
URL_PREFIX = os.environ.get('URL_PREFIX', '/wans')
if URL_PREFIX and URL_PREFIX != '/':
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, URL_PREFIX)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(app.root_path, 'uploads'))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
ALLOWED_PROOF_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
csrf = CSRFProtect()
csrf.init_app(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["500 per day"])

PASSWORD = os.environ.get('APP_PASSWORD', 'wans123')
COMPANY_NAME = os.environ.get('COMPANY_NAME', 'WANS COLLECTION')
CURRENCY = os.environ.get('CURRENCY', 'UGX')
PAYMENT_PHONE = os.environ.get('PAYMENT_PHONE', '0763750114')
PRO_PRICE = os.environ.get('PRO_PRICE', 'UGX 15,000 / month')
TRIAL_DAYS = int(os.environ.get('TRIAL_DAYS', '7'))
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

@app.before_request
def refresh_biz():
    if session.get('user_id') and session.get('biz'):
        bid = session['biz'].get('id')
        if bid:
            conn = get_db()
            try:
                biz = query(conn, 'SELECT * FROM businesses WHERE id = ?', (bid,)).fetchone()
                if biz:
                    session['biz']['plan'] = biz['plan'] or 'free'
                    session['biz']['status'] = biz['status']
                    session['biz']['paid_until'] = biz['paid_until']
                    session['biz']['trial_ends_at'] = biz['trial_ends_at']
                    session['biz']['name'] = biz['name']
                    session['biz']['currency'] = biz['currency']
                    session['biz']['logo'] = biz['logo']
                elif session.get('role') != 'superadmin':
                    session.clear()
                    return redirect(url_for('login'))
            except Exception:
                pass
            db_close(conn)

@app.before_request
def block_suspended():
    if session.get('user_id'):
        status = session.get('biz', {}).get('status')
        if status == 'suspended':
            session.clear()
            return redirect(url_for('login'))
        if status == 'pending' and request.endpoint not in ('logout', 'static'):
            return render_template('error.html', error='Your business is still awaiting approval.'), 403
        allowed = ('logout', 'static', 'billing', 'request_upgrade', 'platform', 'serve_upload')
        if status == 'active' and session.get('role') != 'superadmin' \
                and get_effective_plan() == 'expired' and request.endpoint not in allowed:
            flash('Your free trial has ended. Activate Pro to continue using Wans Plan.', 'warning')
            return redirect(url_for('billing'))

@app.context_processor
def inject_globals():
    biz = session.get('biz', {})
    return dict(COMPANY_NAME=biz.get('name') or COMPANY_NAME,
                CURRENCY=biz.get('currency') or CURRENCY,
                TENANT_SLUG=biz.get('slug') or '',
                CURRENT_PLAN=get_effective_plan(),
                PAYMENT_PHONE=PAYMENT_PHONE,
                PRO_PRICE=PRO_PRICE,
                biz_plan_state=biz_plan_state,
                current_year=date.today().year)

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
    CREATE TABLE IF NOT EXISTS businesses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
        currency TEXT DEFAULT 'UGX', logo TEXT, about TEXT,
        plan TEXT DEFAULT 'free', status TEXT DEFAULT 'pending',
        paid_until TIMESTAMP, upgrade_requested INTEGER DEFAULT 0,
        upgrade_note TEXT, upgrade_proof TEXT, upgrade_requested_at TIMESTAMP,
        trial_ends_at TIMESTAMP,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL DEFAULT 1,
        username TEXT NOT NULL, password_hash TEXT NOT NULL,
        full_name TEXT, role TEXT DEFAULT 'staff',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(business_id, username)
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
        business_id INTEGER NOT NULL DEFAULT 1,
        user_id INTEGER, username TEXT, action TEXT NOT NULL,
        table_name TEXT NOT NULL, record_id INTEGER, details TEXT,
        ip_address TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL DEFAULT 1,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'product',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(business_id, kind, name)
    );
'''

SCHEMA_PG = '''
    CREATE TABLE IF NOT EXISTS businesses (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
        currency TEXT DEFAULT 'UGX', logo TEXT, about TEXT,
        plan TEXT DEFAULT 'free', status TEXT DEFAULT 'pending',
        paid_until TIMESTAMP, upgrade_requested BOOLEAN DEFAULT FALSE,
        upgrade_note TEXT, upgrade_proof TEXT, upgrade_requested_at TIMESTAMP,
        trial_ends_at TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        username TEXT NOT NULL, password_hash TEXT NOT NULL,
        full_name TEXT, role TEXT DEFAULT 'staff',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(business_id, username)
    );
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        author TEXT, isbn TEXT, publisher TEXT, category TEXT,
        quantity INTEGER DEFAULT 0, buying_price REAL DEFAULT 0, selling_price REAL DEFAULT 0, notes TEXT,
        version INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        name TEXT NOT NULL, phone TEXT, email TEXT, address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        product_id INTEGER NOT NULL REFERENCES products(id),
        customer_id INTEGER REFERENCES customers(id),
        customer_name TEXT,
        quantity_sold INTEGER NOT NULL, unit_price REAL NOT NULL,
        total_amount REAL NOT NULL, profit REAL NOT NULL,
        sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        description TEXT NOT NULL, amount REAL NOT NULL, category TEXT,
        user_id INTEGER REFERENCES users(id),
        expense_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stock_adjustments (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        product_id INTEGER NOT NULL REFERENCES products(id),
        adjustment_type TEXT NOT NULL, quantity INTEGER NOT NULL,
        reason TEXT, user_id INTEGER REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        user_id INTEGER, username TEXT, action TEXT NOT NULL,
        table_name TEXT NOT NULL, record_id INTEGER,
        details TEXT, ip_address TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY, business_id INTEGER NOT NULL DEFAULT 1,
        name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'product',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(business_id, kind, name)
    );
'''

MIGRATION_SQLITE = [
    "ALTER TABLE products ADD COLUMN version INTEGER DEFAULT 1",
    "ALTER TABLE sales ADD COLUMN customer_id INTEGER",
    "ALTER TABLE expenses ADD COLUMN user_id INTEGER",
    "ALTER TABLE products ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE customers ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE sales ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE expenses ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE stock_adjustments ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE audit_log ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE users ADD COLUMN business_id INTEGER DEFAULT 1",
    "ALTER TABLE businesses ADD COLUMN status TEXT DEFAULT 'pending'",
    "ALTER TABLE businesses ADD COLUMN paid_until TIMESTAMP",
    "ALTER TABLE businesses ADD COLUMN upgrade_requested INTEGER DEFAULT 0",
    "ALTER TABLE businesses ADD COLUMN upgrade_note TEXT",
    "ALTER TABLE businesses ADD COLUMN upgrade_proof TEXT",
    "ALTER TABLE businesses ADD COLUMN upgrade_requested_at TIMESTAMP",
    "ALTER TABLE businesses ADD COLUMN trial_ends_at TIMESTAMP",
]

MIGRATION_PG = [
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1",
    "ALTER TABLE sales ADD COLUMN IF NOT EXISTS customer_id INTEGER",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE customers ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE sales ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE stock_adjustments ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_id INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS paid_until TIMESTAMP",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS upgrade_requested BOOLEAN DEFAULT FALSE",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS upgrade_note TEXT",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS upgrade_proof TEXT",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS upgrade_requested_at TIMESTAMP",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP",
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

def create_default_business():
    conn = get_db()
    try:
        existing = query(conn, "SELECT id FROM businesses WHERE slug = 'wans'").fetchone()
        if not existing:
            query(conn, "INSERT INTO businesses (name, slug, currency, about, plan, status) VALUES (?,?,?,?,?,?)",
                  ('WANS COLLECTION', 'wans', 'UGX',
                   'WANS COLLECTION official inventory system', 'pro', 'active'))
            db_commit(conn)
            print('[INFO] Default business created: WANS COLLECTION (slug: wans)', file=sys.stderr)
        else:
            query(conn, "UPDATE businesses SET status = 'active' WHERE slug = 'wans'")
            db_commit(conn)
    except Exception as e:
        print(f'[BUSINESS INIT ERROR] {e}', file=sys.stderr)
    db_close(conn)

def create_default_admin():
    conn = get_db()
    try:
        existing = query(conn, "SELECT id FROM users WHERE username = 'admin' AND business_id = 1").fetchone()
        if not existing:
            pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
            pw_hash = generate_password_hash(pw)
            query(conn, "INSERT INTO users (business_id, username, password_hash, full_name, role) VALUES (?,?,?,?,?)",
                  (1, 'admin', pw_hash, 'Administrator', 'superadmin'))
            db_commit(conn)
            print(f'[INFO] Default admin created. Username: admin, Password: {pw}', file=sys.stderr)
        else:
            query(conn, "UPDATE users SET role = 'superadmin' WHERE username = 'admin' AND business_id = 1")
            db_commit(conn)
    except Exception as e:
        print(f'[ADMIN INIT ERROR] {e}', file=sys.stderr)
    db_close(conn)

DEFAULT_PRODUCT_CATEGORIES = ['Perfumes', 'Scented Oils', 'Toys', 'Womens Bags', 'Suitcases']
DEFAULT_EXPENSE_CATEGORIES = ['Rent', 'Utilities', 'Transport', 'Salaries', 'Marketing', 'Other']

def seed_default_categories():
    conn = get_db()
    try:
        for bid, names, kind in [(1, DEFAULT_PRODUCT_CATEGORIES, 'product'),
                                 (1, DEFAULT_EXPENSE_CATEGORIES, 'expense')]:
            for name in names:
                exists = query(conn, 'SELECT id FROM categories WHERE business_id = ? AND kind = ? AND name = ?',
                               (bid, kind, name)).fetchone()
                if not exists:
                    query(conn, 'INSERT INTO categories (business_id, name, kind) VALUES (?,?,?)',
                          (bid, name, kind))
        db_commit(conn)
        print('[INFO] Default categories seeded', file=sys.stderr)
    except Exception as e:
        print(f'[CATEGORY INIT ERROR] {e}', file=sys.stderr)
    db_close(conn)

init_db()
create_default_business()
create_default_admin()
seed_default_categories()

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
        if session.get('role') not in ('admin', 'superadmin'):
            flash('Admin access required', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        if session.get('role') != 'superadmin':
            flash('Superadmin access required', 'danger')
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

def get_business_id():
    return session.get('biz', {}).get('id') or session.get('business_id') or 1

PLAN_LIMITS = {
    'trial': {'products': 50, 'users': 2, 'customers': 100},
    'pro': {'products': None, 'users': None, 'customers': None},
    'expired': {'products': 0, 'users': 0, 'customers': 0},
}

def _parse_date(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S').date()
        except Exception:
            return datetime.strptime(value[:10], '%Y-%m-%d').date()
    return value.date() if hasattr(value, 'date') else value

def get_effective_plan():
    biz = session.get('biz', {})
    plan = biz.get('plan') or 'free'
    if plan == 'pro':
        paid_until = biz.get('paid_until')
        exp = _parse_date(paid_until)
        if exp and date.today() > exp:
            return 'expired'
        return 'pro'
    trial_end = _parse_date(biz.get('trial_ends_at'))
    if trial_end and date.today() <= trial_end:
        return 'trial'
    return 'expired'

def biz_plan_state(biz):
    """Best-effort plan state for a raw businesses row (used by platform)."""
    try:
        plan = biz['plan'] or 'free'
        if plan == 'pro':
            exp = _parse_date(biz['paid_until'])
            if exp and date.today() > exp:
                return 'expired'
            return 'pro'
        trial_end = _parse_date(biz['trial_ends_at'])
        if trial_end and date.today() <= trial_end:
            return 'trial'
        return 'expired'
    except (TypeError, KeyError, IndexError):
        return 'pro'

def check_limit(conn, kind):
    """Return True if the current business is allowed to add more rows of `kind`."""
    limits = PLAN_LIMITS.get(get_effective_plan(), PLAN_LIMITS['trial'])
    cap = limits.get(kind)
    if cap is None:
        return True
    bid = get_business_id()
    count = query(conn, f'SELECT COUNT(*) as c FROM {kind} WHERE business_id = ?', (bid,)).fetchone()['c']
    return count < cap

def log_audit(conn, action, table_name, record_id=None, details=None, business_id=None):
    try:
        ip = request.remote_addr or 'unknown'
        user_id = session.get('user_id')
        username = session.get('username', 'system')
        query(conn, '''INSERT INTO audit_log (business_id, user_id, username, action, table_name, record_id, details, ip_address)
                        VALUES (?,?,?,?,?,?,?,?)''',
              (business_id or get_business_id(), user_id, username, action, table_name, record_id, details, ip))
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

@app.route('/')
def landing():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    conn = get_db()
    bid = get_business_id()
    prods = query(conn, 'SELECT COUNT(*) as c FROM products WHERE business_id = ?', (bid,)).fetchone()
    db_close(conn)
    return render_template('landing.html', stats=prods)

@app.route('/signup', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def signup():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        business_name = sanitize_input(request.form.get('business_name', ''))
        slug = sanitize_input(request.form.get('slug', '').strip().lower())
        currency = sanitize_input(request.form.get('currency', 'UGX'))
        username = sanitize_input(request.form.get('username', ''))
        full_name = sanitize_input(request.form.get('full_name', ''))
        password = request.form.get('password', '')
        if not business_name or not slug or not username or not password:
            flash('Business name, slug, username and password are required', 'danger')
            return render_template('signup.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'danger')
            return render_template('signup.html')
        slug = re.sub(r'[^a-z0-9-]', '-', slug).strip('-')[:40]
        if not slug:
            flash('Invalid business slug', 'danger')
            return render_template('signup.html')
        conn = get_db()
        try:
            existing = query(conn, 'SELECT id FROM businesses WHERE slug = ?', (slug,)).fetchone()
            if existing:
                flash('That business slug is already taken', 'danger')
                db_close(conn)
                return render_template('signup.html')
            if IS_PG:
                cur = query(conn, 'INSERT INTO businesses (name, slug, currency, about) VALUES (?,?,?,?) RETURNING id',
                            (business_name, slug, currency, ''))
                business_id = cur.fetchone()['id']
            else:
                cur = query(conn, 'INSERT INTO businesses (name, slug, currency, about) VALUES (?,?,?,?)',
                            (business_name, slug, currency, ''))
                business_id = cur.lastrowid
            pw_hash = generate_password_hash(password)
            query(conn, 'INSERT INTO users (business_id, username, password_hash, full_name, role) VALUES (?,?,?,?,?)',
                  (business_id, username, pw_hash, full_name or username, 'admin'))
            db_commit(conn)
            log_audit(conn, 'signup', 'businesses', business_id, f'Business registered: {business_name}', business_id)
            flash('Business registered! You can now log in.', 'success')
            db_close(conn)
            return redirect(url_for('login', slug=slug))
        except Exception as e:
            flash(f'Error registering business: {e}', 'danger')
            db_close(conn)
            return render_template('signup.html')
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    slug = sanitize_input(request.args.get('slug', request.form.get('slug', '')))
    if request.method == 'POST':
        username = sanitize_input(request.form.get('username', ''))
        password = request.form.get('password', '')
        if not username or not password or not slug:
            flash('Username, business slug and password required', 'danger')
            return render_template('login.html', slug=slug)
        conn = get_db()
        user = query(conn, '''SELECT u.*, b.name as business_name, b.currency, b.slug, b.logo,
                                     b.status as biz_status, b.plan as biz_plan, b.paid_until, b.trial_ends_at
                              FROM users u JOIN businesses b ON u.business_id = b.id
                              WHERE u.username = ? AND b.slug = ?''',
                     (username, slug)).fetchone()
        db_close(conn)
        if user and check_password_hash(user['password_hash'], password):
            if user['biz_status'] == 'pending':
                flash('Your business is awaiting approval. You will be notified once activated.', 'warning')
                return render_template('login.html', slug=slug)
            if user['biz_status'] == 'suspended':
                flash('Your business has been suspended. Contact support.', 'danger')
                return render_template('login.html', slug=slug)
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name'] or user['username']
            session['biz'] = {'id': user['business_id'], 'name': user['business_name'],
                              'currency': user['currency'], 'slug': user['slug'], 'logo': user['logo'],
                              'plan': user['biz_plan'] or 'free', 'paid_until': user['paid_until'],
                              'trial_ends_at': user['trial_ends_at'],
                              'status': user['biz_status']}
            session.permanent = True
            app.permanent_session_lifetime = timedelta(hours=12)
            return redirect(url_for('dashboard'))
        flash('Invalid username, slug or password', 'danger')
    return render_template('login.html', slug=slug)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    bid = get_business_id()
    total_products = query(conn, 'SELECT COUNT(*) as c FROM products WHERE business_id = ?', (bid,)).fetchone()['c']
    total_stock = query(conn, 'SELECT COALESCE(SUM(quantity),0) as s FROM products WHERE business_id = ?', (bid,)).fetchone()['s']
    total_invested = query(conn, 'SELECT COALESCE(SUM(buying_price * quantity),0) as t FROM products WHERE business_id = ?', (bid,)).fetchone()['t']
    total_sales_amount = query(conn, 'SELECT COALESCE(SUM(total_amount),0) as t FROM sales WHERE business_id = ?', (bid,)).fetchone()['t']
    total_profit = query(conn, 'SELECT COALESCE(SUM(profit),0) as t FROM sales WHERE business_id = ?', (bid,)).fetchone()['t']
    total_possible_profit = query(conn, 'SELECT COALESCE(SUM((selling_price - buying_price) * quantity),0) as t FROM products WHERE business_id = ?', (bid,)).fetchone()['t']
    total_customers = query(conn, 'SELECT COUNT(*) as c FROM customers WHERE business_id = ?', (bid,)).fetchone()['c']
    first = date.today().replace(day=1)
    next_month = first.replace(month=first.month % 12 + 1, year=first.year + (first.month // 12))
    monthly_profit = query(conn, 'SELECT COALESCE(SUM(profit),0) as t FROM sales WHERE sale_date >= ? AND sale_date < ? AND business_id = ?',
                           (first.isoformat(), next_month.isoformat(), bid)).fetchone()['t']
    monthly_expenses = query(conn, 'SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE expense_date >= ? AND expense_date < ? AND business_id = ?',
                              (first.isoformat(), next_month.isoformat(), bid)).fetchone()['t']
    low_stock = query(conn, 'SELECT * FROM products WHERE quantity <= 5 AND business_id = ? ORDER BY quantity LIMIT 10', (bid,)).fetchall()
    recent_sales = query(conn, '''
        SELECT s.*, p.title, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_display
        FROM sales s JOIN products p ON s.product_id = p.id
        LEFT JOIN customers c ON s.customer_id = c.id
        WHERE s.business_id = ? AND p.business_id = ?
        ORDER BY s.sale_date DESC LIMIT 10
    ''', (bid, bid)).fetchall()
    recent_sales_count = query(conn, 'SELECT COUNT(*) as c FROM sales WHERE business_id = ?', (bid,)).fetchone()['c']
    category_breakdown = query(conn, '''
        SELECT COALESCE(category,'Uncategorized') as category,
               COUNT(*) as count, COALESCE(SUM(quantity),0) as stock,
               COALESCE(SUM(buying_price * quantity),0) as value
        FROM products WHERE business_id = ? GROUP BY category ORDER BY value DESC
    ''', (bid,)).fetchall()
    top_products = query(conn, '''
        SELECT p.title, SUM(s.quantity_sold) as total_sold, COALESCE(SUM(s.total_amount),0) as revenue
        FROM sales s JOIN products p ON s.product_id = p.id
        WHERE s.business_id = ? AND p.business_id = ?
        GROUP BY p.id ORDER BY revenue DESC LIMIT 5
    ''', (bid, bid)).fetchall()
    db_close(conn)
    return render_template('dashboard.html', total_products=total_products, total_stock=total_stock,
                           total_invested=total_invested, total_sales_amount=total_sales_amount,
                           total_profit=total_profit, total_possible_profit=total_possible_profit,
                           monthly_profit=monthly_profit, monthly_expenses=monthly_expenses,
                           low_stock=low_stock, recent_sales=recent_sales, recent_sales_count=recent_sales_count,
                           category_breakdown=category_breakdown, top_products=top_products,
                           total_customers=total_customers)

@app.route('/categories', methods=['GET', 'POST'])
@admin_required
def categories():
    conn = get_db()
    bid = get_business_id()
    kind = sanitize_input(request.form.get('kind', ''))
    if request.method == 'POST':
        name = sanitize_input(request.form.get('name', '')).strip()
        if kind not in ('product', 'expense'):
            flash('Invalid category type', 'danger')
        elif not name:
            flash('Category name is required', 'danger')
        else:
            exists = query(conn, 'SELECT id FROM categories WHERE business_id = ? AND kind = ? AND name = ?',
                           (bid, kind, name)).fetchone()
            if exists:
                flash(f'Category "{name}" already exists', 'warning')
            else:
                query(conn, 'INSERT INTO categories (business_id, name, kind) VALUES (?,?,?)',
                      (bid, name, kind))
                db_commit(conn)
                log_audit(conn, 'create', 'categories', None, f'Added {kind} category: {name}')
                flash(f'Category "{name}" added', 'success')
        db_close(conn)
        return redirect(url_for('categories'))
    product_cats = query(conn, 'SELECT * FROM categories WHERE business_id = ? AND kind = ? ORDER BY lower(name)', (bid, 'product')).fetchall()
    expense_cats = query(conn, 'SELECT * FROM categories WHERE business_id = ? AND kind = ? ORDER BY lower(name)', (bid, 'expense')).fetchall()
    db_close(conn)
    return render_template('categories.html', product_cats=product_cats, expense_cats=expense_cats)

@app.route('/categories/delete/<int:id>', methods=['POST'])
@admin_required
def delete_category(id):
    conn = get_db()
    bid = get_business_id()
    try:
        cat = query(conn, 'SELECT * FROM categories WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        if not cat:
            flash('Category not found', 'danger')
        else:
            query(conn, 'DELETE FROM categories WHERE id = ? AND business_id = ?', (id, bid))
            db_commit(conn)
            log_audit(conn, 'delete', 'categories', id, f'Removed {cat["kind"]} category: {cat["name"]}')
            flash(f'Category "{cat["name"]}" removed', 'success')
    except Exception as e:
        flash(f'Error removing category: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('categories'))

@app.route('/products')
@login_required
def products():
    page = request.args.get('page', 1, type=int)
    conn = get_db()
    bid = get_business_id()
    rows, total, total_pages = paginate_query(conn,
        'SELECT *, (selling_price - buying_price) as profit_margin FROM products WHERE business_id = ? ORDER BY created_at DESC',
        [bid], page)
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
        bid = get_business_id()
        try:
            if not check_limit(conn, 'products'):
                flash(f'Product limit reached on your {get_effective_plan().capitalize()} plan. Please upgrade.', 'warning')
                db_close(conn)
                return redirect(url_for('products'))
            query(conn, 'INSERT INTO products (business_id, title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes) VALUES (?,?,?,?,?,?,?,?,?,?)',
                  (bid, title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes))
            db_commit(conn)
            log_audit(conn, 'create', 'products', None, f'Added: {title}')
            flash('Product added successfully', 'success')
        except Exception as e:
            flash(f'Error adding product: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('products'))
    conn = get_db()
    bid = get_business_id()
    cats = query(conn, 'SELECT name FROM categories WHERE business_id = ? AND kind = ? ORDER BY lower(name)', (bid, 'product')).fetchall()
    db_close(conn)
    return render_template('add_product.html', categories=cats)

@app.route('/products/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_product(id):
    conn = get_db()
    bid = get_business_id()
    product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
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
                            WHERE id=? AND version=? AND business_id=?''',
                  (title, author, isbn, publisher, category, quantity, buying_price, selling_price, notes, id, old_version, bid))
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
    cats = query(conn, 'SELECT name FROM categories WHERE business_id = ? AND kind = ? ORDER BY lower(name)', (bid, 'product')).fetchall()
    db_close(conn)
    return render_template('edit_product.html', product=product, categories=cats)

@app.route('/products/delete/<int:id>', methods=['POST'])
@login_required
def delete_product(id):
    conn = get_db()
    bid = get_business_id()
    try:
        product = query(conn, 'SELECT title FROM products WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        query(conn, 'DELETE FROM sales WHERE product_id = ? AND business_id = ?', (id, bid))
        query(conn, 'DELETE FROM stock_adjustments WHERE product_id = ? AND business_id = ?', (id, bid))
        query(conn, 'DELETE FROM products WHERE id = ? AND business_id = ?', (id, bid))
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
    bid = get_business_id()
    try:
        product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
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
        query(conn, 'INSERT INTO stock_adjustments (business_id, product_id, adjustment_type, quantity, reason, user_id) VALUES (?,?,?,?,?,?)',
              (bid, id, adj_type, quantity, reason, session.get('user_id')))
        query(conn, 'UPDATE products SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND business_id = ?', (new_qty, id, bid))
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
    bid = get_business_id()
    sql = '''SELECT s.*, p.title, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_display
             FROM sales s JOIN products p ON s.product_id = p.id
             LEFT JOIN customers c ON s.customer_id = c.id
             WHERE s.business_id = ? AND p.business_id = ?
             ORDER BY s.sale_date DESC'''
    rows, total, total_pages = paginate_query(conn, sql, [bid, bid], page)
    db_close(conn)
    return render_template('sales.html', sales=rows, page=page, total_pages=total_pages, total=total)

@app.route('/sales/add', methods=['GET', 'POST'])
@login_required
def add_sale():
    conn = get_db()
    bid = get_business_id()
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
        product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (product_id, bid)).fetchone()
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
            query(conn, '''INSERT INTO sales (business_id, product_id, customer_id, customer_name, quantity_sold, unit_price, total_amount, profit)
                           VALUES (?,?,?,?,?,?,?,?)''',
                  (bid, product_id, customer_id, customer_name, quantity_sold, unit_price, total_amount, profit))
            query(conn, 'UPDATE products SET quantity = quantity - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND business_id = ?',
                  (quantity_sold, product_id, bid))
            db_commit(conn)
            log_audit(conn, 'create', 'sales', None, f'Sale: {quantity_sold}x {product["title"]} for {CURRENCY} {total_amount:,.0f}')
            flash('Sale recorded successfully', 'success')
        except Exception as e:
            flash(f'Error recording sale: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('sales'))
    products = query(conn, 'SELECT * FROM products WHERE quantity > 0 AND business_id = ? ORDER BY title', (bid,)).fetchall()
    customers = query(conn, 'SELECT * FROM customers WHERE business_id = ? ORDER BY name', (bid,)).fetchall()
    db_close(conn)
    return render_template('add_sale.html', products=products, customers=customers)

@app.route('/sales/delete/<int:id>', methods=['POST'])
@login_required
def delete_sale(id):
    conn = get_db()
    bid = get_business_id()
    try:
        sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        if sale:
            query(conn, 'UPDATE products SET quantity = quantity + ? WHERE id = ? AND business_id = ?',
                  (sale['quantity_sold'], sale['product_id'], bid))
            query(conn, 'DELETE FROM sales WHERE id = ? AND business_id = ?', (id, bid))
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
    bid = get_business_id()
    rows, total, total_pages = paginate_query(conn,
        'SELECT * FROM expenses WHERE business_id = ? ORDER BY expense_date DESC', [bid], page)
    total_all = query(conn, 'SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE business_id = ?', (bid,)).fetchone()['t']
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
        bid = get_business_id()
        try:
            query(conn, 'INSERT INTO expenses (business_id, description, amount, category, user_id) VALUES (?,?,?,?,?)',
                  (bid, description, amount, category, session.get('user_id')))
            db_commit(conn)
            log_audit(conn, 'create', 'expenses', None, f'Expense: {description} - {CURRENCY} {amount:,.0f}')
            flash('Expense added successfully', 'success')
        except Exception as e:
            flash(f'Error adding expense: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('expenses'))
    conn = get_db()
    bid = get_business_id()
    cats = query(conn, 'SELECT name FROM categories WHERE business_id = ? AND kind = ? ORDER BY lower(name)', (bid, 'expense')).fetchall()
    db_close(conn)
    return render_template('add_expense.html', categories=cats)

@app.route('/expenses/delete/<int:id>', methods=['POST'])
@login_required
def delete_expense(id):
    conn = get_db()
    bid = get_business_id()
    try:
        query(conn, 'DELETE FROM expenses WHERE id = ? AND business_id = ?', (id, bid))
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
    bid = get_business_id()
    rows, total, total_pages = paginate_query(conn,
        'SELECT * FROM customers WHERE business_id = ? ORDER BY name', [bid], page)
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
        bid = get_business_id()
        if not check_limit(conn, 'customers'):
            flash(f'Customer limit reached on your {get_effective_plan().capitalize()} plan. Please upgrade.', 'warning')
            db_close(conn)
            return redirect(url_for('customers'))
        try:
            query(conn, 'INSERT INTO customers (business_id, name, phone, email, address) VALUES (?,?,?,?,?)',
                  (bid, name, phone, email, address))
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
    bid = get_business_id()
    customer = query(conn, 'SELECT * FROM customers WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
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
            query(conn, 'UPDATE customers SET name=?, phone=?, email=?, address=? WHERE id=? AND business_id=?',
                  (name, phone, email, address, id, bid))
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
    bid = get_business_id()
    try:
        query(conn, 'UPDATE sales SET customer_id = NULL WHERE customer_id = ? AND business_id = ?', (id, bid))
        query(conn, 'DELETE FROM customers WHERE id = ? AND business_id = ?', (id, bid))
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
    bid = get_business_id()
    customer = query(conn, 'SELECT * FROM customers WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
    if not customer:
        flash('Customer not found', 'danger')
        db_close(conn)
        return redirect(url_for('customers'))
    sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.customer_id = ? AND s.business_id = ? AND p.business_id = ? ORDER BY s.sale_date DESC
    ''', (id, bid, bid)).fetchall()
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
    bid = get_business_id()
    sql = '''SELECT sa.*, p.title, u.username FROM stock_adjustments sa
             JOIN products p ON sa.product_id = p.id
             LEFT JOIN users u ON sa.user_id = u.id
             WHERE sa.business_id = ? AND p.business_id = ?
             ORDER BY sa.created_at DESC'''
    rows, total, total_pages = paginate_query(conn, sql, [bid, bid], page)
    db_close(conn)
    return render_template('stock/history.html', adjustments=rows,
                           page=page, total_pages=total_pages, total=total)

@app.route('/invoices/<int:sale_id>')
@login_required
def invoice(sale_id):
    conn = get_db()
    bid = get_business_id()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (sale_id, bid)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (sale['product_id'], bid)).fetchone()
    customer = None
    if sale['customer_id']:
        customer = query(conn, 'SELECT * FROM customers WHERE id = ? AND business_id = ?', (sale['customer_id'], bid)).fetchone()
    db_close(conn)
    sale_date = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
    return render_template('invoice.html', sale=sale, product=product, sale_date=sale_date, customer=customer)

@app.route('/invoices/<int:sale_id>/pdf')
@login_required
def invoice_pdf(sale_id):
    from utils.pdf import generate_invoice_pdf
    conn = get_db()
    bid = get_business_id()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (sale_id, bid)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (sale['product_id'], bid)).fetchone()
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
    bid = get_business_id()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (sale_id, bid)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (sale['product_id'], bid)).fetchone()
    customer = None
    if sale['customer_id']:
        customer = query(conn, 'SELECT * FROM customers WHERE id = ? AND business_id = ?', (sale['customer_id'], bid)).fetchone()
    db_close(conn)
    sale_date = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
    return render_template('receipt.html', sale=sale, product=product, sale_date=sale_date, customer=customer)

@app.route('/receipts/<int:sale_id>/pdf')
@login_required
def receipt_pdf(sale_id):
    from utils.pdf import generate_receipt_pdf
    conn = get_db()
    bid = get_business_id()
    sale = query(conn, 'SELECT * FROM sales WHERE id = ? AND business_id = ?', (sale_id, bid)).fetchone()
    if not sale:
        db_close(conn)
        flash('Sale not found', 'danger')
        return redirect(url_for('sales'))
    product = query(conn, 'SELECT * FROM products WHERE id = ? AND business_id = ?', (sale['product_id'], bid)).fetchone()
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
    bid = get_business_id()
    prods = query(conn, 'SELECT * FROM products WHERE business_id = ? ORDER BY category, title', (bid,)).fetchall()
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
    bid = get_business_id()
    prods = query(conn, 'SELECT * FROM products WHERE business_id = ? ORDER BY category, title', (bid,)).fetchall()
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
    bid = get_business_id()
    prods = query(conn, 'SELECT * FROM products WHERE business_id = ? ORDER BY category, title', (bid,)).fetchall()
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
    bid = get_business_id()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.sale_date >= ? AND s.sale_date <= ? AND s.business_id = ? AND p.business_id = ?
        ORDER BY s.sale_date DESC
    ''', (date_from, date_to + ' 23:59:59', bid, bid)).fetchall()
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
    bid = get_business_id()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.sale_date >= ? AND s.sale_date <= ? AND s.business_id = ? AND p.business_id = ?
        ORDER BY s.sale_date DESC
    ''', (date_from, date_to + ' 23:59:59', bid, bid)).fetchall()
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
    bid = get_business_id()
    all_sales = query(conn, '''
        SELECT s.*, p.title FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.sale_date >= ? AND s.sale_date <= ? AND s.business_id = ? AND p.business_id = ?
        ORDER BY s.sale_date DESC
    ''', (date_from, date_to + ' 23:59:59', bid, bid)).fetchall()
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
    bid = get_business_id()
    sales_data = query(conn, '''
        SELECT COALESCE(SUM(total_amount), 0) as revenue,
               COALESCE(SUM(profit), 0) as gross_profit
        FROM sales WHERE sale_date >= ? AND sale_date <= ? AND business_id = ?
    ''', (date_from, date_to + ' 23:59:59', bid)).fetchone()
    revenue = sales_data['revenue']
    gross_profit = sales_data['gross_profit']
    cogs = revenue - gross_profit
    expenses = query(conn, '''
        SELECT COALESCE(category, 'Uncategorized') as category, SUM(amount) as amount
        FROM expenses WHERE expense_date >= ? AND expense_date <= ? AND business_id = ?
        GROUP BY category ORDER BY amount DESC
    ''', (date_from, date_to + ' 23:59:59', bid)).fetchall()
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
    bid = get_business_id()
    sales_data = query(conn, '''
        SELECT COALESCE(SUM(total_amount), 0) as revenue,
               COALESCE(SUM(profit), 0) as gross_profit
        FROM sales WHERE sale_date >= ? AND sale_date <= ? AND business_id = ?
    ''', (date_from, date_to + ' 23:59:59', bid)).fetchone()
    revenue = sales_data['revenue']
    gross_profit = sales_data['gross_profit']
    cogs = revenue - gross_profit
    expenses = query(conn, '''
        SELECT COALESCE(category, 'Uncategorized') as category, SUM(amount) as amount
        FROM expenses WHERE expense_date >= ? AND expense_date <= ? AND business_id = ?
        GROUP BY category ORDER BY amount DESC
    ''', (date_from, date_to + ' 23:59:59', bid)).fetchall()
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
    bid = get_business_id()
    sales_data = query(conn, '''
        SELECT COALESCE(SUM(total_amount), 0) as revenue,
               COALESCE(SUM(profit), 0) as gross_profit
        FROM sales WHERE sale_date >= ? AND sale_date <= ? AND business_id = ?
    ''', (date_from, date_to + ' 23:59:59', bid)).fetchone()
    revenue = sales_data['revenue']
    gross_profit = sales_data['gross_profit']
    cogs = revenue - gross_profit
    expenses = query(conn, '''
        SELECT COALESCE(category, 'Uncategorized') as category, SUM(amount) as amount
        FROM expenses WHERE expense_date >= ? AND expense_date <= ? AND business_id = ?
        GROUP BY category ORDER BY amount DESC
    ''', (date_from, date_to + ' 23:59:59', bid)).fetchall()
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
    bid = get_business_id()
    sql = 'SELECT * FROM audit_log WHERE business_id = ? ORDER BY created_at DESC'
    rows, total, total_pages = paginate_query(conn, sql, [bid], page)
    db_close(conn)
    return render_template('audit/log.html', logs=rows,
                           page=page, total_pages=total_pages, total=total)

@app.route('/admin/business', methods=['GET', 'POST'])
@admin_required
def manage_business():
    conn = get_db()
    bid = get_business_id()
    biz = query(conn, 'SELECT * FROM businesses WHERE id = ?', (bid,)).fetchone()
    if not biz:
        db_close(conn)
        flash('Business not found', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = sanitize_input(request.form.get('name', '')) or biz['name']
        currency = sanitize_input(request.form.get('currency', '')) or biz['currency']
        logo = sanitize_input(request.form.get('logo', '')) or ''
        about = sanitize_input(request.form.get('about', '')) or ''
        try:
            query(conn, 'UPDATE businesses SET name=?, currency=?, logo=?, about=? WHERE id=?',
                  (name, currency, logo, about, bid))
            db_commit(conn)
            session['biz']['name'] = name
            session['biz']['currency'] = currency
            session['biz']['logo'] = logo
            log_audit(conn, 'update', 'businesses', bid, f'Updated business settings')
            flash('Business settings updated', 'success')
        except Exception as e:
            flash(f'Error updating business: {e}', 'danger')
        db_close(conn)
        return redirect(url_for('manage_business'))
    db_close(conn)
    return render_template('auth/manage_business.html', biz=biz)

@app.route('/platform')
@superadmin_required
def platform():
    conn = get_db()
    businesses = query(conn, '''SELECT b.*, (SELECT COUNT(*) FROM products p WHERE p.business_id = b.id) as product_count,
                                       (SELECT COUNT(*) FROM users u WHERE u.business_id = b.id) as user_count
                                FROM businesses b ORDER BY b.created_at DESC''').fetchall()
    db_close(conn)
    return render_template('platform.html', businesses=businesses)

@app.route('/platform/<int:id>/approve', methods=['POST'])
@superadmin_required
def platform_approve(id):
    conn = get_db()
    try:
        query(conn, "UPDATE businesses SET status = 'active', trial_ends_at = COALESCE(trial_ends_at, ?) WHERE id = ?",
              ((datetime.now() + timedelta(days=TRIAL_DAYS)).isoformat(sep=' ')[:19], id))
        db_commit(conn)
        log_audit(conn, 'approve', 'businesses', id, f'Approved business #{id}')
        flash('Business approved', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('platform'))

@app.route('/platform/<int:id>/suspend', methods=['POST'])
@superadmin_required
def platform_suspend(id):
    conn = get_db()
    try:
        query(conn, "UPDATE businesses SET status = 'suspended' WHERE id = ?", (id,))
        db_commit(conn)
        log_audit(conn, 'suspend', 'businesses', id, f'Suspended business #{id}')
        flash('Business suspended', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('platform'))

@app.route('/platform/<int:id>/reject', methods=['POST'])
@superadmin_required
def platform_reject(id):
    conn = get_db()
    try:
        query(conn, "UPDATE businesses SET status = 'rejected' WHERE id = ?", (id,))
        query(conn, "UPDATE users SET role = 'staff' WHERE business_id = ? AND role = 'admin'", (id,))
        db_commit(conn)
        log_audit(conn, 'reject', 'businesses', id, f'Rejected business #{id}')
        flash('Business rejected', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('platform'))

@app.route('/platform/<int:id>/plan', methods=['POST'])
@superadmin_required
def platform_set_plan(id):
    plan = sanitize_input(request.form.get('plan', 'free'))
    if plan not in ('free', 'pro'):
        plan = 'free'
    paid_until_raw = sanitize_input(request.form.get('paid_until', ''))
    conn = get_db()
    try:
        if paid_until_raw:
            paid_until = paid_until_raw.strip()
        else:
            paid_until = None
        query(conn, 'UPDATE businesses SET plan = ?, paid_until = ? WHERE id = ?', (plan, paid_until, id))
        if plan == 'pro':
            old = query(conn, 'SELECT upgrade_proof FROM businesses WHERE id = ?', (id,)).fetchone()
            query(conn, '''UPDATE businesses SET upgrade_requested = 0, upgrade_note = NULL,
                            upgrade_proof = NULL, upgrade_requested_at = NULL WHERE id = ?''', (id,))
            _delete_proof_file(old['upgrade_proof'] if old else None)
        db_commit(conn)
        log_audit(conn, 'plan', 'businesses', id, f'Set business #{id} plan: {plan}, paid until {paid_until or "never"}')
        flash('Plan updated', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('platform'))

@app.route('/platform/<int:id>/clear-upgrade', methods=['POST'])
@superadmin_required
def platform_clear_upgrade(id):
    conn = get_db()
    try:
        old = query(conn, 'SELECT upgrade_proof FROM businesses WHERE id = ?', (id,)).fetchone()
        query(conn, '''UPDATE businesses SET upgrade_requested = 0, upgrade_note = NULL,
                        upgrade_proof = NULL, upgrade_requested_at = NULL WHERE id = ?''', (id,))
        _delete_proof_file(old['upgrade_proof'] if old else None)
        db_commit(conn)
        log_audit(conn, 'upgrade', 'businesses', id, f'Dismissed upgrade request for business #{id}')
        flash('Upgrade request dismissed', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('platform'))

@app.route('/billing')
@login_required
def billing():
    conn = get_db()
    bid = get_business_id()
    biz = query(conn, 'SELECT * FROM businesses WHERE id = ?', (bid,)).fetchone()
    if not biz:
        db_close(conn)
        return redirect(url_for('dashboard'))
    plan = get_effective_plan()
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS['trial'])
    usage = {}
    for kind in ('products', 'users', 'customers'):
        usage[kind] = query(conn, f'SELECT COUNT(*) as c FROM {kind} WHERE business_id = ?', (bid,)).fetchone()['c']
    db_close(conn)
    return render_template('billing.html', biz=biz, plan=plan, limits=limits, usage=usage)

def _delete_proof_file(name):
    if not name:
        return
    try:
        path = os.path.join(UPLOAD_FOLDER, os.path.basename(name))
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass

@app.route('/billing/request-upgrade', methods=['POST'])
@login_required
def request_upgrade():
    conn = get_db()
    bid = get_business_id()
    try:
        note = sanitize_input(request.form.get('transaction_ref', '')).strip()
        if not note:
            note = sanitize_input(request.form.get('note', '')).strip()
        if not note:
            note = sanitize_input(request.form.get('message', '')).strip()
        if note:
            note = note[:500]
        proof_name = None
        f = request.files.get('proof')
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in ALLOWED_PROOF_EXT:
                db_close(conn)
                flash('Proof must be an image (PNG, JPG, WEBP, GIF).', 'danger')
                return redirect(url_for('billing'))
            proof_name = f'proof_{bid}_{secrets.token_hex(6)}{ext}'
            f.save(os.path.join(UPLOAD_FOLDER, proof_name))
        if not note and not proof_name:
            db_close(conn)
            flash('Please include a Mobile Money transaction ID or message with your request.', 'danger')
            return redirect(url_for('billing'))
        query(conn, '''UPDATE businesses SET upgrade_requested = 1,
                        upgrade_note = COALESCE(?, upgrade_note),
                        upgrade_proof = COALESCE(?, upgrade_proof),
                        upgrade_requested_at = CURRENT_TIMESTAMP
                        WHERE id = ?''', (note or None, proof_name, bid))
        db_commit(conn)
        log_audit(conn, 'upgrade', 'businesses', bid, f'Requested pro upgrade. Note: {note or "none"}, proof: {proof_name or "none"}')
        flash('Upgrade request sent to the administrator with your payment details.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    db_close(conn)
    return redirect(url_for('billing'))

@app.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename):
    if not filename.startswith('proof_'):
        abort(404)
    fname = os.path.basename(filename)
    conn = get_db()
    if session.get('role') != 'superadmin':
        owner = query(conn, 'SELECT id, upgrade_proof FROM businesses WHERE upgrade_proof = ?', (fname,)).fetchone()
        if not owner or owner['id'] != get_business_id():
            db_close(conn)
            abort(403)
    db_close(conn)
    return send_from_directory(UPLOAD_FOLDER, fname)

@app.route('/admin/users')
@admin_required
def manage_users():
    conn = get_db()
    bid = get_business_id()
    users = query(conn, 'SELECT * FROM users WHERE business_id = ? ORDER BY created_at', (bid,)).fetchall()
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
        bid = get_business_id()
        try:
            if not check_limit(conn, 'users'):
                flash(f'User limit reached on your {get_effective_plan().capitalize()} plan. Please upgrade.', 'warning')
                db_close(conn)
                return redirect(url_for('manage_users'))
            existing = query(conn, 'SELECT id FROM users WHERE business_id = ? AND username = ?', (bid, username)).fetchone()
            if existing:
                flash('Username already exists', 'danger')
                db_close(conn)
                return redirect(url_for('add_user'))
            pw_hash = generate_password_hash(password)
            query(conn, 'INSERT INTO users (business_id, username, password_hash, full_name, role) VALUES (?,?,?,?,?)',
                  (bid, username, pw_hash, full_name, role))
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
    bid = get_business_id()
    user = query(conn, 'SELECT * FROM users WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
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
                query(conn, 'UPDATE users SET full_name=?, role=?, password_hash=? WHERE id=? AND business_id=?',
                      (full_name, role, pw_hash, id, bid))
            else:
                query(conn, 'UPDATE users SET full_name=?, role=? WHERE id=? AND business_id=?',
                      (full_name, role, id, bid))
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
    bid = get_business_id()
    try:
        user = query(conn, 'SELECT * FROM users WHERE id = ? AND business_id = ?', (id, bid)).fetchone()
        if user and user['username'] == 'admin':
            flash('Cannot delete the admin account', 'danger')
            db_close(conn)
            return redirect(url_for('manage_users'))
        query(conn, 'DELETE FROM users WHERE id = ? AND business_id = ?', (id, bid))
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
