"""The only exchange-rate transport. Never follows redirects or uses other endpoints."""
from decimal import Decimal, InvalidOperation
import requests
from money import WalletError

ENDPOINT = 'http://api.exchangerate.host/convert'


class CurrencyAPI:
    def __init__(self, key, session=None):
        self.key = key
        self.session = session or requests.Session()

    def convert(self, source, target, amount=Decimal('1')) -> Decimal:
        try:
            response = self.session.get(
                ENDPOINT,
                params={'access_key': self.key, 'from': source, 'to': target,
                        'amount': str(amount)},
                timeout=(5, 15), allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise WalletError('API перенаправляет HTTP-запрос. Разрешён только заданный HTTP /convert; попробуйте позже.')
            response.raise_for_status()
            data = response.json(parse_float=Decimal)
        except (requests.RequestException, ValueError):
            raise WalletError('Не удалось связаться с сервисом курсов. Попробуйте ещё раз чуть позже.') from None
        if not isinstance(data, dict):
            raise WalletError('Сервис курсов вернул неожиданный ответ. Попробуйте позже.')
        if data.get('success') is not True:
            error = data.get('error') or {}
            code = str(error.get('code', '')) if isinstance(error, dict) else ''
            messages = {
                '101': 'Ключ API недействителен. Проверьте CURRENCY_API_KEY в .env.',
                '102': 'Учётная запись API отключена. Проверьте аккаунт exchangerate.host.',
                '104': 'Лимит запросов API исчерпан. Попробуйте позже.',
                '105': 'Ваш тариф exchangerate.host не поддерживает /convert. Нужен тариф с конвертацией.',
                '106': 'API не принимает заданный HTTP-запрос. Проверьте ограничения аккаунта.',
                '401': 'Неизвестная исходная валюта.',
                '402': 'Неизвестная валюта назначения.',
                '403': 'API не принимает эту сумму.',
            }
            raise WalletError(messages.get(code, 'Сервис не смог конвертировать эту валютную пару. Попробуйте позже или выберите другую страну.'))
        try:
            result = Decimal(str(data['result']))
            if not result.is_finite() or result <= 0 or result > Decimal('1e24'):
                raise ValueError
            return result
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise WalletError('API вернул некорректную сумму. Попробуйте позже.') from None
