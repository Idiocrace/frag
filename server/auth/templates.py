"""Account document template handling.

The template is stored in Firestore so it can evolve without a deploy.
This module isolates the loading + per-registration cloning so the
template document is never mutated in place (a bug in the original code).
"""

from copy import deepcopy
from typing import Optional


DEFAULT_ICON = "https://pixelateddream.net/images/bacon.png"


_FALLBACK_TEMPLATE = {
    "email": "",
    "password": "",
    "flags": {},
    "profile": {
        "display_name": "",
        "summary": "",
        "description": "",
        "icon": DEFAULT_ICON,
        "socials": {},
        "banner": {"color": "", "image": ""},
        "private": True,
    },
    "mfa": {"enabled": False, "pending": False, "verified": False},
    "friends": {"outgoing": [], "incoming": [], "active": [], "blocked": []},
    "assets": {"credit": 0, "inventory": []},
}


class AccountTemplate:
    """Loads and clones the account template document."""

    def __init__(self, template: Optional[dict] = None):
        self._template = template or _FALLBACK_TEMPLATE

    @classmethod
    def from_fsclient(cls, fsclient, doc_id: str = "template-v3.0") -> "AccountTemplate":
        loaded = fsclient.get("accounts", doc_id)
        return cls(loaded or _FALLBACK_TEMPLATE)

    def new_account(self, *, username: str, email: str, password_hash: str) -> dict:
        """Return a fresh account dict ready to be persisted."""
        account = deepcopy(self._template)
        account["email"] = email
        account["password"] = password_hash

        profile = account.setdefault("profile", {})
        profile["display_name"] = username
        profile.setdefault("icon", DEFAULT_ICON)
        profile["private"] = True
        profile["flags"] = {
            "details": "This user has recently registered.",
            "permissions": {},
            "rank": None,
        }
        return account
