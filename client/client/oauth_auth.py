"""PD OAuth 2.0 authentication for Frag desktop client.

Uses Authorization Code + PKCE flow with local loopback redirect.
Tokens are RS256-signed JWTs that can be verified locally.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
import json

import jwt
import requests

DEFAULT_PD_URL = "https://pixelateddream.net"
OAUTH_CLIENT_ID = "frag-desktop"
OAUTH_SCOPE = "openid profile email frag.sync"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"


class OAuthError(Exception):
    """Raised on OAuth flow errors."""


class OAuthConfig:
    """OAuth configuration for a PD instance."""

    def __init__(self, pd_url: str = DEFAULT_PD_URL):
        self.pd_url = pd_url.rstrip("/")
        self._config_cache: Optional[dict] = None
        self._jwks_cache: Optional[dict] = None

    def discovery(self) -> dict:
        """Fetch OpenID configuration."""
        if self._config_cache:
            return self._config_cache
        resp = requests.get(
            f"{self.pd_url}/.well-known/openid-configuration",
            timeout=10,
        )
        resp.raise_for_status()
        self._config_cache = resp.json()
        return self._config_cache

    def jwks(self) -> dict:
        """Fetch public signing keys."""
        if self._jwks_cache:
            return self._jwks_cache
        resp = requests.get(
            f"{self.pd_url}/.well-known/jwks.json",
            timeout=10,
        )
        resp.raise_for_status()
        self._jwks_cache = resp.json()
        return self._jwks_cache

    def authorize_endpoint(self) -> str:
        return self.discovery()["authorization_endpoint"]

    def token_endpoint(self) -> str:
        return self.discovery()["token_endpoint"]


def _pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def _generate_state() -> str:
    """Generate a random state parameter."""
    return secrets.token_urlsafe(32)


class OAuthAuthenticator:
    """OAuth authentication flow handler."""

    def __init__(self, config: OAuthConfig):
        self.config = config
        self.state: Optional[str] = None
        self.nonce: Optional[str] = None

    def authorize_url(self) -> str:
        """Generate the authorization URL for the user to visit."""
        self.state = _generate_state()
        self.nonce = secrets.token_urlsafe(16)
        verifier, challenge = _pkce_pair()
        self._pkce_verifier = verifier

        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "redirect_uri": REDIRECT_URI,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        return f"{self.config.authorize_endpoint()}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, state: str) -> dict:
        """Exchange authorization code for tokens."""
        if state != self.state:
            raise OAuthError("State mismatch (CSRF attack?)")

        if not hasattr(self, "_pkce_verifier"):
            raise OAuthError("PKCE verifier not set. Call authorize_url() first.")

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": OAUTH_CLIENT_ID,
            "code_verifier": self._pkce_verifier,
        }

        resp = requests.post(
            self.config.token_endpoint(),
            data=token_data,
            timeout=10,
        )
        resp.raise_for_status()
        tokens = resp.json()

        self._verify_id_token(tokens.get("id_token", ""))
        return tokens

    def _verify_id_token(self, token_str: str) -> dict:
        """Verify and decode the ID token."""
        try:
            header = jwt.get_unverified_header(token_str)
            kid = header.get("kid")

            jwks = self.config.jwks()
            key = None
            for k in jwks.get("keys", []):
                if k.get("kid") == kid:
                    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                    break

            if not key:
                raise OAuthError(f"Signing key '{kid}' not found in JWKS")

            claims = jwt.decode(
                token_str,
                key,
                algorithms=["RS256"],
                audience=OAUTH_CLIENT_ID,
                options={"verify_exp": True},
            )

            if claims.get("nonce") != self.nonce:
                raise OAuthError("Nonce mismatch")

            return claims
        except jwt.PyJWTError as e:
            raise OAuthError(f"Token verification failed: {e}")


class OAuthCallbackServer:
    """Local HTTP server to receive OAuth callback."""

    def __init__(self, host: str = REDIRECT_HOST, port: int = REDIRECT_PORT):
        self.host = host
        self.port = port
        self.code: Optional[str] = None
        self.state: Optional[str] = None
        self.error: Optional[str] = None
        self._http: Optional[HTTPServer] = None
        self._done = threading.Event()

    def prepare(self) -> None:
        """Bind the socket so the port is ready *before* the browser navigates back."""
        self._http = HTTPServer((self.host, self.port), self._make_handler())
        self._http.timeout = 1  # short so wait() can poll _done

    def wait(self, timeout: float = 300.0) -> None:
        """Block until the callback is received or *timeout* seconds pass."""
        if self._http is None:
            self.prepare()
        deadline = time.monotonic() + timeout
        while not self._done.is_set() and time.monotonic() < deadline:
            self._http.handle_request()

    def start(self) -> None:
        """Convenience wrapper: prepare + wait (original single-call API)."""
        self.prepare()
        self.wait()

    def _make_handler(self):
        """Create the request handler class."""
        server = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_error(404)
                    return

                query = urllib.parse.parse_qs(parsed.query)
                server.code = query.get("code", [None])[0]
                server.state = query.get("state", [None])[0]
                server.error = query.get("error", [None])[0]

                if server.error:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        f"<h1>Authentication Error</h1><p>{server.error}</p>".encode()
                    )
                elif server.code:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<h1>Success!</h1><p>You can close this window and return to Frag.</p>"
                    )
                else:
                    self.send_error(400, "Missing code")

                server._done.set()

            def log_message(self, format, *args):
                pass

        return CallbackHandler
