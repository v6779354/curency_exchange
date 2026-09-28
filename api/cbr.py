"""Official daily rates, expressed in RUB per one currency unit."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from xml.etree import ElementTree
import requests
from errors import UserError

ENDPOINT = 'https://www.cbr.ru/scripts/XML_daily.asp'


class CBRClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def fetch(self):
        try:
            response = self.session.get(ENDPOINT, timeout=(5, 15))
            response.raise_for_status()
        except requests.Timeout:
            raise UserError('Сервис курсов не ответил вовремя. Попробуйте позже.') from None
        except requests.RequestException:
            raise UserError('Сервис курсов недоступен. Попробуйте позже.') from None
        try:
            root = ElementTree.fromstring(response.content)
            if root.tag != 'ValCurs':
                raise ValueError
            date = datetime.strptime(root.attrib['Date'], '%d.%m.%Y').date().isoformat()
            rates = {'RUB': Decimal('1')}
            for item in root.findall('Valute'):
                code = item.findtext('CharCode', '')
                nominal = Decimal(item.findtext('Nominal', '').replace(',', '.'))
                value = Decimal(item.findtext('Value', '').replace(',', '.'))
                if (not re.fullmatch('[A-Z]{3}', code) or code in rates
                        or not nominal.is_finite() or nominal <= 0
                        or not value.is_finite() or value <= 0):
                    raise ValueError
                rates[code] = value / nominal
            if len(rates) < 2:
                raise ValueError
            return rates, date
        except (ElementTree.ParseError, KeyError, ValueError, InvalidOperation):
            raise UserError('Сервис вернул некорректные курсы. Попробуйте позже.') from None
