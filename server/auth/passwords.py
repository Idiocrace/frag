"""Password hashing and verification."""

from passlib.hash import argon2


ARGON2_PREFIX = "$argon2"


def hash_password(plaintext: str) -> str:
    return argon2.hash(plaintext)


def is_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(ARGON2_PREFIX)


def verify_password(plaintext: str, stored: str) -> tuple[bool, bool]:
    """Verify plaintext against a stored password.

    Returns (is_valid, needs_upgrade). `needs_upgrade` is True when the
    stored value was legacy plaintext and matched directly, so the caller
    can re-hash and persist.
    """
    if is_hashed(stored):
        return argon2.verify(plaintext, stored), False
    if plaintext == stored:
        return True, True
    return False, False
