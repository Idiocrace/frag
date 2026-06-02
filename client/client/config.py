"""Persistent client configuration."""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import ClassVar


def _config_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "Frag"


def _default_minecraft_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / ".minecraft"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


CONFIG_DIR = _config_root()
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    """Frag client configuration, split into device-local and cloud-synced sections."""

    # ---- device-local (not synced) -----------------------------------------
    server_url: str = "https://pixelateddream.net"
    pd_oauth_url: str = "https://pixelateddream.net"
    access_token: str = ""  # OAuth access token (RS256 JWT)
    access_token_expires_at: float = 0.0
    id_token: str = ""  # OAuth ID token
    refresh_token: str = ""  # OAuth refresh token (if available)
    minecraft_dir: str = ""
    mods_dir: str = ""
    saves_dir: str = ""
    last_mod_sync: float = 0.0
    last_world_sync: float = 0.0
    last_settings_sync: float = 0.0
    known_mods: dict = field(default_factory=dict)
    device_name: str = ""

    # ---- synced (replicated across devices) --------------------------------
    sync_worlds: bool = False
    mod_sync_excludes: list = field(default_factory=list)
    world_sync_excludes: list = field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.is_file():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        else:
            data = {}

        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        cfg._apply_defaults()
        return cfg

    def _apply_defaults(self) -> None:
        mc_default = _default_minecraft_dir()
        if not self.minecraft_dir:
            self.minecraft_dir = str(mc_default)
        if not self.mods_dir:
            self.mods_dir = str(Path(self.minecraft_dir) / "mods")
        if not self.saves_dir:
            self.saves_dir = str(Path(self.minecraft_dir) / "saves")
        if not self.device_name:
            try:
                self.device_name = socket.gethostname() or "device"
            except OSError:
                self.device_name = "device"

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @property
    def mods_path(self) -> Path:
        return Path(self.mods_dir)

    @property
    def saves_path(self) -> Path:
        return Path(self.saves_dir)

    # ---- selection helpers --------------------------------------------------

    def is_mod_synced(self, sha1: str) -> bool:
        return sha1 not in set(self.mod_sync_excludes)

    def set_mod_synced(self, sha1: str, synced: bool) -> None:
        excludes = set(self.mod_sync_excludes)
        if synced:
            excludes.discard(sha1)
        else:
            excludes.add(sha1)
        self.mod_sync_excludes = sorted(excludes)

    def is_world_synced(self, world_name: str) -> bool:
        return world_name not in set(self.world_sync_excludes)

    def set_world_synced(self, world_name: str, synced: bool) -> None:
        excludes = set(self.world_sync_excludes)
        if synced:
            excludes.discard(world_name)
        else:
            excludes.add(world_name)
        self.world_sync_excludes = sorted(excludes)

    # ---- subsets ------------------------------------------------------------

    SYNCED_FIELDS: ClassVar[tuple[str, ...]] = (
        "sync_worlds",
        "mod_sync_excludes",
        "world_sync_excludes",
    )

    def synced_payload(self) -> dict:
        return {k: getattr(self, k) for k in self.SYNCED_FIELDS}

    def apply_synced_payload(self, payload: dict) -> None:
        for k in self.SYNCED_FIELDS:
            if k in payload:
                setattr(self, k, payload[k])
