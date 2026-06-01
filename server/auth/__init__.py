"""Authorization package for pixelateddream.net.

Exposes a Flask Blueprint plus the building blocks (account store, mailer,
MFA store, permission checks) so they can be reused or replaced by tests.
"""

from server.auth.accounts import (
    AccountStore,
    FirestoreAccountStore,
    InMemoryAccountStore,
)
from server.auth.passwords import hash_password, verify_password
from server.auth.mfa import MFAStore, InMemoryMFAStore
from server.auth.mailer import Mailer, SMTPMailer, NullMailer
from server.auth.permissions import is_admin, has_flag
from server.auth.templates import AccountTemplate
from server.auth.routes import create_auth_blueprint

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
]
