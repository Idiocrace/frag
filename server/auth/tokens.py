"""Access-token validation for Frag API requests.

Primary path: RS256 OAuth access tokens issued by PD (OIDC provider).
Fallback path: legacy HS256 tokens (optional, for old clients).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import jwt
import requests


@dataclass
class TokenValidationConfig:
    pd_oauth_url: str
    oauth_issuer: str
    oauth_audience: str
    jwks_ttl_seconds: int = 3600
    allow_legacy_hs256: bool = True
    legacy_secret: str = ""


class AuthTokenVerifier:
    def __init__(self, config: TokenValidationConfig):
        self._cfg = config
        self._jwks_cache: Optional[dict] = None
        self._jwks_cache_time = 0.0

    def decode(self, token: str) -> Optional[dict]:
        claims = self._decode_pd_oauth(token)
        if claims is not None:
            return self._normalize_claims(claims)
        if self._cfg.allow_legacy_hs256:
            claims = self._decode_legacy_hs256(token)
            if claims is not None:
                return self._normalize_claims(claims)
        return None

    def validate(self, token: str) -> bool:
        claims = self.decode(token)
        return bool(claims and claims.get("user_id"))

    def _decode_pd_oauth(self, token: str) -> Optional[dict]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None

        kid = header.get("kid")
        if not kid:
            return None

        jwk = self._find_jwk(kid)
        if jwk is None:
            return None

        try:
            key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            kwargs = {
                "algorithms": ["RS256"],
                "issuer": self._cfg.oauth_issuer,
                "options": {
                    "verify_exp": True,
                    "verify_aud": bool(self._cfg.oauth_audience),
                },
            }
            if self._cfg.oauth_audience:
                kwargs["audience"] = self._cfg.oauth_audience
            return jwt.decode(token, key, **kwargs)
        except jwt.PyJWTError:
            return None

    def _decode_legacy_hs256(self, token: str) -> Optional[dict]:
        if not self._cfg.legacy_secret:
            return None
        try:
            return jwt.decode(token, self._cfg.legacy_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None

    def _get_jwks(self) -> dict:
        now = time.time()
        if (
            self._jwks_cache
            and (now - self._jwks_cache_time) < self._cfg.jwks_ttl_seconds
        ):
            return self._jwks_cache

        try:
            resp = requests.get(
                f"{self._cfg.pd_oauth_url.rstrip('/')}/.well-known/jwks.json",
                timeout=5,
            )
            resp.raise_for_status()
            self._jwks_cache = resp.json()
            self._jwks_cache_time = now
            return self._jwks_cache
        except Exception:
            return self._jwks_cache or {"keys": []}

    def _find_jwk(self, kid: str) -> Optional[dict]:
        for jwk in self._get_jwks().get("keys", []):
            if jwk.get("kid") == kid:
                return jwk
        return None

    @staticmethod
    def _normalize_claims(claims: dict) -> dict:
        normalized = dict(claims)
        user_id = (
            normalized.get("user_id")
            or normalized.get("sub")
            or normalized.get("frag_user_id")
        )
        if user_id:
            normalized["user_id"] = user_id
        return normalized


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _build_default_verifier() -> AuthTokenVerifier:
    pd_oauth_url = os.environ.get("PD_OAUTH_URL", "https://pixelateddream.net")
    return AuthTokenVerifier(
        TokenValidationConfig(
            pd_oauth_url=pd_oauth_url,
            oauth_issuer=os.environ.get("PD_OAUTH_ISSUER", pd_oauth_url),
            oauth_audience=os.environ.get("FRAG_OAUTH_AUDIENCE", "frag-desktop"),
            jwks_ttl_seconds=int(os.environ.get("FRAG_OAUTH_JWKS_TTL", "3600")),
            allow_legacy_hs256=_env_bool("FRAG_ALLOW_LEGACY_HS256", True),
            legacy_secret=os.environ.get("FRAG_SECRET_KEY", ""),
        )
    )


_DEFAULT_VERIFIER = _build_default_verifier()


def decode_token(token: str) -> Optional[dict]:
    return _DEFAULT_VERIFIER.decode(token)


def validate_token(token: str) -> bool:
    return _DEFAULT_VERIFIER.validate(token)
