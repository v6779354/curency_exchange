"""Russian travel wallet UI. Run with: python bot.py."""
import logging
import argparse
import secrets
import sqlite3
from decimal import Decimal
from functools import wraps

import telebot
from telebot import types
from telebot.apihelper import ApiException

from config import Config
from diagnostics import configure_logging, check_connection
from countries import resolve
from current_api import CurrencyAPI
from money import WalletError, amount, number, rounded, fmt
from storage import Store


def keyboard(rows, back=True):
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(*(types.InlineKeyboardButton(label, callback_data=data) for label, data in row))
    if back:
        markup.row(types.InlineKeyboardButton('🏠 Главное меню / отменить ввод', callback_data='menu'))
    return markup


def main_menu():
    return keyboard([
        [('🌍 Создать новое путешествие', 'newtrip')],
        [('🧳 Мои путешествия', 'switch:0')],
        [('💰 Баланс', 'balance'), ('📒 История расходов', 'history')],
        [('💱 Изменить курс', 'setrate')],
    ], back=False)


def balance_text(trip):
    home = rounded(Decimal(trip['balance']) / Decimal(trip['rate']), trip['home'])
    return (f"{trip['origin']} → {trip['destination']} · №{trip['id']}\n"
            f"Остаток: {fmt(trip['balance'])} {trip['local']} = {fmt(home)} {trip['home']}\n"
            f"Курс: 1 {trip['home']} = {fmt(trip['rate'])} {trip['local']} "
            f"({'ручной' if trip['rate_source'] == 'manual' else 'зафиксирован из API'})")


class WalletBot:
    def __init__(self, bot, store, api):
        self.bot, self.store, self.api = bot, store, api
        bot.message_handler(content_types=['text'])(self.guard(self.message))
        bot.callback_query_handler(func=lambda call: True)(self.guard(self.callback))
        bot.message_handler(content_types=['photo', 'document', 'voice', 'audio', 'sticker', 'video', 'location', 'contact'])(self.guard(self.unsupported))

    def guard(self, handler):
        @wraps(handler)
        def wrapped(event):
            message = event.message if isinstance(event, types.CallbackQuery) else event
            if not message:
                return
            chat = message.chat.id
            if message.chat.type != 'private':
                if isinstance(event, types.CallbackQuery):
                    self.bot.answer_callback_query(event.id, 'Откройте личный чат с ботом.')
                else:
                    self.bot.send_message(chat, 'Для работы с личным кошельком откройте личный чат с ботом.')
                return
            try:
                handler(event)
            except WalletError as exc:
                self.bot.send_message(chat, str(exc), reply_markup=keyboard([]))
            except (sqlite3.Error, ArithmeticError, KeyError, ValueError, TypeError):
                logging.error('Wallet operation failed (%s)', handler.__name__)
                self.bot.send_message(chat, 'Не удалось выполнить операцию. Проверьте баланс и попробуйте ещё раз.', reply_markup=main_menu())
            except ApiException:
                # Never log Telegram URLs: they contain the token.
                logging.error('Telegram request failed (%s)', handler.__name__)
        return wrapped

    def send(self, uid, text, markup=None):
        self.bot.send_message(uid, text, reply_markup=markup or keyboard([]))

    def save(self, uid, state, step):
        state.update(step=step, nonce=secrets.token_hex(5))
        self.store.set_state(uid, state)

    def draft_buttons(self, state, choices):
        return keyboard([[(label, f"draft:{state['nonce']}:{action}") for label, action in choices]])

    def ask_amount(self, uid, state):
        self.save(uid, state, 'initial')
        self.send(uid, f"Введите начальную сумму в домашней валюте {state['home']}.")

    def ready_pair(self, uid, state):
        # Saving this step first keeps the pair retryable after transient API errors.
        self.save(uid, state, 'fetch')
        try:
            rate = self.api.convert(state['home'], state['local'])
        except WalletError:
            self.send(uid, 'Проверить валютную пару ещё раз:', self.draft_buttons(state, [('🔄 Повторить', 'retry')]))
            raise
        state.update(rate=str(rate), rate_source='api')
        self.save(uid, state, 'rate_choice')
        self.send(uid, f"{state['origin']['name']} → {state['destination']['name']}\n"
                  f"Текущий курс: 1 {state['home']} = {fmt(rate)} {state['local']}.\nПодходит ли курс?",
                  self.draft_buttons(state, [('✅ Да', 'rate_yes'), ('❌ Нет, ввести вручную', 'rate_no')]))

    def country_done(self, uid, state, side, currency):
        state['home' if side == 'origin' else 'local'] = currency
        if side == 'origin':
            self.save(uid, state, 'destination')
            self.send(uid, 'Введите страну назначения, например Китай.')
        else:
            self.ready_pair(uid, state)

    def route(self, uid, action):
        self.store.set_state(uid, None)
        if action == 'newtrip':
            self.save(uid, {}, 'origin')
            self.send(uid, 'Создадим путешествие 🌍\nВведите страну отправления, например Россия.')
        elif action in ('start', 'menu'):
            self.send(uid, 'Добро пожаловать! Создайте новое путешествие или выберите существующее.\n'
                      'После создания отправляйте сумму расхода в валюте назначения.', main_menu())
        elif action.startswith('switch'):
            page = max(0, int(action.split(':')[1])) if ':' in action else 0
            trips = self.store.trips(uid, page)
            active = self.store.user(uid)['active_trip']
            rows = [[(f"{'✅ ' if t['id'] == active else ''}{t['destination']} · {t['home']}/{t['local']} · №{t['id']}", f"trip:{t['id']}")] for t in trips[:8]]
            nav = []
            if page:
                nav.append(('← Назад', f'switch:{page-1}'))
            if len(trips) > 8:
                nav.append(('Далее →', f'switch:{page+1}'))
            if nav:
                rows.append(nav)
            rows.append([('Создать новое путешествие', 'newtrip')])
            self.send(uid, 'Ваши путешествия:' if trips else 'Путешествий пока нет.', keyboard(rows))
        elif action == 'balance':
            self.send(uid, balance_text(self.store.trip(uid)), main_menu())
        elif action == 'history' or action.startswith('history:'):
            if ':' in action:
                _, tid, page = action.split(':')
                trip, page = self.store.trip(uid, int(tid)), max(0, int(page))
            else:
                trip, page = self.store.trip(uid), 0
            entries = self.store.history(uid, trip['id'], page)
            text = f"История: {trip['destination']} · №{trip['id']}\n"
            text += '\n'.join(f"{e['confirmed_at']} UTC: {fmt(e['local_amount'])} {trip['local']} = {fmt(e['home_amount'])} {trip['home']}" for e in entries[:8]) or 'Расходов пока нет.'
            nav = []
            if page:
                nav.append(('← Назад', f"history:{trip['id']}:{page-1}"))
            if len(entries) > 8:
                nav.append(('Далее →', f"history:{trip['id']}:{page+1}"))
            self.send(uid, text, keyboard([nav] if nav else []))
        elif action == 'setrate':
            trip = self.store.trip(uid)
            self.save(uid, {'trip_id': trip['id']}, 'edit_rate')
            self.send(uid, f"Сейчас 1 {trip['home']} = {fmt(trip['rate'])} {trip['local']}.\n"
                      f"Введите новый курс: сколько {trip['local']} дают за 1 {trip['home']}.\n"
                      'Остаток в валюте назначения сохранится; его домашняя оценка изменится. История сохранит прежние суммы.')
        else:
            raise WalletError('Неизвестная команда. Выберите действие в меню.')

    def message(self, message):
        uid, text = message.from_user.id, message.text.strip()
        if text.startswith('/'):
            command = text.split()[0].split('@')[0][1:]
            self.route(uid, {'cancel': 'menu', 'help': 'menu'}.get(command, command))
            return
        state = self.store.state(uid)
        if not state:
            trip = self.store.trip(uid)
            value = amount(text, trip['local'])
            token, home = self.store.quote(uid, trip['id'], value)
            self.send(uid, f"{trip['destination']} · №{trip['id']}\n{fmt(value)} {trip['local']} = {fmt(home)} {trip['home']}\nУчесть как расход?",
                      keyboard([[('✅ Да', f'expense:{token}:yes'), ('❌ Нет', f'expense:{token}:no')]]))
            return
        step = state['step']
        if step in ('origin', 'destination'):
            country = resolve(text)
            state[step] = country
            if len(country['currencies']) == 1:
                self.country_done(uid, state, step, country['currencies'][0])
            else:
                state['side'] = step
                self.save(uid, state, 'currency')
                self.send(uid, f"Для {country['name']} в справочнике несколько валют. Выберите нужную:",
                          self.draft_buttons(state, [(c, f'currency_{c}') for c in country['currencies']]))
        elif step == 'manual_rate':
            state.update(rate=str(number(text)), rate_source='manual')
            self.save(uid, state, 'manual_confirm')
            self.send(uid, f"Подтвердить курс 1 {state['home']} = {fmt(state['rate'])} {state['local']}?",
                      self.draft_buttons(state, [('✅ Подтвердить', 'manual_yes'), ('✏️ Изменить', 'rate_no')]))
        elif step == 'initial':
            initial = amount(text, state['home'])
            # Always obtain a real conversion of the initial home amount.
            market = self.api.convert(state['home'], state['local'], initial)
            if state['rate_source'] == 'api':
                state['rate'] = str(market / initial)
                local = rounded(market, state['local'])
            else:
                local = rounded(initial * Decimal(state['rate']), state['local'])
            if local <= 0:
                raise WalletError('После округления получается нулевой баланс. Введите большую сумму.')
            state.update(initial_home=str(initial), initial_local=str(local))
            self.save(uid, state, 'create_confirm')
            self.send(uid, f"{state['origin']['name']} → {state['destination']['name']}\n"
                      f"Начальная сумма: {fmt(initial)} {state['home']}\n"
                      f"Стартовый баланс: {fmt(local)} {state['local']}\n"
                      f"Курс: 1 {state['home']} = {fmt(state['rate'])} {state['local']}\n"
                      + (f"По API сейчас: {fmt(rounded(market, state['local']))} {state['local']}; кошелёк использует ваш ручной курс.\n" if state['rate_source'] == 'manual' else 'Курс уточнён API для начальной суммы.\n')
                      + 'Создать путешествие?', self.draft_buttons(state, [('✅ Создать', 'create')]))
        elif step == 'edit_rate':
            trip = self.store.trip(uid, state['trip_id'])
            state['rate'] = str(number(text))
            self.save(uid, state, 'edit_confirm')
            self.send(uid, f"Установить 1 {trip['home']} = {fmt(state['rate'])} {trip['local']}?",
                      self.draft_buttons(state, [('✅ Подтвердить', 'edit_yes')]))
        else:
            raise WalletError('Выберите ответ кнопкой под последним вопросом или вернитесь в главное меню.')

    def callback(self, call):
        self.bot.answer_callback_query(call.id)
        uid, data = call.from_user.id, call.data or ''
        if data.startswith('expense:'):
            _, token, decision = data.split(':')
            if decision not in ('yes', 'no'):
                raise WalletError('Неизвестное действие.')
            tid = self.store.confirm(uid, token, decision == 'yes')
            self.send(uid, ('Расход записан.\n' if decision == 'yes' else 'Расход не учтён.\n') + balance_text(self.store.trip(uid, tid)), main_menu())
        elif data.startswith('trip:'):
            tid = int(data.split(':')[1])
            self.store.switch(uid, tid)
            self.send(uid, 'Путешествие выбрано.\n' + balance_text(self.store.trip(uid)), main_menu())
        elif data.startswith('draft:'):
            _, nonce, action = data.split(':')
            state = self.store.state(uid)
            if not state or state['nonce'] != nonce:
                raise WalletError('Эта кнопка устарела. Используйте последний вопрос или меню.')
            step = state['step']
            if step == 'currency' and action.startswith('currency_'):
                currency = action.removeprefix('currency_')
                if currency not in state[state['side']]['currencies']:
                    raise WalletError('Выберите валюту из предложенного списка.')
                self.country_done(uid, state, state['side'], currency)
            elif step == 'fetch' and action == 'retry':
                self.ready_pair(uid, state)
            elif step == 'rate_choice' and action == 'rate_yes':
                self.ask_amount(uid, state)
            elif step in ('rate_choice', 'manual_confirm') and action == 'rate_no':
                self.save(uid, state, 'manual_rate')
                self.send(uid, f"Введите курс: сколько {state['local']} дают за 1 {state['home']}. Например, 0,078125.")
            elif step == 'manual_confirm' and action == 'manual_yes':
                self.ask_amount(uid, state)
            elif step == 'create_confirm' and action == 'create':
                self.store.create(uid, state)
                self.send(uid, 'Путешествие создано! Отправьте число — сумму расхода в валюте назначения.\n' + balance_text(self.store.trip(uid)), main_menu())
            elif step == 'edit_confirm' and action == 'edit_yes':
                self.store.set_rate(uid, state['trip_id'], Decimal(state['rate']))
                self.send(uid, 'Курс обновлён. Старые неподтверждённые расходы отменены.\n' + balance_text(self.store.trip(uid, state['trip_id'])), main_menu())
            else:
                raise WalletError('Эта кнопка больше не подходит к текущему шагу.')
        else:
            self.route(uid, data)

    def unsupported(self, message):
        self.send(message.from_user.id, 'Отправьте текст: название страны при создании путешествия или сумму расхода.')


def main():
    parser = argparse.ArgumentParser(description='Миникошелёк путешественника')
    parser.add_argument('--check', action='store_true', help='Проверить Telegram без запуска бота')
    args = parser.parse_args()
    try:
        config = Config.load()
        configure_logging(config.telegram_token)
        logging.info('Проверяю подключение к Telegram…')
        bot = telebot.TeleBot(config.telegram_token, threaded=False)
        check_connection(bot)
        if args.check:
            return
        WalletBot(bot, Store(config.database), CurrencyAPI(config.currency_api_key))
        logging.info('Бот запущен. Отправьте /start в личном чате. Остановка: Ctrl+C.')
        bot.infinity_polling(timeout=20, long_polling_timeout=20,
                             logger_level=logging.ERROR,
                             allowed_updates=['message', 'callback_query'])
        logging.info('Бот остановлен.')
    except ValueError:
        raise SystemExit('Проверьте TELEGRAM_BOT_TOKEN и CURRENCY_API_KEY в .env.') from None
    except Exception:
        logging.exception('Не удалось запустить бота')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
