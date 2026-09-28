import argparse
import logging
import sqlite3
import telebot
from api.cbr import CBRClient
from bot.handlers import CurrencyBot
from config import Config
from database.store import Store
from diagnostics import configure_logging, check_connection
from services.converter import Converter


def main():
    parser = argparse.ArgumentParser(description='Telegram currency converter with SQLite cache')
    parser.add_argument('--check', action='store_true', help='Check Telegram without polling or sending messages')
    args = parser.parse_args()
    try:
        config = Config.load()
        configure_logging(config.telegram_token)
        bot = telebot.TeleBot(config.telegram_token)
        check_connection(bot)
        if args.check:
            return 0
        store = Store(config.database)
        CurrencyBot(bot, Converter(CBRClient(), store, config.cache_ttl), store)
        logging.info('Конвертер запущен. TTL=%s секунд. Для остановки Ctrl+C.', config.cache_ttl)
        bot.infinity_polling(timeout=20, long_polling_timeout=20, allowed_updates=['message', 'callback_query'])
        return 0
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        logging.error('%s', exc)
        return 1
    except Exception as exc:
        logging.error('Не удалось запустить бота (%s). Проверьте токен и соединение.', type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
