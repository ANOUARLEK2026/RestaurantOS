from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import secrets
import hashlib
import hmac
import traceback
import logging
import mimetypes
import shutil
import re

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'database' / 'restaurantos.db'
SCHEMA_PATH = ROOT / 'database' / 'schema.sql'
SEED_PATH = ROOT / 'database' / 'seed.sql'
FRONTEND = ROOT / 'frontend'
STATIC = ROOT / 'static'
LOG_DIR = ROOT / 'logs'
BACKUP_DIR = ROOT / 'database' / 'backups'
LOG_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / 'restaurantos.log',
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)

SESSIONS = {}
SESSION_TTL = timedelta(hours=8)
PBKDF2_ROUNDS = 240_000
ALLOWED_ORDER_STATUS = {'open', 'preparing', 'ready', 'paid', 'cancelled'}
ALLOWED_TABLE_STATUS = {'available', 'occupied', 'reserved', 'cleaning'}


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def money_decimal(value):
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        d = Decimal('0')
    return d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def money_float(value):
    return float(money_decimal(value))


def ensure_column(conn, table, column, definition):
    cols = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in cols:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def hash_password(password, iterations=PBKDF2_ROUNDS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f'pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}'


def verify_password(password, encoded):
    try:
        algo, iterations, salt_hex, digest_hex = encoded.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except Exception:
        return False


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = db()
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    if SEED_PATH.exists():
        with open(SEED_PATH, 'r', encoding='utf-8') as f:
            conn.executescript(f.read())
    # Schema additions are intentionally idempotent so an existing database can be upgraded.
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT NOT NULL UNIQUE COLLATE NOCASE,
      display_name TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'manager',
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS inventory_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sku TEXT UNIQUE,
      name TEXT NOT NULL,
      unit TEXT NOT NULL DEFAULT 'unit',
      current_stock REAL NOT NULL DEFAULT 0,
      min_stock REAL NOT NULL DEFAULT 0,
      cost_price REAL NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stock_movements (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      inventory_item_id INTEGER NOT NULL,
      movement_type TEXT NOT NULL,
      quantity REAL NOT NULL,
      unit_cost REAL NOT NULL DEFAULT 0,
      reference_type TEXT DEFAULT '',
      reference_id INTEGER,
      notes TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE RESTRICT
    );
    CREATE TABLE IF NOT EXISTS menu_recipes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      menu_item_id INTEGER NOT NULL,
      inventory_item_id INTEGER NOT NULL,
      quantity REAL NOT NULL,
      FOREIGN KEY (menu_item_id) REFERENCES menu_items(id) ON DELETE CASCADE,
      FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE RESTRICT,
      UNIQUE(menu_item_id, inventory_item_id)
    );
    CREATE TABLE IF NOT EXISTS suppliers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      phone TEXT DEFAULT '',
      email TEXT DEFAULT '',
      notes TEXT DEFAULT '',
      active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS purchases (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      supplier_id INTEGER,
      invoice_no TEXT DEFAULT '',
      status TEXT NOT NULL DEFAULT 'received',
      subtotal REAL NOT NULL DEFAULT 0,
      tax REAL NOT NULL DEFAULT 0,
      total REAL NOT NULL DEFAULT 0,
      purchased_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      notes TEXT DEFAULT '',
      FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS purchase_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      purchase_id INTEGER NOT NULL,
      inventory_item_id INTEGER NOT NULL,
      quantity REAL NOT NULL,
      unit_cost REAL NOT NULL,
      line_total REAL NOT NULL,
      FOREIGN KEY (purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
      FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE RESTRICT
    );
    CREATE TABLE IF NOT EXISTS expenses (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      category TEXT NOT NULL,
      description TEXT NOT NULL,
      amount REAL NOT NULL,
      paid INTEGER NOT NULL DEFAULT 1,
      expense_date TEXT NOT NULL DEFAULT CURRENT_DATE,
      notes TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id INTEGER,
      payment_method TEXT NOT NULL DEFAULT 'cash',
      amount REAL NOT NULL,
      reference TEXT DEFAULT '',
      paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      notes TEXT DEFAULT '',
      FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS cash_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      opened_by INTEGER,
      opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      opening_amount REAL NOT NULL DEFAULT 0,
      closed_at TEXT,
      closing_amount REAL,
      status TEXT NOT NULL DEFAULT 'open',
      notes TEXT DEFAULT '',
      FOREIGN KEY (opened_by) REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inventory_stock ON inventory_items(current_stock, min_stock);
    CREATE INDEX IF NOT EXISTS idx_movements_item_date ON stock_movements(inventory_item_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
    CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(paid_at);
    CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(purchased_at);
    ''')
    user = conn.execute('SELECT id FROM users WHERE email=?', ('admin@restaurantos.local',)).fetchone()
    if not user:
        conn.execute(
            'INSERT INTO users(email,display_name,password_hash,role) VALUES (?,?,?,?)',
            ('admin@restaurantos.local', 'Administrator', hash_password('Admin@123'), 'admin')
        )
    # Incremental application migrations for richer operations.
    ensure_column(conn, 'orders', 'customer_id', 'INTEGER')
    ensure_column(conn, 'orders', 'discount', 'REAL NOT NULL DEFAULT 0')
    ensure_column(conn, 'orders', 'customer_name', "TEXT DEFAULT ''")
    ensure_column(conn, 'order_items', 'tax_rate', 'REAL NOT NULL DEFAULT 0')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, created_at)")
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS customers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      phone TEXT DEFAULT '',
      email TEXT DEFAULT '',
      notes TEXT DEFAULT '',
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS reservations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      customer_id INTEGER,
      table_id INTEGER,
      reservation_date TEXT NOT NULL,
      reservation_time TEXT NOT NULL,
      guests INTEGER NOT NULL DEFAULT 2,
      status TEXT NOT NULL DEFAULT 'booked',
      notes TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
      FOREIGN KEY (table_id) REFERENCES dining_tables(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_reservations_datetime ON reservations(reservation_date,reservation_time,status);
    CREATE TABLE IF NOT EXISTS debts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      debt_type TEXT NOT NULL,
      party_name TEXT NOT NULL,
      reference TEXT DEFAULT '',
      amount REAL NOT NULL,
      paid_amount REAL NOT NULL DEFAULT 0,
      due_date TEXT,
      status TEXT NOT NULL DEFAULT 'open',
      notes TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_debts_status_date ON debts(status,due_date);
    CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
    ''')
    # Settings that make the frontend independent from hard-coded demo values.
    defaults = {
        'restaurant_name': 'RestaurantOS Demo', 'currency': 'DZD', 'tax_rate': '0',
        'phone': '', 'address': '', 'receipt_footer': 'Thank you for dining with us.'
    }
    for k, v in defaults.items():
        conn.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', (k, v))
    # Starter stock only when the table is empty.
    if conn.execute('SELECT COUNT(*) FROM inventory_items').fetchone()[0] == 0:
        starter = [
            ('RAW-BEEF', 'Beef / Steak', 'kg', 18, 5, 1200),
            ('RAW-BUN', 'Burger Buns', 'unit', 42, 10, 25),
            ('RAW-CHICKEN', 'Chicken', 'kg', 24, 6, 650),
            ('RAW-LETTUCE', 'Lettuce', 'kg', 9, 3, 180),
            ('RAW-FRIES', 'French Fries', 'kg', 30, 8, 220),
            ('RAW-WATER', 'Mineral Water', 'bottle', 80, 20, 55),
            ('RAW-COLA', 'Coca-Cola', 'bottle', 65, 20, 95),
            ('RAW-DESSERT', 'Dessert Portions', 'portion', 35, 8, 180),
        ]
        conn.executemany('INSERT INTO inventory_items(sku,name,unit,current_stock,min_stock,cost_price) VALUES (?,?,?,?,?,?)', starter)
        item_ids = {r['sku']: r['id'] for r in conn.execute('SELECT id,sku FROM inventory_items').fetchall()}
        recipes = [
            (3, item_ids['RAW-BEEF'], 0.18), (3, item_ids['RAW-BUN'], 1),
            (3, item_ids['RAW-LETTUCE'], 0.03), (3, item_ids['RAW-FRIES'], 0.15),
            (4, item_ids['RAW-CHICKEN'], 0.25), (4, item_ids['RAW-FRIES'], 0.18),
            (4, item_ids['RAW-LETTUCE'], 0.03), (6, item_ids['RAW-COLA'], 1),
            (7, item_ids['RAW-WATER'], 1), (8, item_ids['RAW-DESSERT'], 1),
            (9, item_ids['RAW-DESSERT'], 1),
        ]
        conn.executemany('INSERT INTO menu_recipes(menu_item_id,inventory_item_id,quantity) VALUES (?,?,?)', recipes)
    conn.commit()
    conn.close()


def rowdict(row):
    return dict(row) if row else None


def qrows(sql, params=()):
    conn = db(); rows = conn.execute(sql, params).fetchall(); conn.close(); return [rowdict(r) for r in rows]


def log(action, entity, entity_id=None, details=''):
    try:
        conn = db(); conn.execute(
            'INSERT INTO audit_logs(action,entity,entity_id,details) VALUES (?,?,?,?)',
            (action, entity, entity_id, details)
        ); conn.commit(); conn.close()
    except Exception:
        logging.exception('audit log failed')


def parse_body(handler):
    length = int(handler.headers.get('Content-Length', '0'))
    if length > 2_000_000:
        raise ValueError('Request body too large')
    raw = handler.rfile.read(length) if length else b'{}'
    if not raw:
        return {}
    return json.loads(raw.decode('utf-8'))


def get_settings():
    return {r['key']: r['value'] for r in qrows('SELECT key,value FROM settings')}


def current_user(handler):
    token = handler.headers.get('X-Session-Token', '')
    session = SESSIONS.get(token)
    if not session:
        return None
    if session['expires_at'] <= datetime.now(timezone.utc):
        SESSIONS.pop(token, None)
        return None
    return session


def clean_expired_sessions():
    now = datetime.now(timezone.utc)
    for token, session in list(SESSIONS.items()):
        if session['expires_at'] <= now:
            SESSIONS.pop(token, None)


def release_table_if_done(conn, table_id):
    if not table_id:
        return
    active = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE table_id=? AND status IN ('open','preparing','ready')",
        (table_id,)
    ).fetchone()[0]
    if active == 0:
        conn.execute("UPDATE dining_tables SET status='available', updated_at=CURRENT_TIMESTAMP WHERE id=?", (table_id,))


def check_recipe_stock(conn, order_id):
    required = {}
    items = conn.execute('SELECT menu_item_id,quantity FROM order_items WHERE order_id=?', (order_id,)).fetchall()
    for item in items:
        recipes = conn.execute('SELECT inventory_item_id,quantity FROM menu_recipes WHERE menu_item_id=?', (item['menu_item_id'],)).fetchall()
        for recipe in recipes:
            required[recipe['inventory_item_id']] = required.get(recipe['inventory_item_id'], Decimal('0')) + money_decimal(recipe['quantity']) * int(item['quantity'])
    for iid, qty in required.items():
        row = conn.execute('SELECT name,current_stock FROM inventory_items WHERE id=?', (iid,)).fetchone()
        if row and money_decimal(row['current_stock']) < qty:
            raise ValueError(f'Insufficient stock for {row["name"]}: need {qty}, have {money_decimal(row["current_stock"])}')


def apply_recipe_consumption(conn, order_id):
    already = conn.execute(
        "SELECT 1 FROM stock_movements WHERE reference_type='order' AND reference_id=? AND movement_type='sale' LIMIT 1",
        (order_id,)
    ).fetchone()
    if already:
        return
    items = conn.execute('''
        SELECT oi.menu_item_id, oi.quantity
        FROM order_items oi WHERE oi.order_id=?
    ''', (order_id,)).fetchall()
    for item in items:
        recipes = conn.execute('''
            SELECT inventory_item_id, quantity FROM menu_recipes WHERE menu_item_id=?
        ''', (item['menu_item_id'],)).fetchall()
        for recipe in recipes:
            qty = money_decimal(recipe['quantity']) * int(item['quantity'])
            inv = conn.execute('SELECT current_stock,cost_price FROM inventory_items WHERE id=?', (recipe['inventory_item_id'],)).fetchone()
            if not inv:
                continue
            new_stock = money_decimal(inv['current_stock']) - qty
            conn.execute('UPDATE inventory_items SET current_stock=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (float(new_stock), recipe['inventory_item_id']))
            conn.execute('''INSERT INTO stock_movements
                (inventory_item_id,movement_type,quantity,unit_cost,reference_type,reference_id,notes)
                VALUES (?,?,?,?,?,?,?)''', (
                recipe['inventory_item_id'], 'sale', -float(qty), float(inv['cost_price']), 'order', order_id, 'Automatic recipe consumption'
            ))


class Handler(BaseHTTPRequestHandler):
    server_version = 'RestaurantOS/2.2'

    def log_message(self, fmt, *args):
        logging.info('%s - %s', self.address_string(), fmt % args)

    def send_json(self, data, status=200, extra_headers=None):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path, download_name=None):
        if not path.exists() or not path.is_file():
            self.send_error(404); return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text/') or ctype == 'application/javascript' else ''))
        self.send_header('Content-Length', str(len(data)))
        if download_name:
            self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def require_auth(self):
        clean_expired_sessions()
        user = current_user(self)
        if not user:
            self.send_json({'ok': False, 'error': 'Authentication required'}, 401)
            return None
        return user

    def require_role(self, *roles):
        user = self.require_auth()
        if not user:
            return None
        if user['role'] not in roles:
            self.send_json({'ok': False, 'error': 'Permission denied'}, 403)
            return None
        return user

    def do_HEAD(self):
        parsed=urlparse(self.path); path=parsed.path
        if path.startswith('/static/'):
            rel=Path(path[len('/static/'):])
            if '..' in rel.parts:
                self.send_error(403); return
            target=STATIC/rel
        elif path in ('/','/index.html'):
            target=FRONTEND/'index.html'
        else:
            self.send_error(404); return
        if not target.exists() or not target.is_file():
            self.send_error(404); return
        self.send_response(200)
        ctype=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(target.stat().st_size))
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urlparse(self.path); path = parsed.path; qs = parse_qs(parsed.query)
            if path == '/api/health':
                self.send_json({'ok': True, 'app': 'RestaurantOS', 'version': '2.2', 'backend': 'python', 'database': 'sqlite'}); return
            if path.startswith('/static/'):
                rel = Path(path[len('/static/'):])
                if '..' in rel.parts:
                    self.send_error(403); return
                self.send_file(STATIC / rel); return
            if path in ('/', '/index.html'):
                self.send_file(FRONTEND / 'index.html'); return
            user = self.require_auth()
            if not user: return

            if path == '/api/me':
                self.send_json({'ok': True, 'user': {k:user[k] for k in ('id','email','display_name','role')}}); return
            if path == '/api/settings':
                self.send_json({'ok': True, 'settings': get_settings()}); return
            if path == '/api/menu':
                categories = qrows('SELECT * FROM categories WHERE active=1 ORDER BY sort_order,name')
                items = qrows('''SELECT m.*,c.name category_name FROM menu_items m LEFT JOIN categories c ON c.id=m.category_id
                                WHERE m.active=1 ORDER BY COALESCE(c.sort_order,999),m.name''')
                self.send_json({'ok': True, 'categories': categories, 'items': items}); return
            if path == '/api/menu/all':
                if user['role'] not in ('admin', 'manager'):
                    self.send_json({'ok': False, 'error': 'Permission denied'}, 403); return
                categories = qrows('SELECT * FROM categories ORDER BY sort_order,name')
                items = qrows('''SELECT m.*,c.name category_name FROM menu_items m LEFT JOIN categories c ON c.id=m.category_id
                                ORDER BY m.active DESC,COALESCE(c.sort_order,999),m.name''')
                self.send_json({'ok': True, 'categories': categories, 'items': items}); return
            if path == '/api/tables':
                self.send_json({'ok': True, 'tables': qrows('SELECT * FROM dining_tables ORDER BY CAST(REPLACE(table_no,"T","") AS INTEGER),table_no')}); return
            if path == '/api/orders':
                limit = min(max(int((qs.get('limit') or ['100'])[0]), 1), 500)
                status = (qs.get('status') or [''])[0]
                params = []
                where = ''
                if status:
                    where = 'WHERE o.status=?'; params.append(status)
                rows = qrows(f'''SELECT o.*,t.table_no,COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.order_id=o.id),0) paid_amount
                                FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id {where}
                                ORDER BY o.id DESC LIMIT {limit}''', params)
                conn = db()
                for r in rows:
                    r['items'] = [rowdict(x) for x in conn.execute('''SELECT oi.*,m.name FROM order_items oi JOIN menu_items m ON m.id=oi.menu_item_id WHERE oi.order_id=? ORDER BY oi.id''', (r['id'],)).fetchall()]
                conn.close()
                self.send_json({'ok': True, 'orders': rows}); return
            if path == '/api/dashboard':
                period = (qs.get('period') or ['today'])[0]
                start_sql = "date('now')" if period == 'today' else ("date('now','-6 day')" if period == '7d' else "date('now','-29 day')")
                conn = db()
                stats = {
                    'today_orders': conn.execute(f"SELECT COUNT(*) FROM orders WHERE date(created_at)>={start_sql}").fetchone()[0],
                    'open_orders': conn.execute("SELECT COUNT(*) FROM orders WHERE status IN ('open','preparing','ready')").fetchone()[0],
                    'today_sales': conn.execute(f"SELECT COALESCE(SUM(total),0) FROM orders WHERE status='paid' AND date(created_at)>={start_sql}").fetchone()[0],
                    'today_expenses': conn.execute(f"SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date(expense_date)>={start_sql}").fetchone()[0],
                    'available_tables': conn.execute("SELECT COUNT(*) FROM dining_tables WHERE status='available'").fetchone()[0],
                    'occupied_tables': conn.execute("SELECT COUNT(*) FROM dining_tables WHERE status='occupied'").fetchone()[0],
                    'reserved_tables': conn.execute("SELECT COUNT(*) FROM dining_tables WHERE status='reserved'").fetchone()[0],
                    'low_stock': conn.execute("SELECT COUNT(*) FROM inventory_items WHERE active=1 AND current_stock<=min_stock").fetchone()[0],
                    'customers': conn.execute("SELECT COUNT(*) FROM customers WHERE active=1").fetchone()[0],
                    'cash_open': conn.execute("SELECT COUNT(*) FROM cash_sessions WHERE status='open'").fetchone()[0],
                }
                recent = conn.execute("SELECT o.id,o.status,o.total,o.created_at,t.table_no FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id ORDER BY o.id DESC LIMIT 8").fetchall()
                top = conn.execute(f'''SELECT m.name,SUM(oi.quantity) qty,SUM(oi.line_total) sales FROM order_items oi
                    JOIN orders o ON o.id=oi.order_id JOIN menu_items m ON m.id=oi.menu_item_id
                    WHERE o.status='paid' AND date(o.created_at)>={start_sql} GROUP BY oi.menu_item_id ORDER BY qty DESC LIMIT 5''').fetchall()
                conn.close()
                self.send_json({'ok': True, 'stats': stats, 'recent': [rowdict(x) for x in recent], 'top_items': [rowdict(x) for x in top], 'period': period}); return
            if path == '/api/inventory':
                inv = qrows('SELECT *,(current_stock<=min_stock) low_stock FROM inventory_items WHERE active=1 ORDER BY low_stock DESC,name')
                self.send_json({'ok': True, 'items': inv}); return
            if path == '/api/inventory/movements':
                item_id = (qs.get('item_id') or [''])[0]
                params=[]; where=''
                if item_id:
                    where='WHERE sm.inventory_item_id=?'; params=[int(item_id)]
                moves=qrows(f'''SELECT sm.*,ii.name item_name,ii.unit FROM stock_movements sm JOIN inventory_items ii ON ii.id=sm.inventory_item_id {where} ORDER BY sm.id DESC LIMIT 200''',params)
                self.send_json({'ok': True, 'movements': moves}); return
            if path == '/api/suppliers':
                self.send_json({'ok': True, 'suppliers': qrows('SELECT * FROM suppliers WHERE active=1 ORDER BY name')}); return
            if path == '/api/purchases':
                purchases=qrows('''SELECT p.*,s.name supplier_name FROM purchases p LEFT JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.id DESC LIMIT 100''')
                self.send_json({'ok': True, 'purchases': purchases}); return
            if path == '/api/expenses':
                expenses=qrows('SELECT * FROM expenses ORDER BY expense_date DESC,id DESC LIMIT 200')
                self.send_json({'ok': True, 'expenses': expenses}); return
            if path == '/api/payments':
                payments=qrows('''SELECT p.*,o.customer_name FROM payments p LEFT JOIN orders o ON o.id=p.order_id ORDER BY p.id DESC LIMIT 200''')
                self.send_json({'ok': True, 'payments': payments}); return
            if path == '/api/reports/summary':
                start=(qs.get('from') or [datetime.now().strftime('%Y-%m-01')])[0]
                end=(qs.get('to') or [datetime.now().strftime('%Y-%m-%d')])[0]
                conn=db()
                sales=conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status='paid' AND date(created_at) BETWEEN ? AND ?",(start,end)).fetchone()[0]
                expenses=conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date(expense_date) BETWEEN ? AND ?",(start,end)).fetchone()[0]
                purchases=conn.execute("SELECT COALESCE(SUM(total),0) FROM purchases WHERE date(purchased_at) BETWEEN ? AND ?",(start,end)).fetchone()[0]
                cogs = conn.execute("""SELECT COALESCE(SUM(oi.quantity * mr.quantity * ii.cost_price),0)
                    FROM order_items oi
                    JOIN orders o ON o.id=oi.order_id
                    JOIN menu_recipes mr ON mr.menu_item_id=oi.menu_item_id
                    JOIN inventory_items ii ON ii.id=mr.inventory_item_id
                    WHERE o.status='paid' AND date(o.created_at) BETWEEN ? AND ?""",(start,end)).fetchone()[0]
                order_count=conn.execute("SELECT COUNT(*) FROM orders WHERE date(created_at) BETWEEN ? AND ?",(start,end)).fetchone()[0]
                methods=[rowdict(x) for x in conn.execute("SELECT payment_method,SUM(amount) total,COUNT(*) count FROM payments WHERE date(paid_at) BETWEEN ? AND ? GROUP BY payment_method ORDER BY total DESC",(start,end)).fetchall()]
                daily=[rowdict(x) for x in conn.execute("SELECT date(created_at) day,SUM(total) sales,COUNT(*) orders FROM orders WHERE status='paid' AND date(created_at) BETWEEN ? AND ? GROUP BY day ORDER BY day",(start,end)).fetchall()]
                top=[rowdict(x) for x in conn.execute("SELECT m.name,SUM(oi.quantity) qty,SUM(oi.line_total) sales FROM order_items oi JOIN orders o ON o.id=oi.order_id JOIN menu_items m ON m.id=oi.menu_item_id WHERE o.status='paid' AND date(o.created_at) BETWEEN ? AND ? GROUP BY m.id ORDER BY sales DESC LIMIT 10",(start,end)).fetchall()]
                conn.close()
                gross_profit = money_float(Decimal(str(sales))-Decimal(str(cogs)))
                operating_profit = money_float(Decimal(str(sales))-Decimal(str(cogs))-Decimal(str(expenses)))
                net_view = money_float(Decimal(str(sales))-Decimal(str(expenses))-Decimal(str(purchases)))
                self.send_json({'ok':True,'from':start,'to':end,'summary':{'sales':sales,'expenses':expenses,'purchases':purchases,'cogs':cogs,'gross_profit':gross_profit,'operating_profit':operating_profit,'orders':order_count,'net':net_view},'methods':methods,'daily':daily,'top_items':top}); return
            if path == '/api/kitchen':
                orders=qrows("SELECT o.*,t.table_no,COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.order_id=o.id),0) paid_amount FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id WHERE o.status IN ('open','preparing','ready') ORDER BY CASE o.status WHEN 'ready' THEN 1 WHEN 'preparing' THEN 2 ELSE 3 END,o.id ASC")
                conn=db()
                for r in orders:
                    r['items']=[rowdict(x) for x in conn.execute("SELECT oi.*,m.name FROM order_items oi JOIN menu_items m ON m.id=oi.menu_item_id WHERE oi.order_id=? ORDER BY oi.id",(r['id'],)).fetchall()]
                conn.close(); self.send_json({'ok':True,'orders':orders}); return
            if path == '/api/customers':
                self.send_json({'ok':True,'customers':qrows("SELECT c.*,COUNT(o.id) order_count,COALESCE(SUM(CASE WHEN o.status='paid' THEN o.total END),0) spent FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.active=1 GROUP BY c.id ORDER BY c.name")}); return
            if path == '/api/reservations':
                day=(qs.get('date') or [datetime.now().strftime('%Y-%m-%d')])[0]
                self.send_json({'ok':True,'reservations':qrows("SELECT r.*,c.name customer_name,c.phone,d.table_no FROM reservations r LEFT JOIN customers c ON c.id=r.customer_id LEFT JOIN dining_tables d ON d.id=r.table_id WHERE r.reservation_date=? ORDER BY r.reservation_time,r.id",(day,)),'date':day}); return
            if path == '/api/cash':
                conn=db(); current=conn.execute("SELECT * FROM cash_sessions WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
                today=conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_method='cash' AND date(paid_at)=date('now')").fetchone()[0]
                expenses_cash=conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE paid=1 AND date(expense_date)=date('now')").fetchone()[0]
                if current:
                    sales_since=conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_method='cash' AND paid_at>=?",(current['opened_at'],)).fetchone()[0]
                    expenses_since=conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE paid=1 AND created_at>=?",(current['opened_at'],)).fetchone()[0]
                    expected=money_decimal(current['opening_amount'])+money_decimal(sales_since)-money_decimal(expenses_since)
                else:
                    expected=money_decimal(today)-money_decimal(expenses_cash)
                conn.close(); payload={'current':rowdict(current),'today_cash_sales':today,'today_cash_expenses':expenses_cash,'expected_cash':money_float(expected)}; self.send_json({'ok':True,**payload}); return
            if path == '/api/users':
                if user['role']!='admin': self.send_json({'ok':False,'error':'Permission denied'},403); return
                self.send_json({'ok':True,'users':qrows('SELECT id,email,display_name,role,active,created_at,updated_at FROM users ORDER BY display_name')}); return
            if path == '/api/recipes':
                mid=(qs.get('menu_item_id') or [''])[0]
                if mid:
                    self.send_json({'ok':True,'recipes':qrows("SELECT mr.*,ii.name item_name,ii.unit FROM menu_recipes mr JOIN inventory_items ii ON ii.id=mr.inventory_item_id WHERE mr.menu_item_id=? ORDER BY ii.name",(int(mid),))}); return
                self.send_json({'ok':True,'recipes':qrows("SELECT mr.*,m.name menu_name,ii.name item_name,ii.unit FROM menu_recipes mr JOIN menu_items m ON m.id=mr.menu_item_id JOIN inventory_items ii ON ii.id=mr.inventory_item_id ORDER BY m.name,ii.name")}); return
            if path == '/api/debts':
                debts=qrows("SELECT *,CASE WHEN paid_amount>=amount THEN 'paid' WHEN paid_amount>0 THEN 'partial' ELSE status END calculated_status,(amount-paid_amount) remaining FROM debts WHERE status!='archived' ORDER BY CASE calculated_status WHEN 'overdue' THEN 1 WHEN 'open' THEN 2 WHEN 'partial' THEN 3 ELSE 4 END,due_date,id DESC")
                self.send_json({'ok':True,'debts':debts}); return
            if path == '/api/audit':
                logs=qrows('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 200')
                self.send_json({'ok':True,'logs':logs}); return
            if path == '/api/backup':
                if user['role'] != 'admin': self.send_json({'ok':False,'error':'Permission denied'},403); return
                ts=datetime.now().strftime('%Y%m%d_%H%M%S')
                target=BACKUP_DIR/f'restaurantos_{ts}.db'
                conn=db(); conn.execute('PRAGMA wal_checkpoint(FULL)'); conn.close()
                shutil.copy2(DB_PATH,target)
                log('BACKUP','database',details=target.name)
                self.send_json({'ok':True,'filename':target.name,'url':'/api/backup/download?file='+target.name}); return
            if path == '/api/backup/download':
                if user['role'] != 'admin': self.send_json({'ok':False,'error':'Permission denied'},403); return
                name=(qs.get('file') or [''])[0]
                if not re.fullmatch(r'restaurantos_\d{8}_\d{6}\.db',name): self.send_json({'ok':False,'error':'Invalid backup name'},400); return
                target=BACKUP_DIR/name
                self.send_file(target, download_name=name); return
            self.send_error(404)
        except Exception as exc:
            logging.exception('GET error')
            self.send_json({'ok': False, 'error': str(exc)}, 500)

    def do_POST(self):
        conn=None
        try:
            path=urlparse(self.path).path; body=parse_body(self)
            if path == '/api/login':
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password',''))
                if not email or not password:
                    self.send_json({'ok':False,'error':'Enter email and password'},400); return
                conn=db(); user=conn.execute('SELECT id,email,display_name,password_hash,role,active FROM users WHERE email=?', (email,)).fetchone(); conn.close()
                if not user or not user['active'] or not verify_password(password,user['password_hash']):
                    self.send_json({'ok':False,'error':'Invalid email or password'},401); return
                token=secrets.token_urlsafe(32); SESSIONS[token]={'id':user['id'],'email':user['email'],'display_name':user['display_name'],'role':user['role'],'expires_at':datetime.now(timezone.utc)+SESSION_TTL}
                log('LOGIN','user',user['id'],user['email']); self.send_json({'ok':True,'token':token,'user':{k:user[k] for k in ('id','email','display_name','role')}}); return
            user=self.require_auth()
            if not user:return
            if path == '/api/logout':
                token=self.headers.get('X-Session-Token',''); SESSIONS.pop(token,None); log('LOGOUT','user',user['id']); self.send_json({'ok':True}); return
            if path == '/api/cash/open':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                opening=money_decimal(body.get('opening_amount',0)); conn=db();
                if conn.execute("SELECT 1 FROM cash_sessions WHERE status='open' LIMIT 1").fetchone(): conn.close(); self.send_json({'ok':False,'error':'A cash session is already open'},409); return
                cur=conn.execute('INSERT INTO cash_sessions(opened_by,opening_amount,notes,status) VALUES (?,?,?,?)',(user['id'],float(opening),str(body.get('notes','')),'open'))
                sid=cur.lastrowid; conn.commit(); conn.close(); log('OPEN','cash_session',sid,str(opening)); self.send_json({'ok':True,'id':sid,'opening_amount':float(opening)}); return
            if path == '/api/cash/close':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                closing=money_decimal(body.get('closing_amount',0)); conn=db(); row=conn.execute("SELECT * FROM cash_sessions WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
                if not row: conn.close(); self.send_json({'ok':False,'error':'No open cash session'},409); return
                cash_sales=money_decimal(conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE payment_method='cash' AND paid_at>=?",(row['opened_at'],)).fetchone()[0]); cash_exp=money_decimal(conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE paid=1 AND created_at>=?",(row['opened_at'],)).fetchone()[0]); expected=money_decimal(row['opening_amount'])+cash_sales-cash_exp; diff=closing-expected
                conn.execute("UPDATE cash_sessions SET closing_amount=?,closed_at=CURRENT_TIMESTAMP,status='closed',notes=? WHERE id=?",(float(closing),str(body.get('notes','')),row['id'])); conn.commit(); conn.close(); log('CLOSE','cash_session',row['id'],f'expected={expected};closing={closing};difference={diff}'); self.send_json({'ok':True,'expected':float(expected),'difference':float(diff)}); return
            if path == '/api/categories':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                name=str(body.get('name','')).strip();
                if not name:self.send_json({'ok':False,'error':'Category name is required'},400); return
                conn=db(); cur=conn.execute('INSERT INTO categories(name,sort_order,active) VALUES (?,?,1)',(name,int(body.get('sort_order',0) or 0))); cid=cur.lastrowid; conn.commit(); conn.close(); log('CREATE','category',cid,name); self.send_json({'ok':True,'id':cid}); return
            if path == '/api/categories/delete':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                cid=int(body['id']); conn=db(); conn.execute('UPDATE categories SET active=0 WHERE id=?',(cid,)); conn.commit(); conn.close(); log('ARCHIVE','category',cid); self.send_json({'ok':True}); return
            if path == '/api/recipes':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                mid=int(body['menu_item_id']); iid=int(body['inventory_item_id']); qty=money_decimal(body.get('quantity',0));
                if qty<=0:self.send_json({'ok':False,'error':'Recipe quantity must be greater than zero'},400); return
                conn=db(); conn.execute('INSERT INTO menu_recipes(menu_item_id,inventory_item_id,quantity) VALUES (?,?,?) ON CONFLICT(menu_item_id,inventory_item_id) DO UPDATE SET quantity=excluded.quantity',(mid,iid,float(qty))); conn.commit(); conn.close(); log('UPSERT','recipe',mid,f'{iid}:{qty}'); self.send_json({'ok':True}); return
            if path == '/api/recipes/delete':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                rid=int(body['id']); conn=db(); conn.execute('DELETE FROM menu_recipes WHERE id=?',(rid,)); conn.commit(); conn.close(); log('DELETE','recipe',rid); self.send_json({'ok':True}); return
            if path == '/api/customers':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                name=str(body.get('name','')).strip();
                if not name:self.send_json({'ok':False,'error':'Customer name is required'},400); return
                cid=body.get('id'); conn=db()
                if cid: conn.execute('UPDATE customers SET name=?,phone=?,email=?,notes=?,active=? WHERE id=?',(name,str(body.get('phone','')),str(body.get('email','')),str(body.get('notes','')),1 if body.get('active',True) else 0,int(cid)))
                else: cid=conn.execute('INSERT INTO customers(name,phone,email,notes) VALUES (?,?,?,?)',(name,str(body.get('phone','')),str(body.get('email','')),str(body.get('notes','')))).lastrowid
                conn.commit(); conn.close(); log('UPSERT','customer',int(cid),name); self.send_json({'ok':True,'id':int(cid)}); return
            if path == '/api/customers/delete':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                cid=int(body['id']); conn=db(); conn.execute('UPDATE customers SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?',(cid,)); conn.commit(); conn.close(); log('ARCHIVE','customer',cid); self.send_json({'ok':True}); return
            if path == '/api/reservations':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                date=str(body.get('reservation_date','')).strip(); time=str(body.get('reservation_time','')).strip(); guests=int(body.get('guests',2) or 2);
                if not date or not time or guests<1:self.send_json({'ok':False,'error':'Date, time and guests are required'},400); return
                conn=db(); rid=body.get('id')
                if rid: conn.execute('UPDATE reservations SET customer_id=?,table_id=?,reservation_date=?,reservation_time=?,guests=?,status=?,notes=? WHERE id=?',(body.get('customer_id') or None,body.get('table_id') or None,date,time,guests,str(body.get('status','booked')),str(body.get('notes','')),int(rid)))
                else: rid=conn.execute('INSERT INTO reservations(customer_id,table_id,reservation_date,reservation_time,guests,status,notes) VALUES (?,?,?,?,?,?,?)',(body.get('customer_id') or None,body.get('table_id') or None,date,time,guests,str(body.get('status','booked')),str(body.get('notes','')))).lastrowid
                conn.commit(); conn.close(); log('UPSERT','reservation',int(rid),f'{date} {time}'); self.send_json({'ok':True,'id':int(rid)}); return
            if path == '/api/reservations/status':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                rid=int(body['id']); status=str(body['status']);
                if status not in ('booked','confirmed','seated','completed','cancelled','no-show'):self.send_json({'ok':False,'error':'Invalid reservation status'},400); return
                conn=db(); conn.execute('UPDATE reservations SET status=? WHERE id=?',(status,rid)); conn.commit(); conn.close(); log('UPDATE','reservation',rid,status); self.send_json({'ok':True}); return
            if path == '/api/tables':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                no=str(body.get('table_no','')).strip(); seats=int(body.get('seats',4) or 4); status=str(body.get('status','available'))
                if not no or seats<1 or status not in ALLOWED_TABLE_STATUS:self.send_json({'ok':False,'error':'Valid table number, seats and status are required'},400); return
                conn=db(); tid=body.get('id')
                if tid: conn.execute('UPDATE dining_tables SET table_no=?,seats=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(no,seats,status,int(tid)))
                else: tid=conn.execute('INSERT INTO dining_tables(table_no,seats,status) VALUES (?,?,?)',(no,seats,status)).lastrowid
                conn.commit(); conn.close(); log('UPSERT','table',int(tid),no); self.send_json({'ok':True,'id':int(tid)}); return
            if path == '/api/tables/delete':
                if user['role']!='admin': self.send_json({'ok':False,'error':'Permission denied'},403); return
                tid=int(body['id']); conn=db(); conn.execute("UPDATE dining_tables SET status='cleaning',updated_at=CURRENT_TIMESTAMP WHERE id=?",(tid,)); conn.commit(); conn.close(); log('ARCHIVE','table',tid); self.send_json({'ok':True}); return
            if path == '/api/users/update':
                if user['role']!='admin': self.send_json({'ok':False,'error':'Permission denied'},403); return
                uid=int(body['id']); role=str(body.get('role','cashier')); active=1 if body.get('active',True) else 0; name=str(body.get('display_name','')).strip();
                if role not in ('admin','manager','cashier','stock') or not name:self.send_json({'ok':False,'error':'Valid name and role are required'},400); return
                conn=db(); conn.execute('UPDATE users SET display_name=?,role=?,active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(name,role,active,uid));
                if body.get('password'): conn.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(str(body['password'])),uid))
                conn.commit();conn.close();log('UPDATE','user',uid,role);self.send_json({'ok':True});return
            if path == '/api/inventory/items/update':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                iid=int(body['id']); conn=db(); conn.execute('UPDATE inventory_items SET sku=?,name=?,unit=?,min_stock=?,cost_price=?,active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(str(body.get('sku','')).strip() or None,str(body.get('name','')).strip(),str(body.get('unit','unit')).strip() or 'unit',float(body.get('min_stock',0) or 0),money_float(body.get('cost_price',0)),1 if body.get('active',True) else 0,iid)); conn.commit();conn.close();log('UPDATE','inventory_item',iid);self.send_json({'ok':True});return
            if path == '/api/debts':
                if user['role'] not in ('admin','manager','cashier','stock'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                debt_type=str(body.get('debt_type','receivable')); party=str(body.get('party_name','')).strip(); amount=money_decimal(body.get('amount',0)); due=str(body.get('due_date','')).strip() or None
                if debt_type not in ('receivable','payable') or not party or amount<=0:self.send_json({'ok':False,'error':'Type, party and positive amount are required'},400);return
                conn=db(); did=body.get('id')
                if did:
                    conn.execute('UPDATE debts SET debt_type=?,party_name=?,reference=?,amount=?,due_date=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(debt_type,party,str(body.get('reference','')),float(amount),due,str(body.get('notes','')),int(did)))
                else:
                    did=conn.execute('INSERT INTO debts(debt_type,party_name,reference,amount,due_date,notes,status) VALUES (?,?,?,?,?,?,?)',(debt_type,party,str(body.get('reference','')),float(amount),due,str(body.get('notes','')),'open')).lastrowid
                conn.commit();conn.close();log('UPSERT','debt',int(did),party);self.send_json({'ok':True,'id':int(did)});return
            if path == '/api/debts/payment':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                did=int(body['id']); amount=money_decimal(body.get('amount',0));
                if amount<=0:self.send_json({'ok':False,'error':'Amount must be greater than zero'},400);return
                conn=db(); row=conn.execute('SELECT amount,paid_amount FROM debts WHERE id=?',(did,)).fetchone()
                if not row:conn.close();self.send_json({'ok':False,'error':'Debt not found'},404);return
                remaining=money_decimal(row['amount'])-money_decimal(row['paid_amount']); amount=min(amount,remaining)
                if amount<=0:conn.close();self.send_json({'ok':False,'error':'Debt is already paid'},409);return
                paid=money_decimal(row['paid_amount'])+amount; status='paid' if paid>=money_decimal(row['amount']) else 'partial'; conn.execute('UPDATE debts SET paid_amount=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(float(paid),status,did));conn.commit();conn.close();log('PAY','debt',did,str(amount));self.send_json({'ok':True,'paid_amount':float(paid),'remaining':float(money_decimal(row['amount'])-paid),'status':status});return
            if path == '/api/debts/archive':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                did=int(body['id']);conn=db();conn.execute("UPDATE debts SET status='archived',updated_at=CURRENT_TIMESTAMP WHERE id=?",(did,));conn.commit();conn.close();log('ARCHIVE','debt',did);self.send_json({'ok':True});return
            if path == '/api/settings':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                updates=body.get('settings',body)
                allowed={'restaurant_name','currency','tax_rate','phone','address','receipt_footer'}
                conn=db()
                for k,v in updates.items():
                    if k in allowed: conn.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
                conn.commit(); conn.close(); log('UPDATE','settings',details=','.join(k for k in updates if k in allowed)); self.send_json({'ok':True,'settings':get_settings()}); return
            if path == '/api/menu/items':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                item_id=body.get('id'); name=str(body.get('name','')).strip(); description=str(body.get('description','')).strip(); price=money_float(body.get('price',0)); category_id=body.get('category_id') or None; active=1 if body.get('active',True) else 0
                if not name or price < 0: self.send_json({'ok':False,'error':'Name and non-negative price are required'},400); return
                conn=db()
                if item_id:
                    conn.execute('UPDATE menu_items SET name=?,description=?,price=?,category_id=?,active=? WHERE id=?',(name,description,price,category_id,active,int(item_id))); entity_id=int(item_id); action='UPDATE'
                else:
                    cur=conn.execute('INSERT INTO menu_items(category_id,name,description,price,active) VALUES (?,?,?,?,?)',(category_id,name,description,price,active)); entity_id=cur.lastrowid; action='CREATE'
                conn.commit(); conn.close(); log(action,'menu_item',entity_id,name); self.send_json({'ok':True,'id':entity_id}); return
            if path == '/api/menu/items/delete':
                if user['role'] not in ('admin','manager'): self.send_json({'ok':False,'error':'Permission denied'},403); return
                item_id=int(body['id']); conn=db(); conn.execute('UPDATE menu_items SET active=0 WHERE id=?',(item_id,)); conn.commit(); conn.close(); log('ARCHIVE','menu_item',item_id); self.send_json({'ok':True}); return
            if path == '/api/tables/status':
                table_id=int(body['table_id']); status=str(body['status'])
                if status not in ALLOWED_TABLE_STATUS: self.send_json({'ok':False,'error':'Invalid table status'},400); return
                conn=db(); conn.execute('UPDATE dining_tables SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,table_id)); conn.commit(); conn.close(); log('UPDATE','table',table_id,status); self.send_json({'ok':True}); return
            if path == '/api/orders':
                table_id=body.get('table_id'); customer_name=str(body.get('customer_name','')).strip(); customer_id=body.get('customer_id') or None; discount=money_decimal(body.get('discount',0)); items=body.get('items',[])
                if table_id is not None: table_id=int(table_id)
                if customer_id is not None: customer_id=int(customer_id)
                if discount < 0: discount=Decimal('0')
                if not items:self.send_json({'ok':False,'error':'Order must contain items'},400);return
                conn=db(); tax_rate=money_decimal((conn.execute("SELECT value FROM settings WHERE key='tax_rate'").fetchone() or {'value':'0'})['value'])
                subtotal=Decimal('0'); valid=[]
                for item in items:
                    menu_id=int(item['menu_item_id']); qty=int(item.get('quantity',1));
                    if qty<1:continue
                    row=conn.execute('SELECT id,name,price FROM menu_items WHERE id=? AND active=1',(menu_id,)).fetchone()
                    if not row: raise ValueError(f'Menu item {menu_id} not found')
                    price=money_decimal(row['price']); line=price*qty; subtotal+=line; valid.append((menu_id,qty,price,line,str(item.get('notes',''))))
                if not valid: raise ValueError('Order must contain at least one valid item')
                discount=min(discount,subtotal); taxable=max(Decimal('0'),subtotal-discount); tax=(taxable*tax_rate/Decimal('100')).quantize(Decimal('0.01'),ROUND_HALF_UP); total=(taxable+tax).quantize(Decimal('0.01'),ROUND_HALF_UP)
                cur=conn.execute('INSERT INTO orders(table_id,customer_name,customer_id,status,subtotal,discount,tax,total) VALUES (?,?,?,?,?,?,?,?)',(table_id,customer_name,customer_id,'open',float(subtotal),float(discount),float(tax),float(total))); order_id=cur.lastrowid
                for menu_id,qty,price,line,notes in valid:
                    conn.execute('INSERT INTO order_items(order_id,menu_item_id,quantity,unit_price,line_total,notes) VALUES (?,?,?,?,?,?)',(order_id,menu_id,qty,float(price),float(line),notes))
                if table_id: conn.execute("UPDATE dining_tables SET status='occupied',updated_at=CURRENT_TIMESTAMP WHERE id=?",(table_id,))
                conn.commit(); conn.close(); log('CREATE','order',order_id,customer_name); self.send_json({'ok':True,'order_id':order_id,'subtotal':float(subtotal),'discount':float(discount),'tax':float(tax),'total':float(total)}); return
            if path == '/api/orders/status':
                order_id=int(body['order_id']); status=str(body['status'])
                if status not in ALLOWED_ORDER_STATUS:self.send_json({'ok':False,'error':'Invalid status'},400);return
                conn=db(); row=conn.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
                if not row: conn.close(); self.send_json({'ok':False,'error':'Order not found'},404); return
                transitions={'open':{'preparing','cancelled'},'preparing':{'ready','cancelled'},'ready':{'paid','cancelled'},'paid':set(),'cancelled':set()}
                if status != row['status'] and status not in transitions.get(row['status'],set()):
                    conn.close(); self.send_json({'ok':False,'error':f'Cannot move order from {row["status"]} to {status}'},409); return
                conn.execute('UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,order_id))
                if status in ('paid','cancelled'):
                    release_table_if_done(conn,row['table_id'])
                if status == 'paid':
                    existing=conn.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE order_id=?',(order_id,)).fetchone()[0]
                    if money_decimal(existing) < money_decimal(row['total']):
                        missing=money_decimal(row['total'])-money_decimal(existing)
                        conn.execute('INSERT INTO payments(order_id,payment_method,amount,reference,notes) VALUES (?,?,?,?,?)',(order_id,'cash',float(missing),'AUTO','Auto payment on paid status'))
                    check_recipe_stock(conn,order_id); apply_recipe_consumption(conn,order_id)
                conn.commit(); conn.close(); log('UPDATE','order',order_id,status); self.send_json({'ok':True}); return
            if path == '/api/payments':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403);return
                order_id=body.get('order_id'); amount=money_decimal(body.get('amount',0)); method=str(body.get('payment_method','cash')).strip() or 'cash'
                if amount<=0:self.send_json({'ok':False,'error':'Amount must be greater than zero'},400);return
                conn=db()
                if order_id:
                    order=conn.execute('SELECT * FROM orders WHERE id=?',(int(order_id),)).fetchone()
                    if not order: conn.close();self.send_json({'ok':False,'error':'Order not found'},404);return
                    paid=money_decimal(conn.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE order_id=?',(int(order_id),)).fetchone()[0]); remaining=money_decimal(order['total'])-paid
                    if amount>remaining: amount=remaining
                    conn.execute('INSERT INTO payments(order_id,payment_method,amount,reference,notes) VALUES (?,?,?,?,?)',(int(order_id),method,str(amount),str(body.get('reference','')),str(body.get('notes',''))))
                    paid_after=paid+amount
                    if paid_after>=money_decimal(order['total']):
                        check_recipe_stock(conn,int(order_id)); conn.execute("UPDATE orders SET status='paid',updated_at=CURRENT_TIMESTAMP WHERE id=?",(int(order_id),)); release_table_if_done(conn,order['table_id']); apply_recipe_consumption(conn,int(order_id))
                else:
                    conn.execute('INSERT INTO payments(order_id,payment_method,amount,reference,notes) VALUES (?,?,?,?,?)',(None,method,float(amount),str(body.get('reference','')),str(body.get('notes',''))))
                pid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]; conn.commit();conn.close();log('CREATE','payment',pid,method);self.send_json({'ok':True,'id':pid,'amount':float(amount)});return
            if path == '/api/inventory/items':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403);return
                name=str(body.get('name','')).strip(); unit=str(body.get('unit','unit')).strip() or 'unit'; sku=str(body.get('sku','')).strip() or None
                if not name:self.send_json({'ok':False,'error':'Name is required'},400);return
                conn=db(); cur=conn.execute('INSERT INTO inventory_items(sku,name,unit,current_stock,min_stock,cost_price) VALUES (?,?,?,?,?,?)',(sku,name,unit,float(body.get('current_stock',0) or 0),float(body.get('min_stock',0) or 0),money_float(body.get('cost_price',0)))); iid=cur.lastrowid
                if float(body.get('current_stock',0) or 0): conn.execute('INSERT INTO stock_movements(inventory_item_id,movement_type,quantity,unit_cost,notes) VALUES (?,?,?,?,?)',(iid,'opening',float(body.get('current_stock',0) or 0),money_float(body.get('cost_price',0)),'Opening stock'))
                conn.commit();conn.close();log('CREATE','inventory_item',iid,name);self.send_json({'ok':True,'id':iid});return
            if path == '/api/inventory/adjust':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403);return
                iid=int(body['inventory_item_id']); qty=money_decimal(body.get('quantity',0)); movement=str(body.get('movement_type','adjustment')); notes=str(body.get('notes',''))
                if movement not in ('purchase','adjustment','waste','return') or qty==0:self.send_json({'ok':False,'error':'Invalid stock adjustment'},400);return
                signed=qty if movement in ('purchase','return') else -abs(qty) if movement=='waste' else qty
                conn=db(); row=conn.execute('SELECT current_stock,cost_price FROM inventory_items WHERE id=?',(iid,)).fetchone();
                if not row:conn.close();self.send_json({'ok':False,'error':'Inventory item not found'},404);return
                new=money_decimal(row['current_stock'])+signed
                if new<0:self.send_json({'ok':False,'error':'Stock cannot become negative'},400);conn.close();return
                conn.execute('UPDATE inventory_items SET current_stock=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(float(new),iid));conn.execute('INSERT INTO stock_movements(inventory_item_id,movement_type,quantity,unit_cost,notes) VALUES (?,?,?,?,?)',(iid,movement,float(signed),float(row['cost_price']),notes));conn.commit();conn.close();log('STOCK', 'inventory_item',iid,f'{movement}:{signed}');self.send_json({'ok':True,'stock':float(new)});return
            if path == '/api/purchases':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403);return
                supplier_id=body.get('supplier_id') or None; items=body.get('items') or []
                if not items:self.send_json({'ok':False,'error':'Purchase must contain items'},400);return
                conn=db(); subtotal=Decimal('0'); normalized=[]
                for x in items:
                    iid=int(x['inventory_item_id']); qty=money_decimal(x.get('quantity',0)); cost=money_decimal(x.get('unit_cost',0));
                    if qty<=0 or cost<0:continue
                    if not conn.execute('SELECT 1 FROM inventory_items WHERE id=?',(iid,)).fetchone():raise ValueError('Inventory item not found')
                    line=qty*cost;subtotal+=line;normalized.append((iid,qty,cost,line))
                if not normalized:raise ValueError('Purchase has no valid lines')
                cur=conn.execute('INSERT INTO purchases(supplier_id,invoice_no,status,subtotal,tax,total,purchased_at,notes) VALUES (?,?,?,?,?,?,?,?)',(supplier_id,str(body.get('invoice_no','')), 'received',float(subtotal),0,float(subtotal),str(body.get('purchased_at') or datetime.now().strftime('%Y-%m-%d')),str(body.get('notes','')))); pid=cur.lastrowid
                for iid,qty,cost,line in normalized:
                    conn.execute('INSERT INTO purchase_items(purchase_id,inventory_item_id,quantity,unit_cost,line_total) VALUES (?,?,?,?,?)',(pid,iid,float(qty),float(cost),float(line)))
                    conn.execute('UPDATE inventory_items SET current_stock=current_stock+?,cost_price=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(float(qty),float(cost),iid))
                    conn.execute('INSERT INTO stock_movements(inventory_item_id,movement_type,quantity,unit_cost,reference_type,reference_id,notes) VALUES (?,?,?,?,?,?,?)',(iid,'purchase',float(qty),float(cost),'purchase',pid,'Purchase receipt'))
                conn.commit();conn.close();log('CREATE','purchase',pid,str(body.get('invoice_no','')));self.send_json({'ok':True,'id':pid,'total':float(subtotal)});return
            if path == '/api/suppliers':
                if user['role'] not in ('admin','manager','stock'): self.send_json({'ok':False,'error':'Permission denied'},403);return
                name=str(body.get('name','')).strip();
                if not name:self.send_json({'ok':False,'error':'Supplier name is required'},400);return
                conn=db();cur=conn.execute('INSERT INTO suppliers(name,phone,email,notes) VALUES (?,?,?,?)',(name,str(body.get('phone','')),str(body.get('email','')),str(body.get('notes',''))));sid=cur.lastrowid;conn.commit();conn.close();log('CREATE','supplier',sid,name);self.send_json({'ok':True,'id':sid});return
            if path == '/api/expenses':
                if user['role'] not in ('admin','manager','cashier'): self.send_json({'ok':False,'error':'Permission denied'},403);return
                category=str(body.get('category','General')).strip() or 'General';description=str(body.get('description','')).strip();amount=money_decimal(body.get('amount',0));
                if not description or amount<=0:self.send_json({'ok':False,'error':'Description and positive amount are required'},400);return
                conn=db();cur=conn.execute('INSERT INTO expenses(category,description,amount,paid,expense_date,notes) VALUES (?,?,?,?,?,?)',(category,description,float(amount),1 if body.get('paid',True) else 0,str(body.get('expense_date') or datetime.now().strftime('%Y-%m-%d')),str(body.get('notes',''))));eid=cur.lastrowid;conn.commit();conn.close();log('CREATE','expense',eid,description);self.send_json({'ok':True,'id':eid});return
            if path == '/api/backup':
                if user['role'] != 'admin':self.send_json({'ok':False,'error':'Permission denied'},403);return
                ts=datetime.now().strftime('%Y%m%d_%H%M%S');target=BACKUP_DIR/f'restaurantos_{ts}.db'
                conn=db();conn.execute('PRAGMA wal_checkpoint(FULL)');conn.close();shutil.copy2(DB_PATH,target);log('BACKUP','database',details=target.name);self.send_json({'ok':True,'filename':target.name,'url':'/api/backup/download?file='+target.name});return
            if path == '/api/users':
                if user['role']!='admin':self.send_json({'ok':False,'error':'Permission denied'},403);return
                email=str(body.get('email','')).strip().lower();name=str(body.get('display_name','')).strip();password=str(body.get('password',''));role=str(body.get('role','cashier'))
                if not email or not name or len(password)<8 or role not in ('admin','manager','cashier','stock'):self.send_json({'ok':False,'error':'Valid email, name, 8+ character password and role are required'},400);return
                conn=db();cur=conn.execute('INSERT INTO users(email,display_name,password_hash,role) VALUES (?,?,?,?)',(email,name,hash_password(password),role));uid=cur.lastrowid;conn.commit();conn.close();log('CREATE','user',uid,email);self.send_json({'ok':True,'id':uid});return
            self.send_error(404)
        except sqlite3.IntegrityError as exc:
            if conn: conn.rollback()
            logging.exception('POST integrity error')
            self.send_json({'ok':False,'error':'Data conflict: '+str(exc)},409)
        except Exception as exc:
            if conn:
                try: conn.rollback()
                except Exception: pass
            logging.exception('POST error')
            message=str(exc)
            status=409 if message.startswith('Insufficient stock') else 500
            self.send_json({'ok':False,'error':message},status)
        finally:
            if conn:
                try: conn.close()
                except Exception: pass


def run():
    init_db()
    import os
    host=os.environ.get('RESTAURANTOS_HOST','127.0.0.1')
    port=int(os.environ.get('RESTAURANTOS_PORT','4000'))
    server=ThreadingHTTPServer((host,port),Handler)
    logging.info('RestaurantOS 2.1 starting at http://%s:%s',host,port)
    print(f'RestaurantOS 2.1 running at http://{host}:{port}')
    print('Demo login: admin@restaurantos.local / Admin@123')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__':
    run()
