"""HTTP client for the Frag server API."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import requests

from .config import Config

USER_AGENT = "FragModdingClient/v1"  # server requires this exact UA
DEFAULT_TIMEOUT = 30
JWT_REFRESH_MARGIN = 5 * 60  # refresh if <5min remaining


class FragAPIError(Exception):
    """Raised on any non-success Frag API response."""


class FragClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    # ---- auth ---------------------------------------------------------------

    def _need_auth(self) -> bool:
        if not self.cfg.jwt:
            return True
        return time.time() + JWT_REFRESH_MARGIN >= self.cfg.jwt_expires_at

    def authenticate(self, force: bool = False) -> None:
        if not force and not self._need_auth():
            return
        if not self.cfg.auth_token:
            raise FragAPIError("No auth token set. Configure it in Settings.")
        url = f"{self.cfg.server_url.rstrip('/')}/frag/v1/authenticate"
        resp = self.session.post(url, params={"token": self.cfg.auth_token}, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        data = resp.json()
        jwt_token = data.get("jwt")
        if not jwt_token:
            raise FragAPIError("Server response missing 'jwt'.")
        self.cfg.jwt = jwt_token
        # Server-side JWT is 24h; refresh slightly before that
        self.cfg.jwt_expires_at = time.time() + (24 * 3600) - JWT_REFRESH_MARGIN
        self.cfg.save()

    def _authed(self) -> dict:
        self.authenticate()
        return {"Authorization": f"Bearer {self.cfg.jwt}"}

    # ---- low-level ----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.cfg.server_url.rstrip('/')}{path}"

    def _format_error(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            return f"{resp.status_code}: {data.get('message') or data.get('error') or resp.text[:200]}"
        except ValueError:
            return f"{resp.status_code}: {resp.text[:200]}"

    # ---- endpoints ----------------------------------------------------------

    def ping(self) -> bool:
        try:
            resp = self.session.post(
                self._url("/frag/v1/ping"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_files(self) -> list[str]:
        resp = self.session.get(
            self._url("/frag/v1/files/"), headers=self._authed(), timeout=DEFAULT_TIMEOUT
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))
        return resp.json().get("files", [])

    def upload_zip(
        self,
        zip_path: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Multipart upload. progress(bytes_sent, total) is optional."""
        if not zip_path.is_file():
            raise FragAPIError(f"File not found: {zip_path}")

        total = zip_path.stat().st_size
        url = self._url("/frag/v1/upload")
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
        url = self._url(f"/frag/v1/files/{filename}")
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
            self._url(f"/frag/v1/files/{filename}"),
            headers=self._authed(),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise FragAPIError(self._format_error(resp))


class _ProgressReader:
    """File-like wrapper that reports read progress to a callback (for requests multipart)."""

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
        # requests uses len() to set Content-Length for non-chunked uploads
        return self._total


# ---- bundling helpers (used by the UI) --------------------------------------


def zip_directory(
    src_dir: Path,
    out_zip: Path,
    items: Iterable[Path] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """
    Zip the contents of src_dir into out_zip. If `items` is given, only those files/dirs
    (must be inside src_dir) are included. Paths inside the zip are relative to src_dir.
    """
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
    """Extract a zip into dest_dir. Returns list of extracted member names."""
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
