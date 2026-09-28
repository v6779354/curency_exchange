"""Visible Telegram diagnostics without leaking tokens from request URLs."""
import logging
import re
import telebot


class SafeFormatter(logging.Formatter):
    def __init__(self, token):
        super().__init__('%(levelname)s: %(message)s')
        self.token = token

    def format(self, record):
        text = super().format(record)
        if self.token:
            text = text.replace(self.token, '[TOKEN]')
        return re.sub(r'bot\d+:[A-Za-z0-9_-]+', 'bot[TOKEN]', text)


def configure_logging(token):
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter(token))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    telebot.logger.handlers.clear()
    telebot.logger.propagate = True
    telebot.logger.disabled = False
    telebot.logger.setLevel(logging.ERROR)


def check_connection(bot):
    me = bot.get_me()
    webhook = bot.get_webhook_info()
    if webhook.url:
        raise RuntimeError('У бота установлен webhook. Отключите его перед запуском long polling.')
    logging.info('Подключение успешно: @%s. Откройте этого бота в Telegram.', me.username)
    return me
