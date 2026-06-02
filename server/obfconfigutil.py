"""Generate / load obfuscated camouflaged config files.

The generated ``.py`` is a plain module exposing a single dict under a
randomly-named identifier.  The dict contains the real entries (with
obfuscated keys) mixed in with hundreds of decoys whose values look like
real tokens, URLs, ARNs, JWTs, PEM blocks, etc.

For every real value we also seed ``MIN_SHAPE_SIBLINGS`` extra decoys of
the *same shape*, so a real ``pd_<40hex>`` key is one of many in the file
rather than a lone needle.

The ``.secrets`` JSON file is the only place that records (a) the dict's
variable name, (b) which obfuscated keys correspond to which real names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import randomutil


_MISSING = object()

# How many extra decoys to generate per real-entry shape (on top of the
# baseline pool).  Higher = better camouflage at the cost of file size.
MIN_SHAPE_SIBLINGS = 40

# Baseline pool of mixed-shape decoys.
BASELINE_DECOYS = 1000


# ---------------------------------------------------------------------------
# Public path helper
# ---------------------------------------------------------------------------


def get_current_abspath() -> str:
    """Return the directory of this file for use as a project-relative base path."""
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_key_map(names: list[str]) -> dict[str, str]:
    """Return ``{original_name: obfuscated_identifier}``."""
    ids = randomutil.unique_identifiers(len(names))
    return dict(zip(names, ids))


def _build_decoys(real_values: list[Any], reserved_keys: set[str]) -> dict[str, Any]:
    """Return ``{obf_key: decoy_value}`` for the camouflage pool.

    Strategy:
      - For each real value whose shape is recognised, generate
        ``MIN_SHAPE_SIBLINGS`` decoys of that exact shape.
      - On top of that, fill out a baseline of mixed-shape decoys so the
        file doesn't look suspiciously tilted toward one shape.
    """
    decoys: dict[str, Any] = {}
    used = set(reserved_keys)

    def _fresh_key() -> str:
        while True:
            cand = randomutil.python_identifier(16)
            if cand not in used:
                used.add(cand)
                return cand

    # Per-shape siblings for each real value.
    for value in real_values:
        shape = randomutil.shape_of(value)
        if shape is None:
            continue
        gen = randomutil.DECOY_GENERATORS[shape]
        for _ in range(MIN_SHAPE_SIBLINGS):
            decoys[_fresh_key()] = gen()

    # Baseline of varied decoys.
    for _ in range(BASELINE_DECOYS):
        decoys[_fresh_key()] = randomutil.random_decoy()

    return decoys


def _render_py(
    real_entries: dict[str, Any],
    decoy_entries: dict[str, Any],
    dict_var: str,
) -> str:
    """Render a .py module exposing a single camouflaged dict."""
    import random  # local import: only used for shuffle, randomutil owns entropy

    combined = list(real_entries.items()) + list(decoy_entries.items())
    random.shuffle(combined)

    items = ",\n    ".join(f"{repr(k)}: {repr(v)}" for k, v in combined)
    return (
        "# ruff: noqa\n"
        f"{dict_var} = {{\n    {items},\n}}\n"
    )


def _render_secrets(
    data: dict[str, Any], key_map: dict[str, str], dict_var: str
) -> dict:
    return {
        "version": 2,
        "dict_var": dict_var,
        "entries": {
            name: {"obfuscated_key": key_map[name], "value": data[name]}
            for name in data
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(
    data: dict[str, Any],
    py_path: str | Path,
    secrets_path: str | Path,
) -> None:
    """Generate both output files, overwriting any existing ones."""
    py_path = Path(py_path)
    secrets_path = Path(secrets_path)

    key_map = _build_key_map(list(data.keys()))
    real_entries = {key_map[name]: value for name, value in data.items()}

    decoy_entries = _build_decoys(
        real_values=list(data.values()),
        reserved_keys=set(real_entries),
    )

    dict_var = randomutil.python_identifier(16)

    py_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)

    py_path.write_text(
        _render_py(real_entries, decoy_entries, dict_var), encoding="utf-8"
    )
    secrets_path.write_text(
        json.dumps(_render_secrets(data, key_map, dict_var), indent=2),
        encoding="utf-8",
    )


def load(py_path: str | Path, secrets_path: str | Path) -> dict[str, Any]:
    """Exec the generated ``.py`` and return ``{original_name: value}``."""
    raw = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
    dict_var = raw["dict_var"]
    entries = raw["entries"]

    source = Path(py_path).read_text(encoding="utf-8")
    namespace: dict = {}
    exec(compile(source, str(py_path), "exec"), namespace)  # nosec: controlled internal file
    blob = namespace.get(dict_var)
    if blob is None:
        raise ValueError(f"No dict named {dict_var!r} in {py_path!r}")

    return {name: blob[entry["obfuscated_key"]] for name, entry in entries.items()}


def load_secrets(secrets_path: str | Path) -> dict[str, Any]:
    """Read the ``.secrets`` file and return ``{original_name: value}``."""
    raw = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
    return {name: entry["value"] for name, entry in raw["entries"].items()}


def get_or_create(
    data: dict[str, Any],
    py_path: str | Path,
    secrets_path: str | Path,
) -> dict[str, Any]:
    """Return ``{original_name: value}``, generating both files if missing."""
    py_path = Path(py_path)
    secrets_path = Path(secrets_path)

    if not py_path.exists() or not secrets_path.exists():
        generate(data, py_path, secrets_path)

    return load(py_path, secrets_path)


# ---------------------------------------------------------------------------
# Quality-of-life wrapper
# ---------------------------------------------------------------------------


class Config:
    """Convenience wrapper around the loaded ``{name: value}`` map.

    Usage::

        cfg = Config.from_files("out/config.py", "out/.secrets")
        api_key = cfg.get("pd-api-key")
        api_key = cfg["pd-api-key"]            # same thing
        for name in cfg: ...                   # iterate names
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    # --- constructors ------------------------------------------------------

    @classmethod
    def from_files(cls, py_path: str | Path, secrets_path: str | Path) -> "Config":
        return cls(load(py_path, secrets_path))

    @classmethod
    def from_secrets(cls, secrets_path: str | Path) -> "Config":
        """Load directly from ``.secrets`` (skips the camouflaged .py)."""
        return cls(load_secrets(secrets_path))

    # --- access ------------------------------------------------------------

    def get(self, name: str, default: Any = _MISSING) -> Any:
        if name in self._data:
            return self._data[name]
        if default is _MISSING:
            raise KeyError(f"Unknown config key: {name!r}")
        return default

    def __getitem__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise KeyError(f"Unknown config key: {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._data

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def __repr__(self) -> str:
        return f"Config(keys={sorted(self._data)!r})"
