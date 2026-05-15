"""Validation helpers: scene up-axis checks, offset grading, proportions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from . import math_utils as m


class Severity(Enum):
    OK = "ok"
    WARN = "warn"
    HIGH = "high"


@dataclass(frozen=True)
class OffsetGrade:
    severity: Severity
    angle_deg: float
    note: str = ""


def categorize_offset(
    rx_deg: float,
    ry_deg: float,
    rz_deg: float,
    warn_threshold_deg: float = 10.0,
    high_threshold_deg: float = 30.0,
) -> OffsetGrade:
    """Grade an Euler offset by its quaternion-magnitude angle.

    Using the magnitude rather than the sum of components avoids penalizing
    rotations that happen to use multiple Euler axes for a small effective
    rotation (e.g. a 5 degree rotation expressed as ``(3, 4, 0)``).
    """
    import math

    qx = m.quat_from_axis_angle((1.0, 0.0, 0.0), math.radians(rx_deg))
    qy = m.quat_from_axis_angle((0.0, 1.0, 0.0), math.radians(ry_deg))
    qz = m.quat_from_axis_angle((0.0, 0.0, 1.0), math.radians(rz_deg))
    q = m.quat_mul(m.quat_mul(qx, qy), qz)
    angle_deg = m.degrees_magnitude(q)

    if angle_deg <= warn_threshold_deg:
        return OffsetGrade(Severity.OK, angle_deg)
    if angle_deg <= high_threshold_deg:
        return OffsetGrade(
            Severity.WARN,
            angle_deg,
            "Notable correction (>10 deg). Verify the bone visually.",
        )
    return OffsetGrade(
        Severity.HIGH,
        angle_deg,
        "Large correction (>30 deg). Possible mis-mapped bone or unusual stance.",
    )


def is_y_up_scene() -> bool:
    """True if the active scene is configured Y-up.

    MotionBuilder's HumanIK solver assumes Y-up; running with Z-up has been
    documented to break retargeting in unexpected ways (e.g. wrist-only
    failures). Rather than silently failing we surface the problem.
    """
    try:
        from pyfbsdk import FBSystem  # type: ignore
        scene = FBSystem().Scene
    except Exception:
        return True

    candidates = ("UpAxis", "GlobalUpAxis")
    for prop_name in candidates:
        prop = scene.PropertyList.Find(prop_name) if hasattr(scene, "PropertyList") else None
        if prop is not None:
            try:
                value = prop.Data
            except Exception:
                continue
            if isinstance(value, str):
                return value.upper().startswith("Y")

    settings = getattr(scene, "GlobalLightSettings", None)
    if settings is not None:
        prop = settings.PropertyList.Find("UpAxis") if hasattr(settings, "PropertyList") else None
        if prop is not None:
            try:
                value = prop.Data
                if isinstance(value, str):
                    return value.upper().startswith("Y")
            except Exception:
                pass

    return True


def assert_y_up_scene() -> None:
    """Raise ``RuntimeError`` if the current scene is not Y-up."""
    if not is_y_up_scene():
        raise RuntimeError(
            "TPoseAligner requires a Y-up scene. The active MotionBuilder "
            "scene appears to use a different up axis. Set up axis to Y in "
            "File > Scene Settings before aligning."
        )


@dataclass
class ProportionReport:
    """Side-by-side measurements of two characters' major bone lengths."""

    src_arm: float = 0.0
    tgt_arm: float = 0.0
    src_leg: float = 0.0
    tgt_leg: float = 0.0
    src_torso: float = 0.0
    tgt_torso: float = 0.0
    src_total_height: float = 0.0
    tgt_total_height: float = 0.0
    notes: List[str] = field(default_factory=list)

    @property
    def height_ratio(self) -> float:
        if self.src_total_height < 1e-6:
            return 1.0
        return self.tgt_total_height / self.src_total_height

    @property
    def arm_ratio(self) -> float:
        if self.src_arm < 1e-6:
            return 1.0
        return self.tgt_arm / self.src_arm

    @property
    def leg_ratio(self) -> float:
        if self.src_leg < 1e-6:
            return 1.0
        return self.tgt_leg / self.src_leg

    def recommend_match_source(self) -> bool:
        return abs(self.height_ratio - 1.0) > 0.15 or abs(self.leg_ratio - 1.0) > 0.15


def _bone_distance(character, parent_name: str, child_name: str) -> float:
    from pyfbsdk import FBMatrix, FBModelTransformationType  # type: ignore
    from .canonical_pose import resolve_node_id

    parent_id = resolve_node_id(parent_name)
    child_id = resolve_node_id(child_name)
    if parent_id is None or child_id is None:
        return 0.0
    parent_model = character.GetModel(parent_id)
    child_model = character.GetModel(child_id)
    if parent_model is None or child_model is None:
        return 0.0

    pm = FBMatrix()
    cm = FBMatrix()
    parent_model.GetMatrix(pm, FBModelTransformationType.kModelTransformation, True)
    child_model.GetMatrix(cm, FBModelTransformationType.kModelTransformation, True)
    p = m.matrix_translation(m.fb_matrix_to_tuple(pm))
    c = m.matrix_translation(m.fb_matrix_to_tuple(cm))
    return m.vec_length(m.vec_sub(c, p))


def compare_proportions(source, target) -> ProportionReport:
    """Measure the major bone-chain lengths of two characters.

    Used to surface scale mismatches that the user should compensate for
    via Match Source / IK Pull settings on the target character.
    """
    rep = ProportionReport()

    rep.src_arm = (
        _bone_distance(source, "kFBLeftShoulderNodeId", "kFBLeftElbowNodeId")
        + _bone_distance(source, "kFBLeftElbowNodeId", "kFBLeftWristNodeId")
    )
    rep.tgt_arm = (
        _bone_distance(target, "kFBLeftShoulderNodeId", "kFBLeftElbowNodeId")
        + _bone_distance(target, "kFBLeftElbowNodeId", "kFBLeftWristNodeId")
    )

    rep.src_leg = (
        _bone_distance(source, "kFBLeftHipNodeId", "kFBLeftKneeNodeId")
        + _bone_distance(source, "kFBLeftKneeNodeId", "kFBLeftAnkleNodeId")
    )
    rep.tgt_leg = (
        _bone_distance(target, "kFBLeftHipNodeId", "kFBLeftKneeNodeId")
        + _bone_distance(target, "kFBLeftKneeNodeId", "kFBLeftAnkleNodeId")
    )

    rep.src_torso = _bone_distance(source, "kFBHipsNodeId", "kFBHeadNodeId")
    rep.tgt_torso = _bone_distance(target, "kFBHipsNodeId", "kFBHeadNodeId")

    rep.src_total_height = rep.src_leg + rep.src_torso
    rep.tgt_total_height = rep.tgt_leg + rep.tgt_torso

    if abs(rep.height_ratio - 1.0) > 0.15:
        rep.notes.append(
            "Height ratio {:.2f}x. Consider enabling Match Source on target."
            .format(rep.height_ratio)
        )
    if abs(rep.arm_ratio - 1.0) > 0.20:
        rep.notes.append(
            "Arm length ratio {:.2f}x. Hand IK contacts will need adjustment."
            .format(rep.arm_ratio)
        )
    if abs(rep.leg_ratio - 1.0) > 0.20:
        rep.notes.append(
            "Leg length ratio {:.2f}x. Stride length will not match by default."
            .format(rep.leg_ratio)
        )
    return rep


def grade_offsets_dict(
    offsets: Dict,
    warn_threshold_deg: float = 10.0,
    high_threshold_deg: float = 30.0,
) -> Dict[object, OffsetGrade]:
    """Convenience: grade every entry of an ``{node_id: (rx, ry, rz)}`` dict.

    Accepts either an ``FBRVector`` or a plain ``(rx, ry, rz)`` tuple as the
    value type, so the same routine grades both freshly-applied offsets and
    deserialized presets.
    """
    out: Dict[object, OffsetGrade] = {}
    for node_id, value in offsets.items():
        try:
            rx = float(value[0])
            ry = float(value[1])
            rz = float(value[2])
        except Exception:
            continue
        out[node_id] = categorize_offset(
            rx, ry, rz, warn_threshold_deg, high_threshold_deg,
        )
    return out


def severity_color_hex(severity: Severity) -> str:
    """Hex color suitable for UI/log highlighting."""
    if severity == Severity.OK:
        return "#3da35d"
    if severity == Severity.WARN:
        return "#d6a227"
    return "#c84630"
