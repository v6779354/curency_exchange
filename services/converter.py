from decimal import Decimal, ROUND_HALF_UP
import logging
import re
import threading
import time
from cache.memory import Entry, MemoryCache
from errors import UserError


def parse_query(text):
    parts = text.upper().split()
    if len(parts) == 2:
        quantity, source, target = '1', *parts
    elif len(parts) == 3:
        quantity, source, target = parts
    else:
        raise UserError('Введите пару USD RUB или сумму: 100 USD RUB.')
    if not all(re.fullmatch('[A-Z]{3}', code) for code in (source, target)):
        raise UserError('Коды валют должны состоять из трёх латинских букв: USD RUB.')
    quantity = quantity.replace(',', '.')
    if len(quantity) > 24 or not re.fullmatch(r'\d+(?:\.\d{1,8})?', quantity):
        raise UserError('Введите положительную сумму, например 100 или 100,50.')
    value = Decimal(quantity)
    if not 0 < value <= Decimal('1e12'):
        raise UserError('Сумма должна быть больше нуля и не больше 1 000 000 000 000.')
    return value, source, target


def fmt(value, places=6):
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return format(rounded, 'f').rstrip('0').rstrip('.') if '.' in format(rounded, 'f') else str(rounded)


class Converter:
    def __init__(self, api, store, ttl=3600, clock=time.time):
        self.api, self.store, self.clock = api, store, clock
        pairs, self.snapshot = store.load()
        self.cache = MemoryCache(ttl, pairs)
        self.lock = threading.Lock()
        self.retry_after = 0
        self.last_error = None

    def rate(self, source, target):
        if not all(re.fullmatch('[A-Z]{3}', code) for code in (source, target)):
            raise UserError('Неверный код валюты.')
        pair = f'{source}_{target}'
        # One process owns the cache; lock also coalesces simultaneous misses.
        with self.lock:
            now = self.clock()
            entry = self.cache.get(pair, now)
            if entry:
                logging.info('cache_hit pair=%s', pair)
                return entry, 'кеш'
            logging.info('cache_miss pair=%s', pair)
            marker = self.snapshot.get('RUB')
            fresh = marker and 0 <= now - marker.updated_at < self.cache.ttl
            origin = 'кеш'
            if not fresh:
                if now < self.retry_after:
                    raise UserError(self.last_error)
                try:
                    rates, date = self.api.fetch()
                except UserError as exc:
                    self.last_error = str(exc)
                    self.retry_after = self.clock() + 15
                    raise
                updated = self.clock()
                self.store.save_snapshot(rates, updated, date)
                self.snapshot = {code: Entry(value, updated, date) for code, value in rates.items()}
                self.cache.entries.clear()
                self.retry_after = 0
                origin = 'API ЦБ'
            if source not in self.snapshot or target not in self.snapshot:
                raise UserError('Валюта не поддерживается ЦБ РФ. Список: /currencies.')
            base, quote = self.snapshot[source], self.snapshot[target]
            entry = Entry(base.value / quote.value, base.updated_at, base.rate_date)
            self.store.save_pair(pair, entry)
            self.cache.entries[pair] = entry
            return entry, origin

    def currencies(self):
        self.rate('RUB', 'RUB')
        with self.lock:
            return ', '.join(sorted(self.snapshot))

    def convert(self, text):
        quantity, source, target = parse_query(text)
        entry, origin = self.rate(source, target)
        return (f'{fmt(quantity, 8)} {source} = {fmt(quantity * entry.value)} {target}\n'
                f'Курс ЦБ на {entry.rate_date} · источник: {origin}')
