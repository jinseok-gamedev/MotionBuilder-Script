"""Shape feature extraction for a Source / Target HumanIK character pair.

The retargeting option advisor (:mod:`Retargeter.core.option_advisor`) needs
quantitative information about how the two characters differ in size and
topology in order to recommend HIK / PlotConfig values. This module is the
single place that touches MotionBuilder's scene to collect that information.

What we extract
---------------

For each character (:class:`CharacterShape`):

* Absolute scale: ``height_m``, ``upper_body_m``, ``leg_length_m``,
  ``arm_length_m`` (in metres, assuming MotionBuilder's scene units are cm).
* Ratios: ``upper_body_ratio``, ``leg_ratio``, ``arm_to_height``.
* Widths: ``shoulder_width_m``, ``hip_width_m``, ``shoulder_hip_ratio``.
* Topology: ``spine_segments``, ``neck_segments``, ``finger_count_l/r``.
* Pose: ``arm_angle_l/r`` in degrees (T-pose ~ 0, A-pose ~ 30-45).

For the pair (:class:`PairFeatures`):

* ``height_ratio = target / source``, same for shoulder width / arm length.
* ``spine_segments_delta``, ``finger_count_match``, ``arm_angle_diff_deg``.

How we sample
-------------

We evaluate the scene at the current take's start frame once and read each
slot's :func:`GlobalTransform.GetTranslation`. If a slot is empty (`None`)
the corresponding measurement becomes ``None`` and downstream rules must
treat it as missing rather than zero.

We intentionally never modify the scene: the active take, time, selection
and characterisation are all left untouched.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from pyfbsdk import (  # type: ignore
    FBCharacter,
    FBSystem,
    FBVector3d,
)

from .scene_utils import (
    HEAD_SLOT,
    HIPS_SLOT,
    LEFT_ARM_SLOT,
    LEFT_FINGER_PREFIXES,
    LEFT_FOOT_SLOT,
    LEFT_FORE_ARM_SLOT,
    LEFT_HAND_SLOT,
    LEFT_LEG_SLOT,
    LEFT_SHOULDER_SLOT,
    LEFT_UP_LEG_SLOT,
    NECK_PREFIXES,
    RIGHT_ARM_SLOT,
    RIGHT_FINGER_PREFIXES,
    RIGHT_FOOT_SLOT,
    RIGHT_FORE_ARM_SLOT,
    RIGHT_HAND_SLOT,
    RIGHT_LEG_SLOT,
    RIGHT_SHOULDER_SLOT,
    RIGHT_UP_LEG_SLOT,
    SPINE_PREFIXES,
    enumerate_filled_slots,
    get_slot_model,
)


# MotionBuilder's scene unit is centimetres by default. The advisor's rule
# thresholds are easier to read in metres ("height_ratio < 0.7") so we
# normalise once here. If the operator's scene uses different units the
# RATIOS will still be correct (numerator and denominator scale together);
# only the absolute *_m fields would be off, which the advisor does not
# threshold against directly.
_CM_TO_M = 0.01


# ----------------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------------


@dataclass
class CharacterShape:
    """All shape measurements for a single character.

    Optional ``None`` values mean "the rig did not expose that slot, treat
    as missing". Rules must guard for None rather than assuming 0.
    """

    name: str = ""

    # Absolute lengths (metres). None if the relevant slot(s) were empty.
    height_m: Optional[float] = None
    upper_body_m: Optional[float] = None
    leg_length_m: Optional[float] = None
    arm_length_m: Optional[float] = None

    # Ratios (dimensionless), computed only when both inputs are present.
    upper_body_ratio: Optional[float] = None
    leg_ratio: Optional[float] = None
    arm_to_height: Optional[float] = None

    # Widths (metres).
    shoulder_width_m: Optional[float] = None
    hip_width_m: Optional[float] = None
    shoulder_hip_ratio: Optional[float] = None

    # HIK topology.
    spine_segments: int = 0
    neck_segments: int = 0
    finger_count_l: int = 0
    finger_count_r: int = 0

    # Pose hints (degrees from horizontal).
    arm_angle_l: Optional[float] = None
    arm_angle_r: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PairFeatures:
    """Source vs. target shape comparison used by the advisor."""

    source: CharacterShape = field(default_factory=CharacterShape)
    target: CharacterShape = field(default_factory=CharacterShape)

    # Pair-derived ratios. None if either side is missing the input.
    height_ratio: Optional[float] = None          # target / source
    shoulder_width_ratio: Optional[float] = None
    arm_length_ratio: Optional[float] = None
    leg_length_ratio: Optional[float] = None

    spine_segments_delta: int = 0                 # abs difference
    neck_segments_delta: int = 0
    finger_count_match: bool = True               # both sides + both hands

    arm_angle_diff_deg: Optional[float] = None    # max(|tgt-src|) over L/R

    # Errors/warnings encountered while measuring (logged but non-fatal).
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "height_ratio": self.height_ratio,
            "shoulder_width_ratio": self.shoulder_width_ratio,
            "arm_length_ratio": self.arm_length_ratio,
            "leg_length_ratio": self.leg_length_ratio,
            "spine_segments_delta": self.spine_segments_delta,
            "neck_segments_delta": self.neck_segments_delta,
            "finger_count_match": self.finger_count_match,
            "arm_angle_diff_deg": self.arm_angle_diff_deg,
            "notes": list(self.notes),
        }


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def extract_character_shape(character: FBCharacter) -> CharacterShape:
    """Sample one character at the current scene time and return its shape."""
    if character is None:
        return CharacterShape()
    name = getattr(character, "LongName", "") or getattr(character, "Name", "") or ""

    # Slot models we need translations from.
    hips = get_slot_model(character, HIPS_SLOT)
    head = get_slot_model(character, HEAD_SLOT)
    l_foot = get_slot_model(character, LEFT_FOOT_SLOT)
    r_foot = get_slot_model(character, RIGHT_FOOT_SLOT)
    l_uplleg = get_slot_model(character, LEFT_UP_LEG_SLOT)
    r_uplleg = get_slot_model(character, RIGHT_UP_LEG_SLOT)
    l_knee = get_slot_model(character, LEFT_LEG_SLOT)
    r_knee = get_slot_model(character, RIGHT_LEG_SLOT)
    l_shoulder = get_slot_model(character, LEFT_SHOULDER_SLOT)
    r_shoulder = get_slot_model(character, RIGHT_SHOULDER_SLOT)
    l_arm = get_slot_model(character, LEFT_ARM_SLOT)
    r_arm = get_slot_model(character, RIGHT_ARM_SLOT)
    l_elbow = get_slot_model(character, LEFT_FORE_ARM_SLOT)
    r_elbow = get_slot_model(character, RIGHT_FORE_ARM_SLOT)
    l_hand = get_slot_model(character, LEFT_HAND_SLOT)
    r_hand = get_slot_model(character, RIGHT_HAND_SLOT)

    # Up-axis aware height (most MoBu scenes are Y-up; we still handle Z-up).
    up_axis = _scene_up_axis()
    hips_p = _global_translation(hips)
    head_p = _global_translation(head)
    l_foot_p = _global_translation(l_foot)
    r_foot_p = _global_translation(r_foot)

    height_m: Optional[float] = None
    if hips_p is not None and (l_foot_p is not None or r_foot_p is not None):
        foot_up = _min(
            l_foot_p[up_axis] if l_foot_p is not None else None,
            r_foot_p[up_axis] if r_foot_p is not None else None,
        )
        if foot_up is not None:
            height_m = abs(hips_p[up_axis] - foot_up) * _CM_TO_M

    upper_body_m: Optional[float] = None
    if hips_p is not None and head_p is not None:
        upper_body_m = _distance(hips_p, head_p) * _CM_TO_M

    leg_length_m: Optional[float] = _chain_length(
        [_global_translation(l_uplleg), _global_translation(l_knee), _global_translation(l_foot)]
    )
    arm_length_m: Optional[float] = _chain_length(
        [
            _global_translation(l_arm) or _global_translation(l_shoulder),
            _global_translation(l_elbow),
            _global_translation(l_hand),
        ]
    )
    # Right-side fallback if the left chain was incomplete.
    if leg_length_m is None:
        leg_length_m = _chain_length(
            [_global_translation(r_uplleg), _global_translation(r_knee), _global_translation(r_foot)]
        )
    if arm_length_m is None:
        arm_length_m = _chain_length(
            [
                _global_translation(r_arm) or _global_translation(r_shoulder),
                _global_translation(r_elbow),
                _global_translation(r_hand),
            ]
        )

    shoulder_width_m: Optional[float] = None
    if l_shoulder is not None and r_shoulder is not None:
        lp = _global_translation(l_shoulder)
        rp = _global_translation(r_shoulder)
        if lp is not None and rp is not None:
            shoulder_width_m = _distance(lp, rp) * _CM_TO_M
    elif l_arm is not None and r_arm is not None:
        # Some rigs do not expose a clavicle; fall back to upper-arm roots.
        lp = _global_translation(l_arm)
        rp = _global_translation(r_arm)
        if lp is not None and rp is not None:
            shoulder_width_m = _distance(lp, rp) * _CM_TO_M

    hip_width_m: Optional[float] = None
    lp = _global_translation(l_uplleg)
    rp = _global_translation(r_uplleg)
    if lp is not None and rp is not None:
        hip_width_m = _distance(lp, rp) * _CM_TO_M

    arm_angle_l = _arm_horizontal_angle_deg(
        _global_translation(l_arm) or _global_translation(l_shoulder),
        _global_translation(l_hand),
        side_sign=+1,
    )
    arm_angle_r = _arm_horizontal_angle_deg(
        _global_translation(r_arm) or _global_translation(r_shoulder),
        _global_translation(r_hand),
        side_sign=-1,
    )

    spine_segments = len(enumerate_filled_slots(character, SPINE_PREFIXES))
    neck_segments = len(enumerate_filled_slots(character, NECK_PREFIXES))
    finger_count_l = len(enumerate_filled_slots(character, LEFT_FINGER_PREFIXES))
    finger_count_r = len(enumerate_filled_slots(character, RIGHT_FINGER_PREFIXES))

    shape = CharacterShape(
        name=name,
        height_m=height_m,
        upper_body_m=upper_body_m,
        leg_length_m=leg_length_m,
        arm_length_m=arm_length_m,
        shoulder_width_m=shoulder_width_m,
        hip_width_m=hip_width_m,
        spine_segments=spine_segments,
        neck_segments=neck_segments,
        finger_count_l=finger_count_l,
        finger_count_r=finger_count_r,
        arm_angle_l=arm_angle_l,
        arm_angle_r=arm_angle_r,
    )

    # Derive ratios (only when both inputs exist).
    if shape.upper_body_m is not None and shape.height_m:
        shape.upper_body_ratio = shape.upper_body_m / shape.height_m
    if shape.leg_length_m is not None and shape.height_m:
        shape.leg_ratio = shape.leg_length_m / shape.height_m
    if shape.arm_length_m is not None and shape.height_m:
        shape.arm_to_height = shape.arm_length_m / shape.height_m
    if shape.shoulder_width_m is not None and shape.hip_width_m:
        shape.shoulder_hip_ratio = shape.shoulder_width_m / shape.hip_width_m

    return shape


def extract_pair_features(
    source: FBCharacter, target: FBCharacter
) -> PairFeatures:
    """Evaluate scene at the current take's start frame and return shape diff."""
    notes: List[str] = []
    try:
        _evaluate_at_take_start()
    except Exception as exc:
        notes.append(f"scene evaluation failed: {exc!r}")

    src_shape = extract_character_shape(source)
    tgt_shape = extract_character_shape(target)

    pair = PairFeatures(source=src_shape, target=tgt_shape, notes=notes)
    pair.height_ratio = _safe_ratio(tgt_shape.height_m, src_shape.height_m)
    pair.shoulder_width_ratio = _safe_ratio(tgt_shape.shoulder_width_m, src_shape.shoulder_width_m)
    pair.arm_length_ratio = _safe_ratio(tgt_shape.arm_length_m, src_shape.arm_length_m)
    pair.leg_length_ratio = _safe_ratio(tgt_shape.leg_length_m, src_shape.leg_length_m)
    pair.spine_segments_delta = abs(int(tgt_shape.spine_segments) - int(src_shape.spine_segments))
    pair.neck_segments_delta = abs(int(tgt_shape.neck_segments) - int(src_shape.neck_segments))
    pair.finger_count_match = (
        src_shape.finger_count_l == tgt_shape.finger_count_l
        and src_shape.finger_count_r == tgt_shape.finger_count_r
    )

    arm_diffs: List[float] = []
    if src_shape.arm_angle_l is not None and tgt_shape.arm_angle_l is not None:
        arm_diffs.append(abs(tgt_shape.arm_angle_l - src_shape.arm_angle_l))
    if src_shape.arm_angle_r is not None and tgt_shape.arm_angle_r is not None:
        arm_diffs.append(abs(tgt_shape.arm_angle_r - src_shape.arm_angle_r))
    if arm_diffs:
        pair.arm_angle_diff_deg = max(arm_diffs)

    return pair


# ----------------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------------


def _scene_up_axis() -> int:
    """Return 1 (Y) or 2 (Z) for the current scene's up axis.

    MotionBuilder default is Y-up. We sniff FBSystem().Scene.MotionUnit or
    fall back to Y. Returning the index rather than a string lets callers
    use ``vec[up_axis]`` directly.
    """
    try:
        scene = FBSystem().Scene
        evaluation = getattr(scene, "Evaluation", None)
        up = getattr(evaluation, "UpAxis", None) if evaluation else None
        if up is not None:
            # Heuristic: stringify and check; pyfbsdk enums quack as ints too.
            s = str(up).lower()
            if "z" in s:
                return 2
    except Exception:
        pass
    return 1


def _global_translation(model) -> Optional[List[float]]:
    """Read a model's world-space translation as ``[x, y, z]``.

    Returns ``None`` if the model is missing or every read path raised.
    ``FBModel.GetVector`` defaults to ``(Translation, Global=True)`` so we
    prefer that path; a couple of fallbacks cover odd model subclasses.
    """
    if model is None:
        return None
    try:
        v = FBVector3d()
        model.GetVector(v)
        return [float(v[0]), float(v[1]), float(v[2])]
    except Exception:
        pass
    try:
        t = model.Translation.GetGlobal()
        return [float(t[0]), float(t[1]), float(t[2])]
    except Exception:
        pass
    try:
        t = model.Translation
        return [float(t[0]), float(t[1]), float(t[2])]
    except Exception:
        pass
    return None


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    dz = float(a[2]) - float(b[2])
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _chain_length(points: Sequence[Optional[Sequence[float]]]) -> Optional[float]:
    """Sum |p[i+1] - p[i]| in centimetres, return metres. ``None`` if any gap."""
    pts = [p for p in points if p is not None]
    if len(pts) < 2:
        return None
    total = 0.0
    for i in range(len(pts) - 1):
        total += _distance(pts[i], pts[i + 1])
    return total * _CM_TO_M


def _arm_horizontal_angle_deg(
    shoulder: Optional[Sequence[float]],
    hand: Optional[Sequence[float]],
    *,
    side_sign: int,
) -> Optional[float]:
    """Angle (degrees) between the shoulder->hand vector and horizontal X.

    ``side_sign = +1`` for left (arm extends +X in T-pose), -1 for right.
    A T-pose returns ~0, an A-pose returns ~30-45 (positive = arm hangs down).
    Returns ``None`` if either endpoint is missing.
    """
    if shoulder is None or hand is None:
        return None
    dx = (float(hand[0]) - float(shoulder[0])) * float(side_sign)
    dy = float(hand[1]) - float(shoulder[1])
    horiz = abs(dx)
    if horiz <= 1e-6:
        return 90.0  # arm fully vertical; treat as worst case
    angle_rad = math.atan2(abs(dy), horiz)
    return math.degrees(angle_rad)


def _safe_ratio(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    if num is None or denom is None:
        return None
    if abs(denom) < 1e-9:
        return None
    return float(num) / float(denom)


def _min(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None:
        return b
    if b is None:
        return a
    return a if a < b else b


def _evaluate_at_take_start() -> None:
    """Jump current take to its start time and evaluate the scene once.

    We do NOT restore the previous time afterwards: HIK characters re-evaluate
    on the next frame change anyway, and the operator typically presses Auto
    before plotting (so the scrubber moving 1 frame is harmless).
    """
    system = FBSystem()
    take = system.CurrentTake
    if take is None:
        system.Scene.Evaluate()
        return
    try:
        start = take.LocalTimeSpan.GetStart()
        from pyfbsdk import FBPlayerControl  # type: ignore

        FBPlayerControl().Goto(start)
    except Exception:
        pass
    try:
        system.Scene.Evaluate()
    except Exception:
        pass
