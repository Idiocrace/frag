"""Account storage abstractions.

`AccountStore` is the interface the auth routes depend on. The Firestore
implementation wraps the project's `FSClient`; the in-memory one is for
tests and local development.
"""

from typing import Protocol, Optional

from auth.passwords import hash_password, is_hashed


class AccountStore(Protocol):
    def get(self, username: str) -> Optional[dict]: ...
    def set(self, username: str, data: dict) -> None: ...
    def all(self) -> list[dict]: ...


def _hash_if_needed(data: dict) -> dict:
    pw = data.get("password")
    if isinstance(pw, str) and not is_hashed(pw):
        data["password"] = hash_password(pw)
    return data


class FirestoreAccountStore:
    """AccountStore backed by the project's FSClient."""

    COLLECTION = "accounts"

    def __init__(self, fsclient):
        self._fs = fsclient

    def get(self, username: str) -> Optional[dict]:
        return self._fs.get(self.COLLECTION, username)

    def set(self, username: str, data: dict) -> None:
        self._fs.set(self.COLLECTION, username, _hash_if_needed(data))

    def all(self) -> list[dict]:
        return self._fs.get_all(self.COLLECTION)


class InMemoryAccountStore:
    """Dict-backed AccountStore for tests and the local dev server."""

    def __init__(self, seed: Optional[dict] = None):
        self._data: dict = dict(seed or {})

    def get(self, username: str) -> Optional[dict]:
        return self._data.get(username)

    def set(self, username: str, data: dict) -> None:
        self._data[username] = _hash_if_needed(data)

    def all(self) -> list[dict]:
        return list(self._data.values())
