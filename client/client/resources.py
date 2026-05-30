"""Resolve bundled asset paths in dev and PyInstaller-frozen modes."""

from __future__ import annotations

import sys
from pathlib import Path


def _asset_root() -> Path:
    # PyInstaller (onefile) sets _MEIPASS to the extracted temp dir.
    # PyInstaller (onedir) places bundled data under _internal/ next to the exe;
    # we set sys._MEIPASS too in that mode, so the same lookup works.
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    # Dev mode: this file is in client/, assets are sibling at project root.
    return Path(__file__).resolve().parent.parent


def asset(name: str) -> Path:
    return _asset_root() / "assets" / name


ICON_PNG = "icon.png"
ICON_ICO = "icon.ico"
