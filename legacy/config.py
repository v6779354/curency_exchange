from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    telegram_token: str
    currency_api_key: str
    database: Path = ROOT / 'wallet.sqlite3'

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        values = [os.getenv(key, '').strip() for key in
                  ('TELEGRAM_BOT_TOKEN', 'CURRENCY_API_KEY')]
        if not all(values):
            raise ValueError('Заполните TELEGRAM_BOT_TOKEN и CURRENCY_API_KEY в .env')
        return cls(*values)
