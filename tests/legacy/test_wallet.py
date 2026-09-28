from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from telebot import types

from bot import WalletBot
from current_api import CurrencyAPI, ENDPOINT
from countries import resolve
from money import WalletError, number, amount
from storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'test.sqlite3')


def make_trip(store, uid=1, destination='Китай'):
    state = dict(origin={'name': 'Россия'}, destination={'name': destination},
                 home='RUB', local='CNY', rate='0.078125', rate_source='manual',
                 initial_home='32000', initial_local='2500')
    store.set_state(uid, state)
    return store.create(uid, state)


def test_expense_atomic_idempotent_and_isolated(store):
    tid = make_trip(store)
    token, home = store.quote(1, tid, Decimal('100'))
    assert home == Decimal('1280')
    with pytest.raises(WalletError):
        store.confirm(2, token, True)
    def click(_):
        try:
            return store.confirm(1, token, True)
        except WalletError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(click, range(4)))
    assert results.count(tid) == 1
    assert Decimal(store.trip(1)['balance']) == 2400
    assert len(store.history(1, tid)) == 1
    with pytest.raises(WalletError):
        store.trip(2, tid)
    with pytest.raises(WalletError):
        store.switch(2, tid)


def test_switch_does_not_redirect_expense(store):
    old = make_trip(store)
    token, _ = store.quote(1, old, Decimal('100'))
    new = make_trip(store, destination='Другое')
    store.confirm(1, token, True)
    assert store.trip(1)['id'] == new
    assert Decimal(store.trip(1, old)['balance']) == 2400
    assert Decimal(store.trip(1, new)['balance']) == 2500


def test_cancel_insufficient_and_rate_change(store):
    tid = make_trip(store)
    token, _ = store.quote(1, tid, Decimal('100'))
    store.confirm(1, token, False)
    assert store.history(1, tid) == []
    token, _ = store.quote(1, tid, Decimal('3000'))
    with pytest.raises(WalletError, match='Недостаточно'):
        store.confirm(1, token, True)
    assert Decimal(store.trip(1)['balance']) == 2500
    accepted, _ = store.quote(1, tid, Decimal('100'))
    store.confirm(1, accepted, True)
    store.set_rate(1, tid, Decimal('0.1'))
    with pytest.raises(WalletError):
        store.confirm(1, token, True)
    assert Decimal(store.trip(1)['balance']) == 2400
    assert Decimal(store.history(1, tid)[0]['home_amount']) == 1280


def test_state_survives_restart_and_create_once(store):
    make_trip(store)
    store.set_state(1, {'step': 'origin', 'nonce': 'x'})
    reopened = Store(store.path)
    assert reopened.state(1)['step'] == 'origin'
    assert reopened.trip(1)['destination'] == 'Китай'
    with pytest.raises(WalletError):
        reopened.create(1, {'step': 'different'})


@pytest.mark.parametrize('text', ['0', '-1', 'NaN', 'Infinity', '1e3', 'hello', '1.123456789', '1000000000001'])
def test_invalid_numbers(text):
    with pytest.raises(WalletError):
        number(text)


def test_country_and_precision():
    assert resolve('Россия')['currencies'] == ['RUB']
    assert resolve('Китай')['currencies'] == ['CNY']
    assert resolve('US')['currencies'] == ['USD']
    assert resolve('Япония')['currencies'] == ['JPY']
    assert amount('1 000,50', 'RUB') == Decimal('1000.50')
    with pytest.raises(WalletError):
        amount('1.01', 'JPY')
    with pytest.raises(WalletError):
        resolve('Атлантида')


def test_api_contract():
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {'success': True, 'result': 7.5}
    assert CurrencyAPI('secret', session).convert('USD', 'CNY', Decimal('1')) == Decimal('7.5')
    session.get.assert_called_once_with(ENDPOINT, params={
        'access_key': 'secret', 'from': 'USD', 'to': 'CNY', 'amount': '1'},
        timeout=(5, 15), allow_redirects=False)


@pytest.mark.parametrize('payload', [None, [], {'success': True}, {'success': True, 'result': None},
    {'success': True, 'result': 'NaN'}, {'success': True, 'result': -1},
    {'success': False, 'error': {'code': 105}}, {'success': False, 'error': 'bad'}])
def test_api_bad_payload(payload):
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = payload
    with pytest.raises(WalletError):
        CurrencyAPI('secret', session).convert('RUB', 'CNY')


def test_network_failure_and_redirect():
    session = Mock()
    session.get.side_effect = requests.Timeout('contains secret URL')
    with pytest.raises(WalletError) as exc:
        CurrencyAPI('secret', session).convert('RUB', 'CNY')
    assert 'secret' not in str(exc.value)
    session.get.side_effect = None
    session.get.return_value.status_code = 301
    with pytest.raises(WalletError, match='перенаправляет'):
        CurrencyAPI('secret', session).convert('RUB', 'CNY')


def test_full_conversation(store):
    bot, api = Mock(), Mock()
    api.convert.side_effect = [Decimal('0.078125'), Decimal('2500')]
    wallet = WalletBot(bot, store, api)
    def msg(text):
        wallet.message(SimpleNamespace(from_user=SimpleNamespace(id=1), text=text))
    def draft(action):
        state = store.state(1)
        wallet.callback(SimpleNamespace(id='callback', from_user=SimpleNamespace(id=1),
                                       data=f"draft:{state['nonce']}:{action}"))
    msg('/start')
    msg('/newtrip')
    msg('Россия')
    msg('Китай')
    draft('rate_yes')
    msg('32000')
    draft('create')
    msg('100')
    args = bot.send_message.call_args
    assert '100 CNY = 1 280 RUB' in args.args[1]
    callback = args.kwargs['reply_markup'].keyboard[0][0].callback_data
    wallet.callback(SimpleNamespace(id='callback', from_user=SimpleNamespace(id=1), data=callback))
    assert '2 400 CNY = 30 720 RUB' in bot.send_message.call_args.args[1]
    assert api.convert.call_count == 2
    for command in ['/switch', '/balance', '/history', '/setrate']:
        msg(command)
    msg('0.1')
    draft('edit_yes')
    assert store.trip(1)['rate'] == '0.1'


def test_manual_rate_and_stale_buttons(store):
    bot, api = Mock(), Mock()
    api.convert.side_effect = [Decimal('0.08'), Decimal('80')]
    wallet = WalletBot(bot, store, api)
    def msg(text):
        wallet.message(SimpleNamespace(from_user=SimpleNamespace(id=1), text=text))
    def draft(action, nonce=None):
        nonce = nonce or store.state(1)['nonce']
        wallet.callback(SimpleNamespace(id='c', from_user=SimpleNamespace(id=1), data=f'draft:{nonce}:{action}'))
    msg('/newtrip'); msg('Россия'); msg('Китай')
    stale = store.state(1)['nonce']
    draft('rate_no'); msg('0.1'); draft('manual_yes'); msg('1000'); draft('create')
    assert store.trip(1)['balance'] == '100.00'
    assert store.trip(1)['rate_source'] == 'manual'
    with pytest.raises(WalletError):
        draft('rate_yes', stale)
