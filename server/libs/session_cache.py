"""Local cache for brokered PD sessions.

The canonical session lives in PD; this cache exists only so we don't have to
call ``GET /api/v1/auth/session/<token>`` on every single client request.
Validated sessions are cached for ``CACHE_TTL`` (default 5 minutes) and then
re-validated against PD.

Persisted to disk so cache survives restart.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


CACHE_TTL = 300  # seconds — how long a positive PD validation is trusted locally


@dataclass
class CachedSession:
    token: str
    user_id: str
    username: str
    expires_at: float  # unix seconds; from PD's authoritative answer
    cached_at: float  # unix seconds; when WE last validated

    @property
    def is_pd_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def needs_revalidation(self) -> bool:
        return time.time() - self.cached_at > CACHE_TTL


class SessionCache:
    """JSON-backed key/value cache: token → CachedSession."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, CachedSession] = {}
        self._load()

    # ---- persistence ------------------------------------------------

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for tok, entry in raw.items():
            try:
                self._data[tok] = CachedSession(**entry)
            except TypeError:
                continue

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({tok: asdict(s) for tok, s in self._data.items()}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # ---- API --------------------------------------------------------

    def get(self, token: str) -> Optional[CachedSession]:
        return self._data.get(token)

    def put(self, session: CachedSession) -> None:
        with self._lock:
            self._data[session.token] = session
            self._save_locked()

    def drop(self, token: str) -> None:
        with self._lock:
            if token in self._data:
                del self._data[token]
                self._save_locked()
