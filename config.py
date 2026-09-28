from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    telegram_token: str
    database: Path
    cache_ttl: int

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        if not token:
            raise ValueError('Заполните TELEGRAM_BOT_TOKEN в .env')
        try:
            ttl = int(os.getenv('CACHE_TTL_SECONDS', '3600'))
            if ttl <= 0:
                raise ValueError
        except ValueError:
            raise ValueError('CACHE_TTL_SECONDS должен быть целым числом больше нуля.') from None
        path = Path(os.getenv('DATABASE_PATH', 'currency.sqlite3'))
        return cls(token, path if path.is_absolute() else ROOT / path, ttl)
