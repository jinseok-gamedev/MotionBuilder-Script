"""Preset I/O - persist alignment results so they can be reused across sessions.

A "preset" is a JSON file capturing the offsets that the aligner produced
for a particular pair of characters, plus the options that produced them.
This lets a studio build up a small library of character-pair calibrations
and reapply them with a single click whenever the same source/target pair
returns (the most common production pattern).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .snapshot import OffsetSnapshot, snapshot_to_dict, snapshot_from_dict


PRESET_VERSION = 1


@dataclass
class Preset:
    """Persisted alignment for a character pair."""

    source_character: str
    target_character: str
    options: Dict[str, object] = field(default_factory=dict)
    source_snapshot: Optional[OffsetSnapshot] = None
    target_snapshot: Optional[OffsetSnapshot] = None
    notes: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    version: int = PRESET_VERSION


_INVALID_FILENAME = re.compile(r"[^\w\-.]+")


def _sanitize(name: str) -> str:
    """Strip namespaces and unsafe characters from a character name."""
    bare = name.rsplit(":", 1)[-1] if ":" in name else name
    return _INVALID_FILENAME.sub("_", bare).strip("_") or "unnamed"


def default_presets_dir() -> Path:
    """Return the bundled presets directory next to this package."""
    return Path(__file__).resolve().parent.parent / "presets"


def _resolve_dir(directory: Optional[Path]) -> Path:
    target = Path(directory) if directory else default_presets_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target


def preset_filename(source_name: str, target_name: str) -> str:
    return f"{_sanitize(source_name)}__{_sanitize(target_name)}.json"


def save_preset(
    preset: Preset,
    directory: Optional[Path] = None,
    overwrite: bool = True,
) -> Path:
    """Serialize ``preset`` to ``directory`` and return the file path."""
    out_dir = _resolve_dir(directory)
    path = out_dir / preset_filename(preset.source_character, preset.target_character)
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))

    payload = {
        "version": preset.version,
        "source_character": preset.source_character,
        "target_character": preset.target_character,
        "options": preset.options,
        "notes": preset.notes,
        "created": preset.created,
        "source_snapshot": snapshot_to_dict(preset.source_snapshot) if preset.source_snapshot else None,
        "target_snapshot": snapshot_to_dict(preset.target_snapshot) if preset.target_snapshot else None,
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path


def load_preset(path: Path) -> Preset:
    """Load a preset file from disk."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    version = int(data.get("version", PRESET_VERSION))
    if version > PRESET_VERSION:
        raise ValueError(
            f"Preset {path} is version {version}, this build supports up to {PRESET_VERSION}"
        )

    src = data.get("source_snapshot")
    tgt = data.get("target_snapshot")
    return Preset(
        source_character=str(data.get("source_character", "")),
        target_character=str(data.get("target_character", "")),
        options=dict(data.get("options") or {}),
        source_snapshot=snapshot_from_dict(src) if src else None,
        target_snapshot=snapshot_from_dict(tgt) if tgt else None,
        notes=str(data.get("notes", "")),
        created=str(data.get("created", datetime.now().isoformat())),
        version=version,
    )


def list_presets(directory: Optional[Path] = None) -> List[Path]:
    """Sorted list of every ``.json`` preset file in ``directory``."""
    out_dir = _resolve_dir(directory)
    return sorted(p for p in out_dir.glob("*.json") if p.is_file())


def find_preset_for_pair(
    source_name: str,
    target_name: str,
    directory: Optional[Path] = None,
) -> Optional[Path]:
    """Return the preset path matching this character pair, if any."""
    out_dir = _resolve_dir(directory)
    candidate = out_dir / preset_filename(source_name, target_name)
    return candidate if candidate.exists() else None


def apply_preset(preset: Preset, source_character, target_character) -> Tuple[int, int]:
    """Apply a preset's snapshots to live characters.

    Returns ``(num_source_offsets, num_target_offsets)`` so the caller can
    log how many bones were touched.
    """
    from .snapshot import restore

    n_src = 0
    n_tgt = 0
    if preset.source_snapshot is not None and source_character is not None:
        restore(source_character, preset.source_snapshot)
        n_src = len(preset.source_snapshot.rotations)
    if preset.target_snapshot is not None and target_character is not None:
        restore(target_character, preset.target_snapshot)
        n_tgt = len(preset.target_snapshot.rotations)
    return n_src, n_tgt
