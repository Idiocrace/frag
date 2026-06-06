"""Supporter-token store.

Each token grants the user who claims it a +50 GiB quota bonus.  Only one
user may hold a given token at a time (prevents naive token sharing).
A user releases the token (or gets booted off it by an admin) before
someone else can claim the same token.

File format on disk (JSON)::

    {
      "tokens": {
        "<token>": {
          "token": "<token>",
          "created_at": 1700000000.0,
          "claimed_by": "<user_id> | null",
          "claimed_at": 1700000000.0 | null,
          "note": "freeform admin note"
        },
        ...
      }
    }

The token strings themselves are never logged.  The store is JSON-backed
with the same write-tmp-then-rename atomic pattern as ``SessionCache``.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# How many bytes a single claimed supporter token is worth.
SUPPORTER_BONUS_BYTES = 50 * 1024 * 1024 * 1024  # 50 GiB

# Token format: 32 url-safe characters, prefixed so they're recognisable
# in logs and easy to grep for in support tickets.
TOKEN_PREFIX = "fragsup_"
TOKEN_BODY_BYTES = 24  # ~32 chars after urlsafe encoding


class SupporterTokenError(Exception):
    """Base class for claim / release failures."""


class UnknownToken(SupporterTokenError):
    pass


class TokenAlreadyClaimed(SupporterTokenError):
    def __init__(self, claimed_by: str):
        super().__init__(f"Token already claimed by {claimed_by!r}")
        self.claimed_by = claimed_by


class TokenNotClaimedByUser(SupporterTokenError):
    pass


@dataclass
class SupporterToken:
    token: str
    created_at: float
    claimed_by: Optional[str] = None
    claimed_at: Optional[float] = None
    note: str = ""

    @property
    def is_claimed(self) -> bool:
        return self.claimed_by is not None


@dataclass
class _State:
    tokens: dict[str, SupporterToken] = field(default_factory=dict)


def new_token_string() -> str:
    """Return a fresh, never-before-issued token string."""
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BODY_BYTES)


class SupporterTokenStore:
    """JSON-backed store of supporter tokens + their current claim."""

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
        for tok, entry in (raw.get("tokens") or {}).items():
            try:
                self._state.tokens[tok] = SupporterToken(**entry)
            except TypeError:
                continue

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = {
            "tokens": {tok: asdict(entry) for tok, entry in self._state.tokens.items()},
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    # ---- mutators -------------------------------------------------------

    def issue(self, note: str = "") -> SupporterToken:
        """Generate and persist a brand-new unclaimed token."""
        with self._lock:
            # Vanishingly unlikely to collide but be defensive anyway.
            while True:
                tok = new_token_string()
                if tok not in self._state.tokens:
                    break
            entry = SupporterToken(
                token=tok,
                created_at=time.time(),
                note=note,
            )
            self._state.tokens[tok] = entry
            self._save_locked()
            return entry

    def issue_many(self, count: int, note: str = "") -> list[SupporterToken]:
        return [self.issue(note=note) for _ in range(count)]

    def revoke(self, token: str) -> None:
        """Delete a token entirely.  Releases the current claim if any."""
        with self._lock:
            if token not in self._state.tokens:
                raise UnknownToken(token)
            del self._state.tokens[token]
            self._save_locked()

    def claim(self, token: str, user_id: str) -> SupporterToken:
        """Bind *token* to *user_id*.

        - If *user_id* already holds *token*, that's a no-op.
        - If someone else holds it, raises :class:`TokenAlreadyClaimed`.
        """
        with self._lock:
            entry = self._state.tokens.get(token)
            if entry is None:
                raise UnknownToken(token)
            if entry.claimed_by and entry.claimed_by != user_id:
                raise TokenAlreadyClaimed(entry.claimed_by)
            entry.claimed_by = user_id
            entry.claimed_at = time.time()
            self._save_locked()
            return entry

    def release(self, token: str, user_id: str) -> SupporterToken:
        """Free *token* so it can be claimed by another user."""
        with self._lock:
            entry = self._state.tokens.get(token)
            if entry is None:
                raise UnknownToken(token)
            if entry.claimed_by != user_id:
                raise TokenNotClaimedByUser(
                    f"Token is not currently claimed by {user_id!r}"
                )
            entry.claimed_by = None
            entry.claimed_at = None
            self._save_locked()
            return entry

    def release_user(self, user_id: str) -> int:
        """Release every token held by *user_id*.  Returns the count freed."""
        freed = 0
        with self._lock:
            for entry in self._state.tokens.values():
                if entry.claimed_by == user_id:
                    entry.claimed_by = None
                    entry.claimed_at = None
                    freed += 1
            if freed:
                self._save_locked()
        return freed

    def force_release(self, token: str) -> bool:
        """Free *token*'s current claim, regardless of who holds it.

        Admin-style: used when the token's owner (e.g. via the Discord
        bot) wants to free their claim without proving the holding
        frag user_id.  Returns True if the token existed and was
        claimed; False if unknown or already free.
        """
        with self._lock:
            entry = self._state.tokens.get(token)
            if entry is None:
                return False
            if entry.claimed_by is None:
                return False
            entry.claimed_by = None
            entry.claimed_at = None
            self._save_locked()
            return True

    # ---- readers --------------------------------------------------------

    def get(self, token: str) -> Optional[SupporterToken]:
        return self._state.tokens.get(token)

    def tokens_for_user(self, user_id: str) -> list[SupporterToken]:
        return [t for t in self._state.tokens.values() if t.claimed_by == user_id]

    def bonus_for_user(self, user_id: str, bonus_bytes: int) -> int:
        """Total bonus bytes from every token currently held by *user_id*."""
        return sum(
            bonus_bytes for t in self._state.tokens.values() if t.claimed_by == user_id
        )

    def all_tokens(self) -> Iterable[SupporterToken]:
        return list(self._state.tokens.values())
