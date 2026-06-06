"""Tiny wrapper around PD's published-catalog API.

Used by the Frag client to ask PD "is there a newer Frag than the one
I'm running?" and (eventually) "where do I download it?".  Lives apart
from :mod:`client.api` because that module talks to the Frag *server*
(``frag.pixelateddream.net/frag/v1``) while this one talks to the PD
*platform* (``pixelateddream.net/api/v1``); different base URLs, no
auth needed for the public catalog endpoints.

We hand-roll the HTTP here instead of going through the `pdapi` SDK
because the SDK's v0.1 resource set doesn't include the software
catalog yet.  Migration to ``pdapi.software.check_update(...)`` is a
v0.4 follow-up once we publish pdapi v0.2 with that resource.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from client import __version__


# Where PD lives.  Overridable for staging deployments.
DEFAULT_PD_BASE_URL = "https://pixelateddream.net/api/v1"
DEFAULT_TIMEOUT_SECONDS = 6

# The product slug Frag was published under in PD's catalog.  This must
# match the slug created when Frag was added at /account/apps/<id>/publish.
FRAG_SLUG = "frag"


log = logging.getLogger(__name__)


@dataclass
class UpdateCheckResult:
    update_available: bool
    latest_version: str
    current_version: str
    download_url: str = ""
    notes: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> "UpdateCheckResult":
        return cls(
            update_available=bool(payload.get("update_available")),
            latest_version=str(payload.get("latest_version", "")),
            current_version=str(payload.get("current_version", "")),
            download_url=str(payload.get("download_url", "")),
            notes=str(payload.get("notes", "")),
        )


class PDCatalogClient:
    """Read-only client for PD's public catalog endpoints.

    Methods are blocking — call from a worker thread, not the UI loop.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_PD_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: Optional[str] = None,
    ):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._ua = user_agent or f"frag-client/{__version__}"

    def _headers(self) -> dict:
        return {"User-Agent": self._ua, "Accept": "application/json"}

    def check_update(
        self,
        *,
        current_version: str = __version__,
        slug: str = FRAG_SLUG,
    ) -> Optional[UpdateCheckResult]:
        """Ask PD whether *current_version* of *slug* is outdated.

        Returns ``None`` on any network failure — the caller should
        treat "couldn't check" the same as "no update available" so a
        flaky network doesn't nag the user.
        """
        url = f"{self._base}/software/{slug}/check-update"
        try:
            resp = requests.get(
                url, params={"current_version": current_version},
                headers=self._headers(), timeout=self._timeout,
            )
        except requests.RequestException as exc:
            log.debug("PD update check failed: %s", exc)
            return None

        if resp.status_code == 404:
            # Catalog entry doesn't exist (yet?) — treat as "no update".
            return None
        if resp.status_code != 200:
            log.debug(
                "PD update check returned %d: %s",
                resp.status_code, resp.text[:200],
            )
            return None

        try:
            data = resp.json()
        except ValueError:
            return None
        return UpdateCheckResult.from_payload(data)
