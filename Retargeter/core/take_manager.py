"""Take lifecycle helpers.

The retargeter creates one new take per imported FBX, named after the file. If
the user imports two files with the same stem the second take is automatically
suffixed ``_01``, ``_02``, ... so nothing is silently overwritten.

It also supports a "clean import" mode that removes all existing animation
takes before the batch starts. MotionBuilder requires at least one take in the
scene at all times, so the cleaner reuses the first take as a placeholder and
empties it instead of deleting it.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Set

from pyfbsdk import (  # type: ignore
    FBSystem,
    FBTake,
)


_INVALID_TAKE_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def take_name_from_fbx_path(path: str) -> str:
    """Filename without extension, sanitised for use as a take name."""
    stem = os.path.splitext(os.path.basename(path))[0]
    cleaned = _INVALID_TAKE_CHARS.sub("_", stem).strip("_")
    return cleaned or "imported"


def all_takes() -> List[FBTake]:
    return list(FBSystem().Scene.Takes)


def all_take_names() -> List[str]:
    return [t.Name for t in all_takes()]


def get_take_by_name(name: str) -> Optional[FBTake]:
    for t in all_takes():
        if t.Name == name:
            return t
    return None


def unique_take_name(base: str, existing: Optional[Iterable[str]] = None) -> str:
    """Return ``base``, or ``base_01``, ``base_02``, ... until unused."""
    names: Set[str] = set(existing if existing is not None else all_take_names())
    if base not in names:
        return base
    idx = 1
    while True:
        candidate = f"{base}_{idx:02d}"
        if candidate not in names:
            return candidate
        idx += 1


def set_current_take(take: FBTake) -> None:
    FBSystem().CurrentTake = take


def create_take(name: str) -> FBTake:
    """Create a new empty take with the given (already unique) name.

    ``FBTake(name)`` constructs the object and MotionBuilder registers it in
    ``FBSystem().Scene.Takes`` automatically.
    """
    return FBTake(name)


def rename_take(take: FBTake, new_name: str) -> None:
    take.Name = new_name


def clean_all_takes(keep_name: str = "_retarget_tmp") -> FBTake:
    """Remove every take in the scene and return a single empty placeholder.

    MotionBuilder will not allow zero takes in a scene, so rather than
    iterating and deleting (which is fragile) we rename the first take and
    delete the rest.
    """
    takes = all_takes()
    if not takes:
        new_take = create_take(keep_name)
        FBSystem().CurrentTake = new_take
        return new_take

    placeholder = takes[0]
    placeholder.Name = unique_take_name(keep_name)
    FBSystem().CurrentTake = placeholder
    for t in takes[1:]:
        try:
            t.FBDelete()
        except Exception:
            pass
    return placeholder


def delete_take(take: FBTake) -> None:
    try:
        take.FBDelete()
    except Exception:
        pass


def takes_added_since(snapshot_names: Iterable[str]) -> List[FBTake]:
    """Return takes whose name is NOT in the provided snapshot.

    Used by the FBX importer: snapshot take names before ``FileMerge``, then
    compute the diff to see which takes the merge brought in.
    """
    snap = set(snapshot_names)
    return [t for t in all_takes() if t.Name not in snap]
