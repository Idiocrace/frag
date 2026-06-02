"""HTTP client for the PD API (Frag sync endpoints).

Points to the Pixelated Dream API at  /api/v1/frag/  by default.
Set  server_url  in the client config to a local pdsite instance for dev.
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
TOKEN_REFRESH_MARGIN = 5 * 60  # refresh if <5 min remaining

_FRAG_PREFIX = "/api/v1/frag"


class FragAPIError(Exception):
    """Raised on any non-success Frag API response."""


class FragClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    # ---- auth ---------------------------------------------------------------

    def _need_token_refresh(self) -> bool:
        if not self.cfg.access_token:
            return True
        return time.time() + TOKEN_REFRESH_MARGIN >= self.cfg.access_token_expires_at

    def validate_token(self) -> bool:
        if not self.cfg.access_token:
            return False
        return not self._need_token_refresh()

    def _authed(self) -> dict:
        if self._need_token_refresh():
            raise FragAPIError(
                "Access token missing or expired. Re-authenticate via Settings."
            )
        return {"Authorization": f"Bearer {self.cfg.access_token}"}

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
        """Ask the PD API whether a newer version of Frag is available."""
        resp = self.session.get(
            f"{self.cfg.server_url.rstrip('/')}/api/v1/software/frag/check-update",
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
