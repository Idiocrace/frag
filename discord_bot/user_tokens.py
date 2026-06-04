"""Discord-user → frag supporter-token mapping.

Keeps track of which Discord member was issued which fragsup_ token, so
``/token`` is idempotent (or rather: revokes-then-reissues the previous
one), ``/mytoken`` can return the existing token, and admins can revoke
by Discord user ID.

The mapping file is JSON, written atomically (tmp + os.replace), and
lives under ``data/`` by default — gitignored by the bot's .gitignore.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TokenAssignment:
    discord_id: int
    token: str
    issued_at: float
    note: str = ""


@dataclass
class _State:
    by_discord_id: dict[str, TokenAssignment] = field(default_factory=dict)


class UserTokenMap:
    """JSON-backed map of Discord user id (as str) -> TokenAssignment."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._state = _State()
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for key, entry in (raw.get("by_discord_id") or {}).items():
            try:
                self._state.by_discord_id[key] = TokenAssignment(**entry)
            except TypeError:
                continue

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = {
            "by_discord_id": {
                k: asdict(v) for k, v in self._state.by_discord_id.items()
            },
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    # ---- API ------------------------------------------------------------

    def get(self, discord_id: int) -> Optional[TokenAssignment]:
        return self._state.by_discord_id.get(str(discord_id))

    def set(self, discord_id: int, token: str, note: str = "") -> TokenAssignment:
        entry = TokenAssignment(
            discord_id=discord_id,
            token=token,
            issued_at=time.time(),
            note=note,
        )
        with self._lock:
            self._state.by_discord_id[str(discord_id)] = entry
            self._save_locked()
        return entry

    def remove(self, discord_id: int) -> Optional[TokenAssignment]:
        with self._lock:
            entry = self._state.by_discord_id.pop(str(discord_id), None)
            if entry is not None:
                self._save_locked()
            return entry

    def all(self) -> list[TokenAssignment]:
        return list(self._state.by_discord_id.values())
