import logging
import sqlite3
from telebot import types
from telebot.apihelper import ApiException
from errors import UserError

HELP = ('💱 Конвертер валют\n\nВведите USD RUB или 100 USD RUB.\n'
        'Курсы ЦБ РФ; дата курса указана в ответе.\n\n'
        '/currencies — доступные валюты\n/add текст — сохранить заметку\n'
        '/list — мои заметки\n/help — справка')


class CurrencyBot:
    def __init__(self, bot, converter, store):
        self.bot, self.converter, self.store = bot, converter, store
        bot.message_handler(content_types=['text'])(self.message)
        bot.message_handler(content_types=['photo', 'document', 'voice', 'audio', 'sticker',
                                           'video', 'location', 'contact'])(self.message)
        bot.callback_query_handler(func=lambda call: True)(self.callback)

    def message(self, message):
        try:
            if message.chat.type != 'private':
                self.bot.send_message(message.chat.id, 'Откройте личный чат с ботом.')
                return
            self.handle(message)
        except UserError as exc:
            self.bot.send_message(message.chat.id, str(exc))
        except sqlite3.Error:
            logging.error('Database operation failed')
            self.bot.send_message(message.chat.id, 'Не удалось сохранить или прочитать данные. Попробуйте позже.')
        except ApiException:
            logging.error('Telegram request failed')

    def handle(self, message):
        text = (message.text or '').strip()
        uid, chat = message.from_user.id, message.chat.id
        parts = text.split(maxsplit=1)
        command = parts[0].split('@')[0].lower() if parts else ''
        argument = parts[1].strip() if len(parts) > 1 else ''
        if command in ('/start', '/help', '/menu', '/cancel'):
            self.bot.send_message(chat, HELP, reply_markup=types.ReplyKeyboardRemove())
        elif command == '/currencies':
            self.bot.send_message(chat, self.converter.currencies())
        elif command == '/add':
            if not argument or len(argument) > 1000:
                raise UserError('Введите /add текст заметки (от 1 до 1000 символов).')
            self.store.add_note(uid, argument)
            self.bot.send_message(chat, 'Заметка сохранена. Посмотреть: /list')
        elif command == '/list':
            if argument and (not argument.isascii() or not argument.isdigit() or len(argument) > 15):
                raise UserError('Используйте /list или команду следующей страницы из ответа.')
            rows = self.store.notes(uid, int(argument or 0), 6)
            if not rows:
                self.bot.send_message(chat, 'Заметок пока нет. Добавить: /add текст')
            for row in rows[:5]:
                self.bot.send_message(chat, f"№{row['id']} · {row['created_at']} UTC\n{row['text']}")
            if len(rows) > 5:
                self.bot.send_message(chat, f"Следующие заметки: /list {rows[4]['id']}")
        elif command.startswith('/'):
            raise UserError('Неизвестная команда. Справка: /help')
        else:
            self.bot.send_message(chat, self.converter.convert(text))

    def callback(self, call):
        # Old wallet buttons may remain in the chat after switching projects.
        try:
            self.bot.answer_callback_query(call.id, 'Меню обновилось. Отправьте /start.')
        except ApiException:
            logging.error('Telegram callback failed')
