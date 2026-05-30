"""Inspect a Minecraft world's level.dat for mod information.

Forge/NeoForge worlds save the list of mods active at last save into level.dat
under various keys (the schema has shifted across versions). We load the file
and walk the NBT tree to find any mod-list-shaped data — robust to schema drift.

If the Frag companion mod is installed it writes a richer JSON snapshot to
``<world>/frag/world-info.json``; that file is preferred when present because
recent NeoForge versions no longer persist a usable mod registry in level.dat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nbtlib

FRAG_INFO_REL = Path("frag") / "world-info.json"


@dataclass
class WorldInfo:
    path: Path
    name: str = ""
    mc_version: str = ""              # e.g. "1.20.4"
    mc_data_version: int = 0
    last_played_ms: int = 0
    loader: str = ""                  # "forge", "neoforge", or ""
    mods: list[dict] = field(default_factory=list)  # [{modid, version}, ...]
    note: str = ""                    # human-readable reason if mods couldn't be read

    @property
    def last_played(self) -> str:
        if not self.last_played_ms:
            return ""
        try:
            return datetime.fromtimestamp(
                self.last_played_ms / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
        except (OSError, ValueError):
            return ""

    @property
    def mod_count(self) -> int:
        return len(self.mods)


def _to_py(node: Any) -> Any:
    """Recursively convert nbtlib tags to plain Python primitives for lookups."""
    if isinstance(node, nbtlib.tag.Compound):
        return {str(k): _to_py(v) for k, v in node.items()}
    if isinstance(node, (nbtlib.tag.List, list, tuple)):
        return [_to_py(x) for x in node]
    if isinstance(node, (nbtlib.tag.String,)):
        return str(node)
    if isinstance(node, (nbtlib.tag.Byte, nbtlib.tag.Short, nbtlib.tag.Int, nbtlib.tag.Long)):
        return int(node)
    if isinstance(node, (nbtlib.tag.Float, nbtlib.tag.Double)):
        return float(node)
    return node


def _walk_compounds(node: Any, path: tuple[str, ...] = ()):
    """Yield (path, dict) for every compound-shaped node in the tree."""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk_compounds(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_compounds(v, path + (f"[{i}]",))


def _looks_like_mod_entry(d: dict) -> tuple[str, str] | None:
    """If d looks like a single mod entry, return (modid, version). Else None."""
    if not isinstance(d, dict):
        return None
    # Tolerate the various key spellings Forge and NeoForge have used.
    modid_keys = ("ModId", "modId", "modid", "ModID")
    version_keys = ("ModVersion", "modVersion", "modversion", "version", "Version")
    modid = next((str(d[k]) for k in modid_keys if k in d), None)
    if not modid:
        return None
    version = next((str(d[k]) for k in version_keys if k in d), "")
    return modid, version


def _collect_mods(root: dict) -> list[dict]:
    """Find any mod-list-shaped data anywhere in the NBT tree."""
    seen: set[tuple[str, str]] = set()
    mods: list[dict] = []
    for _path, node in _walk_compounds(root):
        entry = _looks_like_mod_entry(node)
        if entry and entry not in seen:
            seen.add(entry)
            mods.append({"modid": entry[0], "version": entry[1]})
    mods.sort(key=lambda m: m["modid"].lower())
    return mods


def _detect_loader(root: dict, mods: list[dict]) -> str:
    flat_keys: list[str] = []
    for path, _node in _walk_compounds(root):
        flat_keys.append("/".join(path).lower())
    blob = " ".join(flat_keys)
    if "neoforge" in blob:
        return "neoforge"
    if "fml" in blob or "forge" in blob:
        return "forge"
    if any(m["modid"].lower() in ("minecraft", "forge", "neoforge") for m in mods):
        return "forge"
    return ""


def _read_frag_companion(world_dir: Path, info: WorldInfo) -> bool:
    """Populate `info` from the Frag mod's world-info.json if present. Returns True on success."""
    companion = world_dir / FRAG_INFO_REL
    if not companion.is_file():
        return False
    try:
        data = json.loads(companion.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        info.note = f"Frag companion file unreadable ({e}); falling back to level.dat."
        return False

    mc = data.get("minecraft") or {}
    loader = data.get("loader") or {}
    world = data.get("world") or {}
    mods = data.get("mods") or []

    info.name = str(world.get("name", info.name))
    info.mc_version = str(mc.get("version", ""))
    info.mc_data_version = int(mc.get("data_version", 0) or 0)
    info.loader = str(loader.get("name", "neoforge"))
    info.mods = [
        {"modid": str(m.get("mod_id", "")), "version": str(m.get("version", ""))}
        for m in mods
        if m.get("mod_id")
    ]
    info.mods.sort(key=lambda m: m["modid"].lower())
    info.note = "Loaded from Frag mod world-info.json."
    return True


def read_world(world_dir: Path) -> WorldInfo:
    info = WorldInfo(path=world_dir, name=world_dir.name)

    # Prefer the Frag mod's companion file when it's there — it's authoritative.
    if _read_frag_companion(world_dir, info):
        # Still try to pull LastPlayed from level.dat for the UI's "last played" chip,
        # since the companion file doesn't track it.
        level_dat = world_dir / "level.dat"
        if level_dat.is_file():
            try:
                nbt_file = nbtlib.load(str(level_dat))
                root = _to_py(nbt_file.root if hasattr(nbt_file, "root") else nbt_file)
                if isinstance(root, dict):
                    if "Data" not in root and "" in root and isinstance(root[""], dict):
                        root = root[""]
                    data = root.get("Data") if isinstance(root.get("Data"), dict) else root
                    info.last_played_ms = int(data.get("LastPlayed", 0) or 0)
            except (OSError, ValueError, EOFError):
                pass
        return info

    level_dat = world_dir / "level.dat"
    if not level_dat.is_file():
        info.note = "No level.dat — not a Minecraft world directory."
        return info

    try:
        nbt_file = nbtlib.load(str(level_dat))
    except (OSError, ValueError, EOFError) as e:
        info.note = f"Could not read level.dat ({e})."
        return info

    root = _to_py(nbt_file.root if hasattr(nbt_file, "root") else nbt_file)
    if not isinstance(root, dict):
        info.note = "Unexpected level.dat structure."
        return info

    # level.dat shape: { "": { "Data": { ... } } } or { "Data": { ... } }
    if "Data" not in root and "" in root and isinstance(root[""], dict):
        root = root[""]
    data = root.get("Data") if isinstance(root.get("Data"), dict) else root

    info.name = str(data.get("LevelName", info.name))
    info.last_played_ms = int(data.get("LastPlayed", 0) or 0)
    info.mc_data_version = int(data.get("DataVersion", 0) or 0)
    version_block = data.get("Version")
    if isinstance(version_block, dict):
        info.mc_version = str(version_block.get("Name", ""))

    info.mods = _collect_mods(data)
    info.loader = _detect_loader(data, info.mods)

    if not info.mods:
        info.note = (
            "No mod registry found in level.dat. Vanilla world, or the loader "
            "doesn't persist mod info there (some NeoForge versions store it "
            "elsewhere)."
        )

    return info
