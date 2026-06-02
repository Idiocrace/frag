"""HTTP client for the Frag backend.

The Frag client never talks to pixelateddream.net directly. Auth is brokered:

  1. ``start_auth()``   → asks the frag backend to begin a login. Backend calls
                          PD, returns ``{handle, authorize_url}``.
  2. Client opens ``authorize_url`` in the user's browser.
  3. ``poll_auth(handle)`` → returns pending/approved/denied. On approval, the
                              backend returns a ``session_token`` we store in
                              the local config.
  4. Every other call sends ``Authorization: Bearer <session_token>``.

Configure ``server_url`` to point at your frag backend
(e.g. ``https://frag.pixelateddream.net`` in prod, ``http://localhost:4543``
for local dev).
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import requests

from .config import Config

USER_AGENT = "FragModdingClient/v1"
DEFAULT_TIMEOUT = 30

_FRAG_PREFIX = "/frag/v1"


class FragAPIError(Exception):
    """Raised on any non-success Frag backend response."""


class FragAuthRequired(FragAPIError):
    """Raised when the cached session is missing/expired and the user must sign in."""


class FragClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    # ---- auth ---------------------------------------------------------------

    def is_authed(self) -> bool:
        """True if we have a session token and it isn't past its expiry."""
        if not self.cfg.session_token:
            return False
        if self.cfg.session_expires_at and time.time() >= self.cfg.session_expires_at:
            return False
        return True

    def _authed(self) -> dict:
        if not self.is_authed():
            raise FragAuthRequired(
                "No active session. Sign in via Settings."
            )
        return {"Authorization": f"Bearer {self.cfg.session_token}"}

    def start_auth(self) -> dict:
        """Ask the Frag backend to begin a login. Returns {handle, authorize_url, expires_in}."""
        resp = self.session.post(
            self._url("/auth/start"),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        return resp.json()

    def poll_auth(self, handle: str) -> dict:
        """Poll a login. Returns {status, session_token?, user_id?, expires_at?}."""
        resp = self.session.get(
            self._url("/auth/poll"),
            params={"handle": handle},
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        result = resp.json()
        if result.get("status") == "approved":
            self._store_session(result)
        return result

    def whoami(self) -> dict:
        resp = self.session.get(
            self._url("/auth/whoami"),
            headers=self._authed(),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        return resp.json()

    def logout(self) -> None:
        """Tell the backend to revoke our session and clear local state."""
        if self.is_authed():
            try:
                self.session.post(
                    self._url("/auth/logout"),
                    headers=self._authed(),
                    timeout=DEFAULT_TIMEOUT,
                )
            except requests.RequestException:
                pass
        self._clear_session()

    def _store_session(self, payload: dict) -> None:
        self.cfg.session_token = payload.get("session_token", "")
        self.cfg.session_user_id = payload.get("user_id", "")
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, str) and expires_at:
            try:
                from datetime import datetime
                self.cfg.session_expires_at = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                self.cfg.session_expires_at = time.time() + 30 * 24 * 3600
        else:
            self.cfg.session_expires_at = time.time() + 30 * 24 * 3600
        self.cfg.save()

    def _clear_session(self) -> None:
        self.cfg.session_token = ""
        self.cfg.session_user_id = ""
        self.cfg.session_expires_at = 0.0
        self.cfg.save()

    # ---- low-level ----------------------------------------------------------

    def _url(self, path: str) -> str:
        base = self.cfg.server_url.rstrip("/")
        return f"{base}{_FRAG_PREFIX}{path}"

    def _format_error(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            return f"{resp.status_code}: {data.get('message') or data.get('error') or resp.text[:200]}"
        except ValueError:
            return f"{resp.status_code}: {resp.text[:200]}"

    # ---- endpoints ----------------------------------------------------------

    def ping(self) -> bool:
        try:
            resp = self.session.post(self._url("/ping"), timeout=10)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_files(self) -> list[str]:
        resp = self.session.get(
            self._url("/files/"), headers=self._authed(), timeout=DEFAULT_TIMEOUT
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        return resp.json().get("files", [])

    def upload_zip(
        self,
        zip_path: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        if not zip_path.is_file():
            raise FragAPIError(f"File not found: {zip_path}")

        total = zip_path.stat().st_size
        url = self._url("/upload")
        headers = self._authed()

        with open(zip_path, "rb") as fh:
            reader = _ProgressReader(fh, total, progress)
            files = {"file": (zip_path.name, reader, "application/zip")}
            resp = self.session.post(url, files=files, headers=headers, timeout=None)

        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        return resp.json()

    def download_file(
        self,
        filename: str,
        dest: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        url = self._url(f"/files/{filename}")
        with self.session.get(url, headers=self._authed(), stream=True, timeout=None) as resp:
            if resp.status_code != 200:
                raise FragAPIError(self._format_error(resp))
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        return dest

    def delete_file(self, filename: str) -> None:
        resp = self.session.delete(
            self._url(f"/files/{filename}"),
            headers=self._authed(),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))

    def check_update(self, current_version: str) -> dict:
        """Ask PD whether a newer version of Frag is available.

        This hits PD's public distribution API directly (no auth required) since
        catalog reads are public.
        """
        pd_base = "https://pixelateddream.net"
        resp = self.session.get(
            f"{pd_base}/api/v1/software/frag/check-update",
            params={"v": current_version},
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        return resp.json()


class _ProgressReader:
    def __init__(self, fh, total: int, callback: Callable[[int, int], None] | None):
        self._fh = fh
        self._total = total
        self._read = 0
        self._cb = callback

    def read(self, size: int = -1) -> bytes:
        data = self._fh.read(size)
        if data:
            self._read += len(data)
            if self._cb:
                self._cb(self._read, self._total)
        return data

    def __len__(self) -> int:
        return self._total


# ---- bundling helpers -------------------------------------------------------


def zip_directory(
    src_dir: Path,
    out_zip: Path,
    items: Iterable[Path] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if items is None:
        files = [p for p in src_dir.rglob("*") if p.is_file()]
    else:
        files = []
        for it in items:
            if it.is_file():
                files.append(it)
            elif it.is_dir():
                files.extend(p for p in it.rglob("*") if p.is_file())

    total = len(files)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, p in enumerate(files, 1):
            zf.write(p, arcname=p.relative_to(src_dir))
            if progress:
                progress(i, total)
    return out_zip


def unzip_into(
    zip_path: Path,
    dest_dir: Path,
    progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        total = len(members)
        for i, name in enumerate(members, 1):
            zf.extract(name, dest_dir)
            extracted.append(name)
            if progress:
                progress(i, total)
    return extracted
