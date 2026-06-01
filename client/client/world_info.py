"""Inspect a Minecraft world.

Two data sources, in priority order:

1. ``<world>/frag/world-info.json`` — written by the Frag NeoForge mod on
   server-started and on every overworld save. Authoritative: includes display
   names, descriptions, loader version, gamerules, dimension list.

2. ``<world>/level.dat`` — gzip-compressed NBT. We walk it looking for any
   compound that resembles a mod entry. Tolerant of Forge/NeoForge schema drift
   but does not work on recent NeoForge (no mod registry persisted).

A world that has neither yields an info object with ``source == "none"`` and
a human-readable ``note``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nbtlib

WORLD_INFO_DIR = "frag"
WORLD_INFO_FILE = "world-info.json"


@dataclass
class Mod:
    mod_id: str
    display_name: str = ""
    version: str = ""
    description: str = ""
    namespace: str = ""
    loader: str = ""

    @property
    def best_name(self) -> str:
        return self.display_name or self.mod_id


@dataclass
class WorldInfo:
    path: Path
    name: str = ""
    source: str = "none"          # "frag-mod" | "level.dat" | "none"
    note: str = ""                # human-readable explanation when source is sparse

    # Minecraft / loader
    mc_version: str = ""          # e.g. "1.21.1"
    mc_data_version: int = 0
    mc_release_target: str = ""
    loader: str = ""              # "neoforge" | "forge" | ""
    loader_version: str = ""
    fml_version: str = ""

    # World state
    seed: int | None = None
    game_time: int = 0
    day_time: int = 0
    difficulty: str = ""
    hardcore: bool = False
    game_type: str = ""
    allow_commands: bool = False
    gamerules: dict[str, str] = field(default_factory=dict)
    dimensions: list[str] = field(default_factory=list)
    last_played_ms: int = 0

    mods: list[Mod] = field(default_factory=list)

    # ---- formatted views ----------------------------------------------------

    @property
    def mod_count(self) -> int:
        return len(self.mods)

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
    def source_label(self) -> str:
        return {
            "frag-mod": "Frag mod",
            "level.dat": "level.dat",
            "none": "none",
        }.get(self.source, self.source)


# ---- public entrypoint -------------------------------------------------------


def read_world(world_dir: Path) -> WorldInfo:
    info = WorldInfo(path=world_dir, name=world_dir.name)

    if not world_dir.is_dir():
        info.note = "Not a directory."
        return info

    if _read_from_frag_mod(world_dir, info):
        return info
    if _read_from_level_dat(world_dir, info):
        return info

    if not info.note:
        info.note = "No level.dat — not a Minecraft world directory."
    return info


# ---- frag-mod source ---------------------------------------------------------


def _read_from_frag_mod(world_dir: Path, info: WorldInfo) -> bool:
    path = world_dir / WORLD_INFO_DIR / WORLD_INFO_FILE
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        info.note = f"Found frag/world-info.json but couldn't parse it ({e})."
        return False

    info.source = "frag-mod"

    mc = data.get("minecraft") or {}
    info.mc_version = str(mc.get("version") or "")
    info.mc_data_version = int(mc.get("data_version") or 0)
    info.mc_release_target = str(mc.get("release_target") or "")

    loader = data.get("loader") or {}
    info.loader = str(loader.get("name") or "")
    info.loader_version = str(loader.get("version") or "")
    info.fml_version = str(loader.get("fml_version") or "")

    world = data.get("world") or {}
    info.name = str(world.get("name") or info.name)
    seed = world.get("seed")
    info.seed = int(seed) if isinstance(seed, (int, float)) else None
    info.game_time = int(world.get("game_time") or 0)
    info.day_time = int(world.get("day_time") or 0)
    info.difficulty = str(world.get("difficulty") or "")
    info.hardcore = bool(world.get("hardcore"))
    info.game_type = str(world.get("game_type") or "")
    info.allow_commands = bool(world.get("allow_commands"))
    info.gamerules = {str(k): str(v) for k, v in (world.get("gamerules") or {}).items()}
    info.dimensions = [str(d) for d in (world.get("dimensions") or [])]

    for entry in data.get("mods") or []:
        if not isinstance(entry, dict):
            continue
        info.mods.append(
            Mod(
                mod_id=str(entry.get("mod_id") or ""),
                display_name=str(entry.get("display_name") or ""),
                version=str(entry.get("version") or ""),
                description=str(entry.get("description") or "").strip(),
                namespace=str(entry.get("namespace") or ""),
                loader=str(entry.get("loader") or ""),
            )
        )
    info.mods.sort(key=lambda m: m.mod_id.lower())

    # last_played still comes from level.dat (the mod doesn't write it)
    _enrich_with_level_dat_timestamp(world_dir, info)
    return True


def _enrich_with_level_dat_timestamp(world_dir: Path, info: WorldInfo) -> None:
    """Extract LastPlayed timestamp from level.dat."""
    level_dat = world_dir / "level.dat"
    if not level_dat.is_file():
        return

    try:
        nbt_file = nbtlib.load(str(level_dat))
    except (OSError, ValueError, EOFError, Exception):
        return

    try:
        root = _to_py(nbt_file.root if hasattr(nbt_file, "root") else nbt_file)
        if not isinstance(root, dict):
            return
        data = _unwrap_data(root)
        info.last_played_ms = int(data.get("LastPlayed", 0) or 0)
    except (ValueError, TypeError, KeyError):
        return


# ---- level.dat fallback ------------------------------------------------------


def _read_from_level_dat(world_dir: Path, info: WorldInfo) -> bool:
    level_dat = world_dir / "level.dat"
    if not level_dat.is_file():
        return False

    try:
        nbt_file = nbtlib.load(str(level_dat))
    except (OSError, ValueError, EOFError) as e:
        info.note = f"Could not read level.dat ({e})."
        info.source = "none"
        return True

    root = _to_py(nbt_file.root if hasattr(nbt_file, "root") else nbt_file)
    if not isinstance(root, dict):
        info.note = "Unexpected level.dat structure."
        info.source = "none"
        return True

    info.source = "level.dat"
    data = _unwrap_data(root)

    info.name = str(data.get("LevelName", info.name))
    info.last_played_ms = int(data.get("LastPlayed", 0) or 0)
    info.mc_data_version = int(data.get("DataVersion", 0) or 0)
    version_block = data.get("Version")
    if isinstance(version_block, dict):
        info.mc_version = str(version_block.get("Name", ""))

    info.mods = _collect_mods_from_nbt(data)
    info.loader = _detect_loader_from_nbt(data, info.mods)
    if not info.mods:
        info.note = (
            "No mod registry found in level.dat. Vanilla world, or a newer "
            "NeoForge that doesn't persist mod info here — install the Frag "
            "mod for richer data."
        )
    return True


def _unwrap_data(root: dict) -> dict:
    # level.dat shape: { "": { "Data": { ... } } } or { "Data": { ... } }
    if "Data" not in root and "" in root and isinstance(root[""], dict):
        root = root[""]
    data = root.get("Data") if isinstance(root.get("Data"), dict) else root
    return data if isinstance(data, dict) else {}


def _collect_mods_from_nbt(root: dict) -> list[Mod]:
    seen: set[tuple[str, str]] = set()
    mods: list[Mod] = []
    for _path, node in _walk_compounds(root):
        entry = _looks_like_mod_entry(node)
        if entry and entry not in seen:
            seen.add(entry)
            mods.append(Mod(mod_id=entry[0], version=entry[1]))
    mods.sort(key=lambda m: m.mod_id.lower())
    return mods


def _detect_loader_from_nbt(root: dict, mods: list[Mod]) -> str:
    keys_blob = " ".join(
        "/".join(p).lower() for p, _ in _walk_compounds(root)
    )
    if "neoforge" in keys_blob:
        return "neoforge"
    if "fml" in keys_blob or "forge" in keys_blob:
        return "forge"
    if any(m.mod_id.lower() in ("forge", "neoforge") for m in mods):
        return "forge"
    return ""


# ---- NBT walk helpers --------------------------------------------------------


def _to_py(node: Any) -> Any:
    """Convert nbtlib tags to native Python types."""
    if node is None:
        return None
    if isinstance(node, nbtlib.tag.Compound):
        return {str(k): _to_py(v) for k, v in node.items()}
    if isinstance(node, (nbtlib.tag.List, list, tuple)):
        return [_to_py(x) for x in node]
    if isinstance(node, nbtlib.tag.String):
        return str(node)
    if isinstance(node, (nbtlib.tag.Byte, nbtlib.tag.Short, nbtlib.tag.Int, nbtlib.tag.Long)):
        return int(node)
    if isinstance(node, (nbtlib.tag.Float, nbtlib.tag.Double)):
        return float(node)
    return node


def _walk_compounds(node: Any, path: tuple[str, ...] = ()):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk_compounds(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_compounds(v, path + (f"[{i}]",))


def _looks_like_mod_entry(d: dict) -> tuple[str, str] | None:
    """Check if dict resembles a mod registry entry."""
    if not isinstance(d, dict):
        return None

    modid_keys = ("ModId", "modId", "modid", "ModID", "mod_id")
    version_keys = ("ModVersion", "modVersion", "modversion", "version", "Version")

    modid = next((str(d[k]) for k in modid_keys if k in d), None)
    if not modid:
        return None

    version = next((str(d.get(k, "")) for k in version_keys if k in d), "")
    return modid, version
