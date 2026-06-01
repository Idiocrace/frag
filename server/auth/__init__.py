"""Authorization package for pixelateddream.net.

Exposes a Flask Blueprint plus the building blocks (account store, mailer,
MFA store, permission checks) so they can be reused or replaced by tests.
"""

from auth.accounts import (
    AccountStore,
    FirestoreAccountStore,
    InMemoryAccountStore,
)
from auth.passwords import hash_password, verify_password
from auth.mfa import MFAStore, InMemoryMFAStore
from auth.mailer import Mailer, SMTPMailer, NullMailer
from auth.permissions import is_admin, has_flag
from auth.templates import AccountTemplate
from auth.routes import create_auth_blueprint
from auth.tokens import AuthTokenVerifier, decode_token, validate_token

__all__ = [
    "AccountStore",
    "FirestoreAccountStore",
    "InMemoryAccountStore",
    "hash_password",
    "verify_password",
    "MFAStore",
    "InMemoryMFAStore",
    "Mailer",
    "SMTPMailer",
    "NullMailer",
    "is_admin",
    "has_flag",
    "AccountTemplate",
    "create_auth_blueprint",
    "AuthTokenVerifier",
    "decode_token",
    "validate_token",
]
