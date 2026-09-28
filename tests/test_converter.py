from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import requests
from api.cbr import CBRClient
from bot.handlers import CurrencyBot
from database.store import Store
from errors import UserError
from services.converter import Converter, parse_query


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'currency.sqlite3')
    api = Mock()
    api.fetch.return_value = ({'RUB': Decimal(1), 'USD': Decimal(90), 'EUR': Decimal(100)}, '2026-09-26')
    now = [1000.0]
    return store, api, now, Converter(api, store, ttl=60, clock=lambda: now[0])


def test_hit_miss_cross_pair_and_restart(setup):
    store, api, now, converter = setup
    assert converter.rate('USD', 'RUB')[0].value == 90
    assert converter.rate('USD', 'RUB')[1] == 'кеш'
    assert converter.rate('USD', 'EUR')[0].value == Decimal('0.9')
    api.fetch.assert_called_once()
    restarted = Converter(api, Store(store.path), 60, lambda: now[0])
    assert restarted.rate('USD', 'RUB')[1] == 'кеш'
    assert restarted.rate('EUR', 'USD')[1] == 'кеш'
    api.fetch.assert_called_once()
    assert store.load()[0]['USD_RUB'].value == 90


def test_ttl_boundary_and_derived_pair_age(setup):
    store, api, now, converter = setup
    converter.rate('USD', 'RUB')
    now[0] = 1059
    assert converter.rate('EUR', 'USD')[0].updated_at == 1000
    now[0] = 1060
    api.fetch.return_value = ({'RUB': Decimal(1), 'USD': Decimal(95), 'EUR': Decimal(100)}, '2026-09-27')
    assert converter.rate('USD', 'RUB')[0].value == 95
    assert api.fetch.call_count == 2
    assert converter.rate('EUR', 'USD')[0].updated_at == 1060


def test_concurrent_misses_fetch_once(setup):
    store, api, now, converter = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: converter.rate('USD', 'RUB')[0].value, range(20)))
    assert values == [90] * 20
    api.fetch.assert_called_once()


def test_failure_preserves_old_data_and_limits_retries(setup):
    store, api, now, converter = setup
    converter.rate('USD', 'RUB')
    now[0] += 60
    api.fetch.side_effect = UserError('Сервис недоступен')
    for _ in range(3):
        with pytest.raises(UserError, match='недоступен'):
            converter.rate('USD', 'RUB')
    assert api.fetch.call_count == 2
    assert store.load()[0]['USD_RUB'].updated_at == 1000
    now[0] += 15
    api.fetch.side_effect = None
    assert converter.rate('USD', 'RUB')[1] == 'API ЦБ'


@pytest.mark.parametrize('text', ['', ' ', 'USD', 'USD RUB EUR GBP', 'US RUB', 'ЮСД RUB',
                                   '0 USD RUB', '-1 USD RUB', 'NaN USD RUB', '1e6 USD RUB'])
def test_invalid_input(text):
    with pytest.raises(UserError):
        parse_query(text)


def test_normalize_and_unknown_currency(setup):
    _, api, _, converter = setup
    assert parse_query(' 100,50 usd rub ') == (Decimal('100.50'), 'USD', 'RUB')
    assert '100 USD = 9000 RUB' in converter.convert('100 USD RUB')
    for _ in range(2):
        with pytest.raises(UserError, match='не поддерживается'):
            converter.convert('ZZZ RUB')
    api.fetch.assert_called_once()


def test_cbr_nominal():
    session = Mock()
    session.get.return_value.content = b'<ValCurs Date="26.09.2026"><Valute><CharCode>JPY</CharCode><Nominal>100</Nominal><Value>60,50</Value></Valute></ValCurs>'
    rates, date = CBRClient(session).fetch()
    assert rates['JPY'] == Decimal('0.605')
    assert date == '2026-09-26'
    assert session.get.call_args.kwargs['timeout'] == (5, 15)


@pytest.mark.parametrize('payload', [b'', b'{}', b'<ValCurs Date="26.09.2026"/>',
    b'<ValCurs Date="26.09.2026"><Valute><CharCode>USD</CharCode><Nominal>0</Nominal><Value>90</Value></Valute></ValCurs>',
    b'<ValCurs Date="26.09.2026"><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>NaN</Value></Valute></ValCurs>'])
def test_bad_xml(payload):
    session = Mock()
    session.get.return_value.content = payload
    with pytest.raises(UserError, match='некорректные'):
        CBRClient(session).fetch()


@pytest.mark.parametrize('error', [requests.Timeout(), requests.ConnectionError(), requests.HTTPError()])
def test_network_errors(error):
    session = Mock()
    session.get.side_effect = error
    with pytest.raises(UserError):
        CBRClient(session).fetch()


def test_notes_isolation_persistence_and_pages(setup):
    store, _, _, converter = setup
    bot = Mock()
    app = CurrencyBot(bot, converter, store)
    def send(text, uid=1):
        app.message(SimpleNamespace(text=text, from_user=SimpleNamespace(id=uid),
                                    chat=SimpleNamespace(id=uid, type='private')))
    for i in range(7):
        send(f'/add note {i}')
    send('/add private', 2)
    assert len(Store(store.path).notes(1)) == 7
    send('/list')
    assert bot.send_message.call_args.args[1] == 'Следующие заметки: /list 5'
    send('/list 5')
    assert 'note 6' in bot.send_message.call_args.args[1]
    send('/list', 2)
    assert 'private' in bot.send_message.call_args.args[1]
    send('/add')
    assert 'Введите /add' in bot.send_message.call_args.args[1]
    send('USD RUB')
    assert '1 USD = 90 RUB' in bot.send_message.call_args.args[1]
