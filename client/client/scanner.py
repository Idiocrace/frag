"""Scan Minecraft mod folder, parse Forge/NeoForge metadata, hash + Modrinth lookup."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

MODRINTH_API = "https://api.modrinth.com/v2"
USER_AGENT = "FragModManager/0.1 (frag.client)"
MODRINTH_TIMEOUT = 8

_VERSION_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}")


@dataclass
class ModInfo:
    path: Path
    filename: str
    size: int
    sha1: str
    mod_id: str = ""
    display_name: str = ""
    version: str = ""
    loader: str = ""             # "forge", "neoforge", or ""
    authors: str = ""
    description: str = ""
    # Modrinth-enriched fields (blank if offline / not found)
    modrinth_project_id: str = ""
    modrinth_slug: str = ""
    modrinth_title: str = ""
    modrinth_page_url: str = ""
    # Diagnostic
    metadata_source: str = "none"  # "modrinth", "jar", "filename", "none"
    warnings: list[str] = field(default_factory=list)

    @property
    def best_name(self) -> str:
        return (
            self.modrinth_title
            or self.display_name
            or self.mod_id
            or self.filename
        )

    @property
    def best_version(self) -> str:
        return self.version or "?"


def sha1_of(path: Path, chunk: int = 1024 * 1024) -> str:
    """Compute SHA1 hash of file."""
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if chunk <= 0:
        raise ValueError(f"Chunk size must be positive, got {chunk}")

    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def parse_mod_jar(jar: Path) -> dict:
    """
    Extract mod metadata from mods.toml or neoforge.mods.toml.
    Returns first [[mods]] entry, or empty dict if not found.
    """
    if not jar.is_file():
        return {}

    candidates = ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
    try:
        with zipfile.ZipFile(jar) as zf:
            names = set(zf.namelist())
            for candidate in candidates:
                if candidate not in names:
                    continue
                try:
                    with zf.open(candidate) as f:
                        raw = f.read().decode("utf-8", errors="replace")
                    raw = raw.lstrip("﻿")
                    data = tomllib.loads(raw)
                except (tomllib.TOMLDecodeError, KeyError, UnicodeDecodeError):
                    continue

                mods = data.get("mods") or []
                if not mods or not isinstance(mods[0], dict):
                    continue

                m = mods[0]
                version = str(m.get("version", ""))
                if _VERSION_PLACEHOLDER_RE.search(version):
                    version = ""

                return {
                    "mod_id": str(m.get("modId", "")),
                    "display_name": str(m.get("displayName", "")),
                    "version": version,
                    "authors": str(m.get("authors", "")),
                    "description": str(m.get("description", "")).strip(),
                    "loader": "neoforge" if "neoforge" in candidate else "forge",
                }
    except (zipfile.BadZipFile, OSError):
        return {}

    return {}


def list_jars(mods_dir: Path) -> list[Path]:
    """List all .jar files in a directory."""
    if not mods_dir.is_dir():
        return []
    try:
        return sorted(p for p in mods_dir.iterdir() if p.is_file() and p.suffix.lower() == ".jar")
    except (OSError, PermissionError):
        return []


def modrinth_lookup(hashes: Iterable[str], timeout: float = MODRINTH_TIMEOUT) -> dict[str, dict]:
    """Batch lookup mods by SHA1. Returns {sha1: version_dict}. Empty dict on failure."""
    hashes = [h for h in hashes if h]
    if not hashes:
        return {}
    try:
        resp = requests.post(
            f"{MODRINTH_API}/version_files",
            json={"hashes": hashes, "algorithm": "sha1"},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except (requests.RequestException, ValueError):
        return {}


def modrinth_project(project_id: str, timeout: float = MODRINTH_TIMEOUT) -> dict:
    try:
        resp = requests.get(
            f"{MODRINTH_API}/project/{project_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except (requests.RequestException, ValueError):
        return {}


def scan_mods(mods_dir: Path, use_modrinth: bool = True) -> tuple[list[ModInfo], list[str]]:
    """
    Scan a mod folder.
    Returns (mods, warnings). Warnings include the offline notice if Modrinth was skipped/failed.
    """
    warnings: list[str] = []
    jars = list_jars(mods_dir)
    if not jars:
        return [], warnings

    mods: list[ModInfo] = []
    for jar in jars:
        try:
            size = jar.stat().st_size
        except OSError:
            continue
        info = ModInfo(path=jar, filename=jar.name, size=size, sha1=sha1_of(jar))
        meta = parse_mod_jar(jar)
        if meta:
            info.mod_id = meta["mod_id"]
            info.display_name = meta["display_name"]
            info.version = meta["version"]
            info.loader = meta["loader"]
            info.authors = meta["authors"]
            info.description = meta["description"]
            info.metadata_source = "jar"
        else:
            info.metadata_source = "filename"
            info.warnings.append("No mods.toml found in jar — Forge/NeoForge mod?")
        mods.append(info)

    if not use_modrinth:
        warnings.append("Modrinth lookup disabled.")
        return mods, warnings

    lookup = modrinth_lookup([m.sha1 for m in mods])
    if not lookup:
        warnings.append(
            "No internet — Modrinth lookup unavailable. Showing data from jar manifests only."
        )
        return mods, warnings

    project_cache: dict[str, dict] = {}
    for info in mods:
        version = lookup.get(info.sha1)
        if not version:
            continue
        project_id = version.get("project_id", "")
        info.modrinth_project_id = project_id
        # Modrinth's version object has version_number; project info has slug/title
        if project_id and project_id not in project_cache:
            project_cache[project_id] = modrinth_project(project_id)
        proj = project_cache.get(project_id, {})
        info.modrinth_slug = proj.get("slug", "")
        info.modrinth_title = proj.get("title", "")
        if info.modrinth_slug:
            info.modrinth_page_url = f"https://modrinth.com/mod/{info.modrinth_slug}"
        # If version was unresolved or missing, take Modrinth's version_number as the source of truth
        if not info.version:
            info.version = version.get("version_number", "")
        info.metadata_source = "modrinth"

    return mods, warnings
