from contextlib import contextmanager
from decimal import Decimal
import sqlite3
from cache.memory import Entry


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS currency_cache (
                    pair TEXT PRIMARY KEY, value TEXT NOT NULL,
                    updated_at REAL NOT NULL, rate_date TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rate_snapshot (
                    currency TEXT PRIMARY KEY, value TEXT NOT NULL,
                    updated_at REAL NOT NULL, rate_date TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                    text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS notes_owner ON notes(user_id, id);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self):
        with self.connection() as db:
            pairs = {r['pair']: Entry(Decimal(r['value']), r['updated_at'], r['rate_date'])
                     for r in db.execute('SELECT * FROM currency_cache')}
            snapshot = {r['currency']: Entry(Decimal(r['value']), r['updated_at'], r['rate_date'])
                        for r in db.execute('SELECT * FROM rate_snapshot')}
        return pairs, snapshot

    def save_snapshot(self, rates, updated_at, rate_date):
        with self.connection() as db:
            db.execute('DELETE FROM rate_snapshot')
            db.execute('DELETE FROM currency_cache')
            db.executemany('INSERT INTO rate_snapshot VALUES (?, ?, ?, ?)',
                           [(code, str(value), updated_at, rate_date) for code, value in rates.items()])

    def save_pair(self, pair, entry):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO currency_cache VALUES (?, ?, ?, ?)',
                       (pair, str(entry.value), entry.updated_at, entry.rate_date))

    def add_note(self, uid, text):
        with self.connection() as db:
            return db.execute('INSERT INTO notes(user_id, text) VALUES (?, ?)', (uid, text)).lastrowid

    def notes(self, uid, after=0, limit=10):
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                'SELECT * FROM notes WHERE user_id=? AND id>? ORDER BY id LIMIT ?',
                (uid, after, limit))]
