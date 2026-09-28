from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Entry:
    value: Decimal
    updated_at: float
    rate_date: str


class MemoryCache:
    """dict lookups are O(1) on average; caller synchronizes access."""
    def __init__(self, ttl, entries=None):
        self.ttl = ttl
        self.entries = dict(entries or {})

    def get(self, pair, now):
        entry = self.entries.get(pair)
        if entry and 0 <= now - entry.updated_at < self.ttl:
            return entry
        return None
