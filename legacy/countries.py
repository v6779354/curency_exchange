"""Local CLDR country/currency lookup; network availability is checked by /convert."""
from datetime import date
import pycountry
from babel import Locale
from babel.numbers import get_territory_currencies
from money import WalletError

RU = Locale('ru')
NAMES = {}
for country in pycountry.countries:
    for name in (country.name, country.alpha_2, country.alpha_3,
                 getattr(country, 'official_name', ''), RU.territories.get(country.alpha_2, '')):
        if name:
            NAMES[name.casefold().replace('ё', 'е')] = country.alpha_2
NAMES.update({'рф': 'RU', 'российская федерация': 'RU', 'сша': 'US', 'америка': 'US',
              'оаэ': 'AE', 'англия': 'GB', 'южная корея': 'KR', 'кнр': 'CN',
              'турция': 'TR', 'таиланд': 'TH', 'тайланд': 'TH', 'вьетнам': 'VN'})


def resolve(text):
    code = NAMES.get(text.strip().casefold().replace('ё', 'е'))
    if not code:
        raise WalletError('Не нашёл страну. Введите её название (Россия, Китай) или код ISO (RU, CN).')
    currencies = get_territory_currencies(code, start_date=date.today(), tender=True)
    if not currencies:
        raise WalletError('В справочнике нет действующей валюты этой страны. Выберите другую страну.')
    return {'code': code, 'name': RU.territories.get(code, code), 'currencies': currencies}
