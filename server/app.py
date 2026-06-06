"""Frag backend — file sync + brokered PD auth.

Architecture
------------
Frag clients never talk to PD directly. They authenticate to *this server*,
which in turn brokers everything through PD's login-broker API using a single
``FRAG_PD_API_KEY`` env var (created at /account/apps on pixelateddream.net).

Auth flow
---------
1. POST /frag/v1/auth/start
   → frag server calls PD's POST /api/v1/auth/login-url with its API key
   → returns ``{handle, authorize_url, expires_in}`` to the client

2. Client opens ``authorize_url`` in a browser
   → user signs into PD, sees the consent screen, clicks Allow

3. GET /frag/v1/auth/poll?handle=<handle>
   → frag server polls PD's GET /api/v1/auth/login-result/<handle>
   → once approved, returns ``{status: "approved", session_token, user_id, expires_at}``

4. All subsequent calls send ``Authorization: Bearer <session_token>``
   → frag server validates via PD (cached locally for SESSION_CACHE_TTL),
     then serves the request

File sync, settings replication, listing, deletion — all unchanged from the
previous build; the only change is how ``g.user`` gets populated.
"""

from __future__ import annotations

import logging
import os
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse


import requests
from flask import Flask, abort, g, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from libs.pdcli import PDClient, PDClientError
from libs.session_cache import CachedSession, SessionCache
from libs.supporter_tokens import (
    SupporterTokenError,
    SupporterTokenStore,
    TokenAlreadyClaimed,
    TokenNotClaimedByUser,
    UnknownToken,
)

from config import (
    PD_API_KEY,
    USER_DATA_ROOT,
    SESSION_CACHE_PATH,
    SUPPORTER_TOKENS_PATH,
    MAX_FILE_BYTES,
    USER_QUOTA_BYTES,
    SUPPORTER_BONUS_BYTES,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_WALL_TIMEOUT,
    USER_ID_RE,
)


log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Flask app + extensions
# ----------------------------------------------------------------------


app = Flask("frag_backend")
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_BYTES

USER_DATA_ROOT.mkdir(parents=True, exist_ok=True)  # type: ignore
SESSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)  # type: ignore

pd = PDClient(PD_API_KEY)  # type: ignore
sessions = SessionCache(SESSION_CACHE_PATH)  # type: ignore
supporter_tokens = SupporterTokenStore(SUPPORTER_TOKENS_PATH)  # type: ignore


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def user_dir(user_id: str) -> Path:
    if not USER_ID_RE.match(user_id):
        abort(400, description="Invalid user id")
    return USER_DATA_ROOT / user_id  # type: ignore


def user_usage_bytes(user_id: str) -> int:
    """Total bytes the user currently holds in their directory."""
    d = user_dir(user_id)
    if not d.is_dir():
        return 0
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def user_quota_bytes(user_id: str) -> int:
    """Base quota + any supporter-token bonuses currently held by the user."""
    return USER_QUOTA_BYTES + supporter_tokens.bonus_for_user(
        user_id, SUPPORTER_BONUS_BYTES
    )


def quota_remaining(user_id: str) -> int:
    return max(user_quota_bytes(user_id) - user_usage_bytes(user_id), 0)


def _quota_error(user_id: str, attempted: int) -> tuple:
    """JSON 507 response body when a write would exceed the user's quota."""
    used = user_usage_bytes(user_id)
    quota = user_quota_bytes(user_id)
    return (
        jsonify(
            {
                "error": "Storage quota exceeded",
                "quota_bytes": quota,
                "base_quota_bytes": USER_QUOTA_BYTES,
                "supporter_bonus_bytes": supporter_tokens.bonus_for_user(
                    user_id, SUPPORTER_BONUS_BYTES
                ),
                "used_bytes": used,
                "remaining_bytes": max(quota - used, 0),
                "attempted_bytes": attempted,
            }
        ),
        507,
    )


def _bearer_token() -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    return auth[len("Bearer ") :].strip()


def _validate_token(token: str) -> CachedSession | None:
    """Return a fresh CachedSession or None. Hits PD when the cache is cold or stale."""
    cached = sessions.get(token)
    if cached is not None:
        if cached.is_pd_expired:
            sessions.drop(token)
            return None
        if not cached.needs_revalidation:
            return cached

    try:
        result = pd.validate_session(token)
    except PDClientError as exc:
        log.warning("PD session validation failed: %s", exc)
        # On transient PD errors, fall back to cache if it was still valid by PD's clock
        if cached is not None and not cached.is_pd_expired:
            return cached
        return None

    if not result.get("valid"):
        sessions.drop(token)
        return None

    fresh = CachedSession(
        token=token,
        user_id=result["user_id"],
        username=result.get("username", ""),
        expires_at=_parse_iso_to_epoch(result.get("expires_at", "")),
        cached_at=time.time(),
    )
    sessions.put(fresh)
    return fresh


def _parse_iso_to_epoch(iso: str) -> float:
    from datetime import datetime

    if not iso:
        return time.time() + 24 * 3600  # defensive default
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time() + 24 * 3600


def login_required(f):
    """Populate g.user from the bearer token; reject if missing/invalid/expired."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token:
            return jsonify(
                {"error": "Missing Authorization: Bearer <session_token>"}
            ), 401
        session = _validate_token(token)
        if session is None:
            return jsonify({"error": "Invalid or expired session"}), 401
        g.user = session
        return f(*args, **kwargs)

    return wrapper


# ----------------------------------------------------------------------
# Public routes
# ----------------------------------------------------------------------


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@app.route("/frag/v1/ping", methods=["POST", "GET"])
def ping():
    return jsonify({"message": "pong", "service": "frag-backend"})


# ----------------------------------------------------------------------
# Auth routes (brokered through PD)
# ----------------------------------------------------------------------


@app.route("/frag/v1/auth/start", methods=["POST"])
def auth_start():
    try:
        result = pd.start_login()
    except PDClientError as exc:
        log.error("PD login start failed: %s", exc)
        return jsonify({"error": "Failed to start login", "detail": str(exc)}), 502
    return jsonify(
        {
            "handle": result["handle"],
            "authorize_url": result["authorize_url"],
            "expires_in": result.get("expires_in", 600),
        }
    )


@app.route("/frag/v1/auth/poll", methods=["GET"])
def auth_poll():
    handle = request.args.get("handle", "").strip()
    if not handle:
        return jsonify({"error": "Query param 'handle' is required"}), 400

    try:
        result = pd.poll_login(handle)
    except PDClientError as exc:
        log.error("PD login poll failed: %s", exc)
        return jsonify({"error": "Login poll failed", "detail": str(exc)}), 502

    status = result.get("status", "pending")
    if status != "approved":
        return jsonify({"status": status})

    # Cache the freshly issued session so the next call doesn't pay a round-trip
    fresh = CachedSession(
        token=result["session_token"],
        user_id=result["user_id"],
        username="",  # filled in on first validate_session
        expires_at=_parse_iso_to_epoch(result.get("expires_at", "")),
        cached_at=time.time(),
    )
    sessions.put(fresh)

    return jsonify(
        {
            "status": "approved",
            "session_token": result["session_token"],
            "user_id": result["user_id"],
            "expires_at": result.get("expires_at"),
        }
    )


@app.route("/frag/v1/auth/whoami", methods=["GET"])
@login_required
def auth_whoami():
    return jsonify(
        {
            "user_id": g.user.user_id,
            "username": g.user.username,
            "expires_at": g.user.expires_at,
        }
    )


@app.route("/frag/v1/auth/logout", methods=["POST"])
@login_required
def auth_logout():
    token = g.user.token
    try:
        pd.revoke_session(token)
    except PDClientError as exc:
        log.warning("PD session revoke failed: %s", exc)
    sessions.drop(token)
    return jsonify({"revoked": True})


# ----------------------------------------------------------------------
# File sync (unchanged semantics, login_required swaps in)
# ----------------------------------------------------------------------


@app.route("/frag/v1/files/", methods=["GET"])
@app.route("/frag/v1/files/<path:filename>", methods=["GET"])
@login_required
def get_file(filename: str = ""):
    user_id = g.user.user_id
    dest_dir = user_dir(user_id)

    if not filename:
        if not dest_dir.is_dir():
            return jsonify({"user_id": user_id, "files": []})
        files = sorted(p.name for p in dest_dir.iterdir() if p.is_file())
        return jsonify({"user_id": user_id, "files": files})

    return send_from_directory(str(dest_dir), filename, as_attachment=True)


@app.route("/frag/v1/upload", methods=["POST"])
@login_required
def upload_file():
    user_id = g.user.user_id
    upload = request.files.get("file")

    if upload is None or not upload.filename:
        return jsonify({"error": "Multipart field 'file' is required"}), 400

    filename = secure_filename(upload.filename)
    if not filename.lower().endswith(".zip"):
        filename += ".zip"

    dest_dir = user_dir(user_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename

    # If we're overwriting an existing file the old bytes will be freed, so
    # don't count them against the user's current usage when checking quota.
    existing = dest.stat().st_size if dest.is_file() else 0
    remaining = max(user_quota_bytes(user_id) - user_usage_bytes(user_id) + existing, 0)

    # Pre-check via Content-Length so we reject huge uploads before saving.
    declared = request.content_length
    if declared is not None and declared > remaining:
        return _quota_error(user_id, declared)

    upload.save(str(dest))

    size = dest.stat().st_size
    if size > MAX_FILE_BYTES:
        dest.unlink(missing_ok=True)
        return jsonify({"error": f"File exceeds {MAX_FILE_BYTES} bytes"}), 413

    # Post-check in case the client lied about Content-Length.
    if size > remaining:
        dest.unlink(missing_ok=True)
        return _quota_error(user_id, size)

    return jsonify(
        {
            "message": "Upload successful",
            "user_id": user_id,
            "filename": filename,
            "size": size,
            "quota_bytes": user_quota_bytes(user_id),
            "used_bytes": user_usage_bytes(user_id),
        }
    )


@app.route("/frag/v1/save-data", methods=["POST"])
@login_required
def save_data():
    user_id = g.user.user_id
    payload = request.get_json(silent=True) or {}
    links = payload.get("files") or payload.get("links")

    if (
        not isinstance(links, list)
        or len(links) != 2
        or not all(isinstance(u, str) for u in links)
    ):
        return jsonify({"error": "Expected JSON body with 'files': [url1, url2]"}), 400

    dest_dir = user_dir(user_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Shared budget across both downloads in this request — second URL must
    # respect the first one's bytes.  Mutable holder so _download_zip can
    # decrement it as bytes land.
    budget = [quota_remaining(user_id)]

    try:
        saved = [_download_zip(url, dest_dir, budget) for url in links]
    except _QuotaExceeded as exc:
        return _quota_error(user_id, exc.attempted)
    except requests.RequestException as exc:
        return jsonify({"error": f"Download failed: {exc}"}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"message": "Data saved", "user_id": user_id, "files": saved})


@app.route("/frag/v1/quota", methods=["GET"])
@login_required
def quota_status():
    user_id = g.user.user_id
    used = user_usage_bytes(user_id)
    quota = user_quota_bytes(user_id)
    bonus = supporter_tokens.bonus_for_user(user_id, SUPPORTER_BONUS_BYTES)
    held = supporter_tokens.tokens_for_user(user_id)
    return jsonify(
        {
            "user_id": user_id,
            "quota_bytes": quota,
            "base_quota_bytes": USER_QUOTA_BYTES,
            "supporter_bonus_bytes": bonus,
            "supporter_tokens_held": len(held),
            "used_bytes": used,
            "remaining_bytes": max(quota - used, 0),
        }
    )


@app.route("/frag/v1/supporter/claim", methods=["POST"])
@login_required
def supporter_claim():
    """Bind a supporter token to the calling user.

    Body: ``{"token": "fragsup_..."}``.  Returns the updated quota or 409
    if the token is already held by someone else, 404 if unknown.
    """
    user_id = g.user.user_id
    payload = request.get_json(silent=True) or {}
    token = (payload.get("token") or "").strip()
    if not token:
        return jsonify({"error": "Missing 'token' in body"}), 400

    try:
        supporter_tokens.claim(token, user_id)
    except UnknownToken:
        return jsonify({"error": "Unknown supporter token"}), 404
    except TokenAlreadyClaimed as exc:
        # Don't leak the other user's id to clients.
        log.info(
            "Supporter token claim conflict for user=%s already_held_by=%s",
            user_id,
            exc.claimed_by,
        )
        return jsonify({"error": "Token already in use on another account"}), 409
    except SupporterTokenError as exc:
        return jsonify({"error": str(exc)}), 400

    used = user_usage_bytes(user_id)
    quota = user_quota_bytes(user_id)
    return jsonify(
        {
            "message": "Supporter token claimed",
            "user_id": user_id,
            "quota_bytes": quota,
            "base_quota_bytes": USER_QUOTA_BYTES,
            "supporter_bonus_bytes": supporter_tokens.bonus_for_user(
                user_id, SUPPORTER_BONUS_BYTES
            ),
            "supporter_tokens_held": len(supporter_tokens.tokens_for_user(user_id)),
            "used_bytes": used,
            "remaining_bytes": max(quota - used, 0),
        }
    )


@app.route("/frag/v1/supporter/release", methods=["POST"])
@login_required
def supporter_release():
    """Release a supporter token currently held by the calling user.

    Body: ``{"token": "fragsup_..."}``.  Omitting the token releases every
    token held by the user.
    """
    user_id = g.user.user_id
    payload = request.get_json(silent=True) or {}
    token = (payload.get("token") or "").strip()

    if not token:
        freed = supporter_tokens.release_user(user_id)
        return jsonify({"message": f"Released {freed} token(s)", "released": freed})

    try:
        supporter_tokens.release(token, user_id)
    except UnknownToken:
        return jsonify({"error": "Unknown supporter token"}), 404
    except TokenNotClaimedByUser:
        return jsonify({"error": "Token is not currently held by you"}), 403

    return jsonify({"message": "Supporter token released", "released": 1})


@app.route("/frag/v1/files/<path:filename>", methods=["DELETE"])
@login_required
def delete_file(filename: str):
    user_id = g.user.user_id
    dest_dir = user_dir(user_id).resolve()
    target = (dest_dir / filename).resolve()

    try:
        target.relative_to(dest_dir)
    except ValueError:
        return jsonify({"error": "Forbidden"}), 403

    if not target.is_file():
        return jsonify({"error": "Not found"}), 404

    target.unlink()
    return jsonify({"message": "Deleted", "filename": filename})


# ----------------------------------------------------------------------
# Private helpers
# ----------------------------------------------------------------------


class _QuotaExceeded(Exception):
    """Raised when a streamed download would push the user over quota."""

    def __init__(self, attempted: int):
        super().__init__(f"User storage quota would be exceeded ({attempted} bytes)")
        self.attempted = attempted


def _download_zip(url: str, dest_dir: Path, budget: list[int]) -> str:
    """Stream *url* to *dest_dir*, charging the bytes against *budget*[0].

    *budget* is a single-element mutable list so this function can decrement
    the caller's remaining quota in place across successive downloads.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")

    filename = secure_filename(os.path.basename(parsed.path) or "file.zip")
    if not filename.lower().endswith(".zip"):
        filename += ".zip"

    dest = dest_dir / filename
    deadline = time.monotonic() + DOWNLOAD_WALL_TIMEOUT

    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
        resp.raise_for_status()

        # Up-front reject if the server advertised a Content-Length we
        # already know exceeds the user's remaining quota.
        try:
            advertised = int(resp.headers.get("Content-Length", "") or 0)
        except ValueError:
            advertised = 0
        if advertised and advertised > budget[0]:
            raise _QuotaExceeded(advertised)

        total = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if time.monotonic() > deadline:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise requests.Timeout(f"Download timed out: {url}")
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError(f"File exceeds {MAX_FILE_BYTES} bytes: {url}")
                if total > budget[0]:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise _QuotaExceeded(total)
                fh.write(chunk)

    budget[0] -= total
    return filename


# ----------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from waitress import serve

    port = int(os.environ.get("PORT", 4543))
    log.info("Frag backend serving on http://0.0.0.0:%d", port)
    log.info("PD base URL: %s", pd.base_url)
    log.info("User data root: %s", USER_DATA_ROOT)
    serve(app, host="0.0.0.0", port=port)
