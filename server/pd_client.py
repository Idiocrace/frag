"""Thin client for the Pixelated Dream API.

Frag server uses a single PD API key (env var ``FRAG_PD_API_KEY``) to talk to
pdsite for:

  * starting login flows on behalf of the client
  * polling for login completion
  * validating existing brokered sessions

The PD API key is created by registering "Frag (server)" at
https://pixelateddream.net/account/apps (requires APIAccess permission).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_PD_BASE_URL = "https://pixelateddream.net"
DEFAULT_TIMEOUT = 15


class PDClientError(Exception):
    """Raised on any non-success PD API response."""


class PDClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key or os.environ.get("FRAG_PD_API_KEY", "")
        if not self.api_key:
            raise PDClientError(
                "FRAG_PD_API_KEY env var is required. Register an app at "
                f"{DEFAULT_PD_BASE_URL}/account/apps and set the key."
            )
        self.base_url = (base_url or os.environ.get("FRAG_PD_BASE_URL") or DEFAULT_PD_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": self.api_key,
            "User-Agent": "frag-server/1.0",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Login broker
    # ------------------------------------------------------------------

    def start_login(self) -> dict:
        """Ask PD to begin a login. Returns dict with ``handle``, ``authorize_url``, ``expires_in``."""
        resp = self.session.post(
            f"{self.base_url}/api/v1/auth/login-url",
            timeout=self.timeout,
        )
        return self._json_or_raise(resp)

    def poll_login(self, handle: str) -> dict:
        """Poll the result of a login. Returns ``{status: pending|approved|denied|expired, ...}``."""
        resp = self.session.get(
            f"{self.base_url}/api/v1/auth/login-result/{handle}",
            timeout=self.timeout,
        )
        return self._json_or_raise(resp)

    def validate_session(self, token: str) -> dict:
        """Validate a brokered session token. Returns ``{valid: bool, user_id, username, expires_at}``."""
        resp = self.session.get(
            f"{self.base_url}/api/v1/auth/session/{token}",
            timeout=self.timeout,
        )
        # 404 / 403 are not raised — they're meaningful negative answers
        if resp.status_code in (200, 403, 404):
            return resp.json()
        raise PDClientError(self._format_error(resp))

    def revoke_session(self, token: str) -> None:
        resp = self.session.delete(
            f"{self.base_url}/api/v1/auth/session/{token}",
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 404):
            raise PDClientError(self._format_error(resp))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _json_or_raise(self, resp: requests.Response) -> dict:
        if resp.status_code >= 300:
            raise PDClientError(self._format_error(resp))
        try:
            return resp.json()
        except ValueError:
            raise PDClientError(f"PD returned non-JSON ({resp.status_code}): {resp.text[:200]}")

    def _format_error(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            msg = data.get("error") or data.get("message") or resp.text[:200]
        except ValueError:
            msg = resp.text[:200]
        return f"PD API {resp.status_code}: {msg}"
