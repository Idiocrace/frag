"""Multi-factor auth code generation and verification."""

import random
from datetime import datetime, timedelta
from typing import Protocol, Optional


class MFAStore(Protocol):
    def issue(self, username: str) -> str: ...
    def verify(self, username: str, code: str) -> bool: ...
    def clear(self, username: str) -> None: ...


class InMemoryMFAStore:
    """In-memory MFA code store with per-code expiry."""

    def __init__(self, ttl_minutes: int = 10):
        self._codes: dict[str, tuple[str, datetime]] = {}
        self._ttl = timedelta(minutes=ttl_minutes)

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    def ttl_human(self) -> tuple[int, str]:
        """Return TTL as (amount, unit-plural) for inclusion in emails."""
        minutes = int(self._ttl.total_seconds() // 60)
        return minutes, "minutes"

    def _expired(self, expires: datetime) -> bool:
        return datetime.utcnow() > expires

    def issue(self, username: str) -> str:
        code = f"{random.randint(0, 999_999):06d}"
        self._codes[username] = (code, datetime.utcnow() + self._ttl)
        return code

    def verify(self, username: str, code: str) -> bool:
        entry: Optional[tuple[str, datetime]] = self._codes.get(username)
        if entry is None:
            return False
        stored, expires = entry
        if self._expired(expires):
            self._codes.pop(username, None)
            return False
        if stored != code:
            return False
        self._codes.pop(username, None)
        return True

    def clear(self, username: str) -> None:
        self._codes.pop(username, None)


def user_needs_mfa(user_data: dict) -> bool:
    """Check the MFA fields on an account doc to decide whether to challenge."""
    mfa = user_data.get("mfa") or {}
    return bool(mfa.get("enabled")) and bool(mfa.get("verified")) and not bool(mfa.get("pending"))
