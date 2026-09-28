import logging
from unittest.mock import Mock
import pytest
from diagnostics import SafeFormatter, check_connection


def test_logs_redact_token_and_traceback():
    token = '123456:secret_ABC'
    formatter = SafeFormatter(token)
    try:
        raise RuntimeError(f'https://api.telegram.org/bot{token}/getUpdates')
    except RuntimeError:
        import sys
        record = logging.LogRecord('test', logging.ERROR, '', 0,
                                   'Polling failed: %s', (token,), sys.exc_info())
    output = formatter.format(record)
    assert token not in output
    assert '[TOKEN]' in output
    assert 'RuntimeError' in output


def test_check_connection_rejects_webhook():
    bot = Mock()
    bot.get_webhook_info.return_value.url = 'https://example.org/hook'
    with pytest.raises(RuntimeError, match='webhook'):
        check_connection(bot)
    bot.get_webhook_info.return_value.url = ''
    assert check_connection(bot) == bot.get_me.return_value
    bot.get_updates.assert_not_called()
