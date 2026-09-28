import json
import secrets
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from money import WalletError, rounded


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, active_trip INTEGER, state TEXT);
                CREATE TABLE IF NOT EXISTS trips (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    origin TEXT NOT NULL, destination TEXT NOT NULL,
                    home TEXT NOT NULL, local TEXT NOT NULL, rate TEXT NOT NULL,
                    rate_source TEXT NOT NULL, initial_home TEXT NOT NULL,
                    initial_local TEXT NOT NULL, balance TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS expenses (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    trip_id INTEGER NOT NULL REFERENCES trips(id),
                    local_amount TEXT NOT NULL, home_amount TEXT NOT NULL, rate TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    confirmed_at TEXT);
                CREATE INDEX IF NOT EXISTS trips_owner ON trips(user_id, id);
                CREATE INDEX IF NOT EXISTS expense_history ON expenses(user_id, trip_id, status, confirmed_at);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def user(self, uid):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO users(id) VALUES (?)', (uid,))
            return dict(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())

    def state(self, uid):
        return json.loads(self.user(uid)['state'] or 'null')

    def set_state(self, uid, state):
        self.user(uid)
        with self.connection() as db:
            db.execute('UPDATE users SET state=? WHERE id=?',
                       (json.dumps(state, ensure_ascii=False) if state else None, uid))

    def trip(self, uid, tid=None):
        if tid is None:
            tid = self.user(uid)['active_trip']
        with self.connection() as db:
            row = db.execute('SELECT * FROM trips WHERE id=? AND user_id=?', (tid, uid)).fetchone()
            if not row:
                raise WalletError('Сначала создайте или выберите путешествие в меню.')
            return dict(row)

    def trips(self, uid, page=0):
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                'SELECT * FROM trips WHERE user_id=? ORDER BY id DESC LIMIT 9 OFFSET ?', (uid, page * 8))]

    def switch(self, uid, tid):
        self.trip(uid, tid)
        with self.connection() as db:
            db.execute('UPDATE users SET active_trip=?, state=NULL WHERE id=?', (tid, uid))

    def create(self, uid, state):
        # Consumes the draft in the same transaction, making repeated confirmation safe.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM users WHERE id=?', (uid,)).fetchone()
            if not row or not row['state'] or json.loads(row['state']) != state:
                raise WalletError('Это подтверждение устарело. Откройте меню.')
            cur = db.execute('''INSERT INTO trips
                (user_id,origin,destination,home,local,rate,rate_source,initial_home,initial_local,balance)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (uid, state['origin']['name'], state['destination']['name'], state['home'], state['local'],
                 state['rate'], state['rate_source'], state['initial_home'], state['initial_local'], state['initial_local']))
            tid = cur.lastrowid
            db.execute('UPDATE users SET active_trip=?,state=NULL WHERE id=?', (tid, uid))
            return tid

    def set_rate(self, uid, tid, rate):
        with self.connection() as db:
            cur = db.execute("UPDATE trips SET rate=?,rate_source='manual' WHERE id=? AND user_id=?",
                             (str(rate), tid, uid))
            if cur.rowcount != 1:
                raise WalletError('Путешествие не найдено.')
            db.execute('UPDATE users SET state=NULL WHERE id=?', (uid,))
            # Quotes calculated with an old rate must not silently be confirmed.
            db.execute("UPDATE expenses SET status='cancelled' WHERE trip_id=? AND user_id=? AND status='pending'", (tid, uid))

    def quote(self, uid, tid, value):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            trip = db.execute('SELECT * FROM trips WHERE id=? AND user_id=?', (tid, uid)).fetchone()
            if not trip:
                raise WalletError('Путешествие не найдено.')
            home = rounded(value / Decimal(trip['rate']), trip['home'])
            token = secrets.token_hex(8)
            db.execute('INSERT INTO expenses(id,user_id,trip_id,local_amount,home_amount,rate) VALUES (?,?,?,?,?,?)',
                       (token, uid, tid, str(value), str(home), trip['rate']))
            return token, home

    def confirm(self, uid, token, accept):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            expense = db.execute('SELECT * FROM expenses WHERE id=? AND user_id=?', (token, uid)).fetchone()
            if not expense or expense['status'] != 'pending':
                raise WalletError('Эта операция уже обработана или отменена. Повторного списания нет.')
            trip = db.execute('SELECT * FROM trips WHERE id=? AND user_id=?', (expense['trip_id'], uid)).fetchone()
            if accept:
                balance = Decimal(trip['balance']) - Decimal(expense['local_amount'])
                if balance < 0:
                    raise WalletError('Недостаточно средств. Расход не записан; введите меньшую сумму.')
                db.execute('UPDATE trips SET balance=? WHERE id=? AND user_id=?', (str(balance), trip['id'], uid))
            db.execute('UPDATE expenses SET status=?,confirmed_at=CURRENT_TIMESTAMP WHERE id=?',
                       ('confirmed' if accept else 'cancelled', token))
            return trip['id']

    def history(self, uid, tid, page=0):
        self.trip(uid, tid)
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM expenses
                WHERE user_id=? AND trip_id=? AND status='confirmed'
                ORDER BY confirmed_at DESC,rowid DESC LIMIT 9 OFFSET ?''', (uid, tid, page * 8))]
