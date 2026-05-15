"""Snapshot / restore for character bone rotations.

Because MotionBuilder 2026's Python binding no longer exposes
``FBCharacter.SetROffset`` / ``SetTOffset``, T-Pose alignment in this tool
is implemented by directly rotating the character's skeleton bones
(``FBModel.Rotation``). To keep the workflow reversible we snapshot the
local rotation / translation of every relevant bone before alignment, and
provide :func:`restore` to put them back.

Snapshots are keyed by the *string* name of the ``FBBodyNodeId`` (e.g.
``"kFBLeftShoulderNodeId"``) so they survive across MotionBuilder
versions where enum integer values might shift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .canonical_pose import resolve_node_id
from .chain_groups import all_canonical_node_names


RVec = Tuple[float, float, float]
TVec = Tuple[float, float, float]


@dataclass
class OffsetSnapshot:
    """Per-body-node bone rotation / translation captured from a character.

    The name keeps the historical "Offset" terminology so existing UI /
    preset code doesn't need to change, but the values are now actual
    bone-local Euler rotations and translations rather than character
    offsets.
    """

    character_name: str
    rotations: Dict[str, RVec] = field(default_factory=dict)
    translations: Dict[str, TVec] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


def _node_iter(extra_names: Optional[Iterable[str]] = None) -> Iterable[Tuple[str, object]]:
    """Yield ``(name, FBBodyNodeId)`` for every node we care about, lazily."""
    seen = set()
    for name in all_canonical_node_names():
        if name in seen:
            continue
        seen.add(name)
        node_id = resolve_node_id(name)
        if node_id is not None:
            yield name, node_id
    if extra_names:
        for name in extra_names:
            if name in seen:
                continue
            seen.add(name)
            node_id = resolve_node_id(name)
            if node_id is not None:
                yield name, node_id


def _read_vec3_property(prop) -> Optional[Tuple[float, float, float]]:
    """Read an animatable Vector3 property as a tuple, robust to API differences."""
    if prop is None:
        return None
    try:
        v = prop.Data
        return (float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        try:
            return (float(prop[0]), float(prop[1]), float(prop[2]))
        except Exception:
            return None


def capture(character, extra_node_names: Optional[Iterable[str]] = None) -> OffsetSnapshot:
    """Snapshot every relevant bone's local rotation and translation."""
    snap = OffsetSnapshot(character_name=character.LongName)
    for name, node_id in _node_iter(extra_node_names):
        bone = character.GetModel(node_id)
        if bone is None:
            continue
        r = _read_vec3_property(getattr(bone, "Rotation", None))
        if r is not None:
            snap.rotations[name] = r
        t = _read_vec3_property(getattr(bone, "Translation", None))
        if t is not None:
            snap.translations[name] = t
    return snap


def _set_vec3_property(prop, value: Tuple[float, float, float]) -> bool:
    """Write an animatable Vector3 property, robust to API differences."""
    if prop is None:
        return False
    from ._compat import FBRVector  # FBVector3d on 2026
    try:
        prop.Data = FBRVector(value[0], value[1], value[2])
        return True
    except Exception:
        try:
            prop[0] = value[0]
            prop[1] = value[1]
            prop[2] = value[2]
            return True
        except Exception:
            return False


def restore(character, snapshot: OffsetSnapshot, recharacterize: bool = True) -> None:
    """Reapply ``snapshot``'s rotations / translations to ``character``.

    If ``recharacterize`` is True (the default) the character is also taken
    through a SetCharacterizeOff/On cycle so its stance pose returns to
    the captured pose - otherwise the stance would still reflect whatever
    pose was active when the alignment was last applied.
    """
    for name, rot in snapshot.rotations.items():
        node_id = resolve_node_id(name)
        if node_id is None:
            continue
        bone = character.GetModel(node_id)
        if bone is None:
            continue
        _set_vec3_property(getattr(bone, "Rotation", None), rot)

    for name, trn in snapshot.translations.items():
        node_id = resolve_node_id(name)
        if node_id is None:
            continue
        bone = character.GetModel(node_id)
        if bone is None:
            continue
        _set_vec3_property(getattr(bone, "Translation", None), trn)

    try:
        from pyfbsdk import FBSystem  # type: ignore
        FBSystem().Scene.Evaluate()
    except Exception:
        pass

    if recharacterize:
        try:
            character.SetCharacterizeOff(False)
            character.SetCharacterizeOn(False)
        except Exception:
            pass
        try:
            from pyfbsdk import FBSystem  # type: ignore
            FBSystem().Scene.Evaluate()
        except Exception:
            pass


def reset_all_offsets(character) -> None:
    """Snapshot the current pose and treat it as the resting baseline.

    The legacy SetROffset workflow had a meaningful "zero out all offsets"
    step. With the bone-rotation approach there is no equivalent: the bone
    rotations are simply the current pose. This function is kept so the
    older API surface still resolves, but it is now a no-op aside from
    pushing a Goto-stance to make sure the character starts from its
    characterized stance.
    """
    try:
        character.GoToStancePose(False, True)
    except TypeError:
        try:
            character.GoToStancePose(False)
        except Exception:
            pass
    except Exception:
        pass
    try:
        from pyfbsdk import FBSystem  # type: ignore
        FBSystem().Scene.Evaluate()
    except Exception:
        pass


def snapshot_to_dict(snap: OffsetSnapshot) -> dict:
    return {
        "character_name": snap.character_name,
        "rotations": {k: list(v) for k, v in snap.rotations.items()},
        "translations": {k: list(v) for k, v in snap.translations.items()},
        "timestamp": snap.timestamp,
    }


def snapshot_from_dict(data: dict) -> OffsetSnapshot:
    return OffsetSnapshot(
        character_name=str(data.get("character_name", "")),
        rotations={k: tuple(v) for k, v in (data.get("rotations") or {}).items()},
        translations={k: tuple(v) for k, v in (data.get("translations") or {}).items()},
        timestamp=str(data.get("timestamp", datetime.now().isoformat())),
    )


def diff_summary(before: OffsetSnapshot, after: OffsetSnapshot) -> List[Tuple[str, RVec]]:
    """Return ``(name, (drx, dry, drz))`` for every bone whose rotation changed."""
    out: List[Tuple[str, RVec]] = []
    keys = set(before.rotations) | set(after.rotations)
    for name in sorted(keys):
        b = before.rotations.get(name, (0.0, 0.0, 0.0))
        a = after.rotations.get(name, (0.0, 0.0, 0.0))
        diff = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
        if any(abs(c) > 1e-4 for c in diff):
            out.append((name, diff))
    return out
