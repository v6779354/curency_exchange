import re
from decimal import Decimal, ROUND_HALF_UP
from babel.numbers import get_currency_precision


class WalletError(Exception):
    """A message safe to display to the user."""


def number(text: str) -> Decimal:
    value = text.strip().replace('\u00a0', '').replace(' ', '').replace(',', '.')
    if len(value) > 30 or not re.fullmatch(r'\d+(?:\.\d{1,8})?', value):
        raise WalletError('Введите положительное число, например 100 или 100,50 (до 8 знаков после запятой).')
    result = Decimal(value)
    if not 0 < result <= Decimal('1000000000000'):
        raise WalletError('Сумма или курс должны быть больше нуля и не больше 1 000 000 000 000.')
    return result


def rounded(value, currency):
    return Decimal(value).quantize(Decimal(1).scaleb(-get_currency_precision(currency)), rounding=ROUND_HALF_UP)


def amount(text, currency):
    value = number(text)
    if value != rounded(value, currency):
        raise WalletError(f'Для {currency} допустимо знаков после запятой: {get_currency_precision(currency)}.')
    return value


def fmt(value):
    s = format(Decimal(value), ',f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s.replace(',', ' ').replace('.', ',')
