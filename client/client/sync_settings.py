"""Replicate sync-relevant settings across devices via the Frag bucket.

The settings payload (``frag-settings.zip`` → ``settings.json``) carries only
fields that make sense to share: world-sync toggle and the per-mod / per-world
exclude lists. Device-specific things (server URL, auth token, local paths) are
never replicated.
"""

from __future__ import annotations

import json
import socket
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .api import FragClient
from .config import Config

SETTINGS_FILENAME = "frag-settings.zip"
INNER_NAME = "settings.json"
PAYLOAD_VERSION = 1


@dataclass
class RemoteSettings:
    payload: dict
    synced_at: float = 0.0
    synced_from: str = ""

    @property
    def synced_at_str(self) -> str:
        if not self.synced_at:
            return ""
        from datetime import datetime, timezone

        return datetime.fromtimestamp(self.synced_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )


def _build_envelope(cfg: Config) -> dict:
    return {
        "version": PAYLOAD_VERSION,
        "synced_from": cfg.device_name or socket.gethostname(),
        "synced_at": time.time(),
        "settings": cfg.synced_payload(),
    }


def push_settings(client: FragClient, cfg: Config) -> RemoteSettings:
    """Bundle the synced fields, upload, persist last_settings_sync."""
    envelope = _build_envelope(cfg)
    tmp = Path(tempfile.gettempdir()) / SETTINGS_FILENAME
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(INNER_NAME, json.dumps(envelope, indent=2))
        client.upload_zip(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    cfg.last_settings_sync = envelope["synced_at"]
    cfg.save()
    return RemoteSettings(
        payload=envelope["settings"],
        synced_at=envelope["synced_at"],
        synced_from=envelope["synced_from"],
    )


def pull_settings(client: FragClient, cfg: Config) -> RemoteSettings | None:
    """Download settings from the bucket and apply them. Returns None if absent."""
    files = client.list_files()
    if SETTINGS_FILENAME not in files:
        return None

    tmp = Path(tempfile.gettempdir()) / SETTINGS_FILENAME
    try:
        client.download_file(SETTINGS_FILENAME, tmp)
        with zipfile.ZipFile(tmp) as zf:
            with zf.open(INNER_NAME) as f:
                envelope = json.load(f)
    finally:
        tmp.unlink(missing_ok=True)

    payload = envelope.get("settings", {})
    cfg.apply_synced_payload(payload)
    cfg.last_settings_sync = time.time()
    cfg.save()
    return RemoteSettings(
        payload=payload,
        synced_at=float(envelope.get("synced_at", 0.0)),
        synced_from=str(envelope.get("synced_from", "")),
    )
