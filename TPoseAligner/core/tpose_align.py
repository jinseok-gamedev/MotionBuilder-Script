"""Core T-Pose alignment routines for MotionBuilder HumanIK characters.

This module is the heart of TPoseAligner. The high-level entry points are:

- :func:`align_character_to_canonical_tpose` - align a single character
- :func:`align_pair` - convenience that aligns both source and target,
  ensuring they end up in identical canonical T-Poses
- :func:`connect_for_retarget` - wire a target character's input to a
  source character so the user can preview / plot the retarget

Behind the scenes the aligner:

1. Validates the scene (Y-up).
2. Captures a snapshot of the existing offsets (for one-click restore).
3. Optionally clears all existing offsets so we start from a clean baseline.
4. Calls ``GoToStancePose`` so the character's bones reflect the current
   characterization stance.
5. For each chain (parent-first), computes a non-destructive ``ROffset``
   that takes each bone from its current world orientation to the canonical
   orientation declared in :mod:`canonical_pose`.
6. Applies a few targeted post-passes for hands, feet, and twist bones.
7. Grades every offset and returns warnings for outliers.

The implementation is conservative: any time it cannot resolve a bone, a
child reference, or a body-node enum value, it skips the entry rather than
crash. This keeps the tool usable on partial / non-standard rigs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from . import math_utils as m
from .canonical_pose import (
    CanonicalDir,
    canonical_dir_for,
    is_roll_bone,
    parent_of_roll,
    name_for_node_id,
    resolve_node_id,
)
from .chain_groups import (
    CHAIN_PROCESS_ORDER,
    CHAIN_TO_NODE_NAMES,
    ChainId,
    all_canonical_node_names,
)
from .snapshot import OffsetSnapshot, capture, reset_all_offsets
from .validation import (
    OffsetGrade,
    Severity,
    assert_y_up_scene,
    categorize_offset,
)


@dataclass
class AlignOptions:
    """Configuration for one alignment pass.

    Attributes:
        chains: Subset of chains to process. ``None`` means "all chains".
        clear_existing: Zero existing offsets before computing new ones.
        preserve_micro_bend: Allow ~1 deg bend in elbows/knees to prevent
            IK over-extension after alignment.
        handle_wrist_flip: Pick the shorter of two equivalent rotations,
            avoiding the well-known 180-degree wrist flip.
        palms_down: Run a post-pass that orients the hand so the palm
            faces ``-Y``.
        feet_flat_forward: Run a post-pass that orients the foot so the
            sole faces ``-Y`` and the toe faces ``+Z``.
        include_fingers: Also align finger bones. Off by default because
            most rigs already have the fingers in a reasonable resting
            pose and re-aligning them can produce odd splays.
        warn_threshold_deg / high_threshold_deg: Bands for offset grading.
        require_y_up: Refuse to run on Z-up scenes.
        push_undo: Pass ``pPushUndo=True`` to ``GoToStancePose``.
    """

    chains: Optional[Set[ChainId]] = None
    clear_existing: bool = True
    preserve_micro_bend: bool = True
    micro_bend_deg: float = 1.0
    handle_wrist_flip: bool = True
    palms_down: bool = True
    feet_flat_forward: bool = True
    include_fingers: bool = False
    warn_threshold_deg: float = 10.0
    high_threshold_deg: float = 30.0
    require_y_up: bool = True
    push_undo: bool = True
    update_stance_after: bool = True

    def to_dict(self) -> Dict[str, object]:
        return {
            "chains": sorted(c.name for c in self.chains) if self.chains else None,
            "clear_existing": self.clear_existing,
            "preserve_micro_bend": self.preserve_micro_bend,
            "micro_bend_deg": self.micro_bend_deg,
            "handle_wrist_flip": self.handle_wrist_flip,
            "palms_down": self.palms_down,
            "feet_flat_forward": self.feet_flat_forward,
            "include_fingers": self.include_fingers,
            "warn_threshold_deg": self.warn_threshold_deg,
            "high_threshold_deg": self.high_threshold_deg,
            "require_y_up": self.require_y_up,
            "push_undo": self.push_undo,
            "update_stance_after": self.update_stance_after,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "AlignOptions":
        chains_raw = data.get("chains")
        chains: Optional[Set[ChainId]] = None
        if chains_raw:
            chains = set()
            for name in chains_raw:  # type: ignore[union-attr]
                try:
                    chains.add(ChainId[str(name)])
                except KeyError:
                    pass
        return cls(
            chains=chains,
            clear_existing=bool(data.get("clear_existing", True)),
            preserve_micro_bend=bool(data.get("preserve_micro_bend", True)),
            micro_bend_deg=float(data.get("micro_bend_deg", 1.0)),
            handle_wrist_flip=bool(data.get("handle_wrist_flip", True)),
            palms_down=bool(data.get("palms_down", True)),
            feet_flat_forward=bool(data.get("feet_flat_forward", True)),
            include_fingers=bool(data.get("include_fingers", False)),
            warn_threshold_deg=float(data.get("warn_threshold_deg", 10.0)),
            high_threshold_deg=float(data.get("high_threshold_deg", 30.0)),
            require_y_up=bool(data.get("require_y_up", True)),
            push_undo=bool(data.get("push_undo", True)),
            update_stance_after=bool(data.get("update_stance_after", True)),
        )


@dataclass
class AlignResult:
    """Outcome of a single character alignment.

    ``offsets`` is keyed by body-node string name (e.g.
    ``"kFBLeftShoulderNodeId"``) and stores the final ``(rx, ry, rz)`` in
    degrees that was applied via ``SetROffset``. ``warnings`` lists tuples
    of ``(name, message)`` so the UI can highlight problematic bones.
    """

    character_name: str
    offsets: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)
    grades: Dict[str, OffsetGrade] = field(default_factory=dict)
    warnings: List[Tuple[str, str]] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    pre_snapshot: Optional[OffsetSnapshot] = None
    post_snapshot: Optional[OffsetSnapshot] = None

    @property
    def num_high_warnings(self) -> int:
        return sum(1 for g in self.grades.values() if g.severity == Severity.HIGH)


_FINGER_CHAIN_IDS: FrozenSet[ChainId] = frozenset({ChainId.LEFT_HAND, ChainId.RIGHT_HAND})


def _selected_chains(options: AlignOptions) -> Tuple[ChainId, ...]:
    """Return the ordered chains to process given the options."""
    selected = set(CHAIN_PROCESS_ORDER) if options.chains is None else set(options.chains)
    if not options.include_fingers:
        selected -= _FINGER_CHAIN_IDS
    return tuple(c for c in CHAIN_PROCESS_ORDER if c in selected)


def _get_world_matrix(model) -> Optional[m.Mat4]:
    from pyfbsdk import FBMatrix, FBModelTransformationType  # type: ignore
    if model is None:
        return None
    fb = FBMatrix()
    model.GetMatrix(fb, FBModelTransformationType.kModelTransformation, True)
    return m.fb_matrix_to_tuple(fb)


def _world_position(model) -> Optional[m.Vec3]:
    mat = _get_world_matrix(model)
    if mat is None:
        return None
    return m.matrix_translation(mat)


def _first_skeleton_child(model):
    """Return the first child that is a skeleton joint, if any.

    HumanIK rigs sometimes hang non-skeleton helpers (e.g. floor markers)
    off of an effector, so we skip anything that does not look like a bone.
    """
    if model is None:
        return None
    try:
        from pyfbsdk import FBModelSkeleton  # type: ignore
    except Exception:
        FBModelSkeleton = None  # type: ignore

    for child in model.Children:
        if FBModelSkeleton is not None and not isinstance(child, FBModelSkeleton):
            continue
        return child
    if model.Children:
        return model.Children[0]
    return None


def _measure_current_orientation(
    parent_world_mat: m.Mat4,
    child_world_pos: m.Vec3,
) -> Tuple[m.Vec3, m.Quat]:
    """Return ``(current_primary_world_dir, current_world_quaternion)``."""
    parent_pos = m.matrix_translation(parent_world_mat)
    primary = m.vec_normalize(m.vec_sub(child_world_pos, parent_pos))
    q_world = m.quat_from_matrix(parent_world_mat)
    return primary, q_world


def _build_target_quaternion(
    current_primary: m.Vec3,
    current_quat: m.Quat,
    canonical: CanonicalDir,
) -> m.Quat:
    """Build the world-space quaternion the bone should have after alignment.

    We start from the current orientation, then layer on a minimal rotation
    that takes the current primary axis onto the canonical primary axis,
    then a twist around the canonical primary axis that makes the bone's
    "up" reference point at the canonical up.
    """
    q_align_primary = m.quat_from_two_vectors(current_primary, canonical.primary)
    q_partial = m.quat_normalize(m.quat_mul(q_align_primary, current_quat))

    _, current_up_world, _ = m.extract_basis(_quat_to_basis_mat(q_partial))
    up_proj_target = _project_onto_perp(canonical.up, canonical.primary)
    up_proj_current = _project_onto_perp(current_up_world, canonical.primary)
    if m.vec_length(up_proj_target) < m.EPSILON or m.vec_length(up_proj_current) < m.EPSILON:
        return q_partial

    q_twist = _quat_align_around_axis(
        up_proj_current, up_proj_target, canonical.primary,
    )
    return m.quat_normalize(m.quat_mul(q_twist, q_partial))


def _quat_to_basis_mat(q: m.Quat) -> m.Mat4:
    """Convert a unit quaternion to the equivalent rotation matrix (no translation)."""
    w, x, y, z = m.quat_normalize(q)
    xx = x * x; yy = y * y; zz = z * z
    xy = x * y; xz = x * z; yz = y * z
    wx = w * x; wy = w * y; wz = w * z
    return (
        1.0 - 2.0 * (yy + zz), 2.0 * (xy + wz),       2.0 * (xz - wy),       0.0,
        2.0 * (xy - wz),       1.0 - 2.0 * (xx + zz), 2.0 * (yz + wx),       0.0,
        2.0 * (xz + wy),       2.0 * (yz - wx),       1.0 - 2.0 * (xx + yy), 0.0,
        0.0,                   0.0,                   0.0,                   1.0,
    )


def _project_onto_perp(v: m.Vec3, axis: m.Vec3) -> m.Vec3:
    n = m.vec_normalize(axis)
    d = m.vec_dot(v, n)
    return m.vec_sub(v, m.vec_scale(n, d))


def _quat_align_around_axis(src: m.Vec3, dst: m.Vec3, axis: m.Vec3) -> m.Quat:
    """Rotation around ``axis`` that takes ``src`` direction to ``dst``."""
    a = m.vec_normalize(_project_onto_perp(src, axis))
    b = m.vec_normalize(_project_onto_perp(dst, axis))
    if m.vec_length(a) < m.EPSILON or m.vec_length(b) < m.EPSILON:
        return m.quat_identity()
    cos_a = max(-1.0, min(1.0, m.vec_dot(a, b)))
    angle = math.acos(cos_a)
    cross = m.vec_cross(a, b)
    sign = 1.0 if m.vec_dot(cross, m.vec_normalize(axis)) >= 0.0 else -1.0
    return m.quat_from_axis_angle(m.vec_normalize(axis), sign * angle)


_BENDABLE_NODES = frozenset({
    "kFBLeftElbowNodeId",
    "kFBRightElbowNodeId",
    "kFBLeftKneeNodeId",
    "kFBRightKneeNodeId",
})


def _apply_micro_bend(
    node_name: str,
    target_quat: m.Quat,
    canonical: CanonicalDir,
    bend_deg: float,
) -> m.Quat:
    """Add a tiny anatomical bend to elbows and knees.

    Both elbows and knees naturally fold so that their tip moves toward the
    character's front (+Z world). We compute the rotation axis as
    ``cross(primary, +Z)`` so the same code path works for left/right and
    arms/legs without sign juggling.
    """
    if node_name not in _BENDABLE_NODES or bend_deg <= 0.0:
        return target_quat

    bend_axis = m.vec_normalize(m.vec_cross(canonical.primary, (0.0, 0.0, 1.0)))
    if m.vec_length(bend_axis) < m.EPSILON:
        return target_quat
    q_bend = m.quat_from_axis_angle(bend_axis, math.radians(bend_deg))
    return m.quat_normalize(m.quat_mul(q_bend, target_quat))


def _apply_world_rotation(bone, q_world_target: m.Quat) -> Tuple[float, float, float]:
    """Set ``bone``'s world rotation to ``q_world_target``.

    Returns the world-space Euler degrees that were written. We use
    ``FBModel.SetVector(... , kModelRotation, True)`` so MotionBuilder
    handles the local conversion taking the parent's world transform into
    account.
    """
    from pyfbsdk import FBVector3d, FBModelTransformationType  # type: ignore

    rx, ry, rz = m.quat_to_euler_xyz_deg(q_world_target)
    bone.SetVector(
        FBVector3d(rx, ry, rz),
        FBModelTransformationType.kModelRotation,
        True,
    )
    return (rx, ry, rz)


def _bone_world_rotation_euler(bone) -> Tuple[float, float, float]:
    """Read a bone's current world rotation as Euler degrees."""
    from pyfbsdk import FBVector3d, FBModelTransformationType  # type: ignore

    out = FBVector3d()
    bone.GetVector(out, FBModelTransformationType.kModelRotation, True)
    return (float(out[0]), float(out[1]), float(out[2]))


def _recharacterize_to_capture_stance(character) -> None:
    """Re-run the characterize cycle so the current bone pose becomes the
    new stance. Required after we modify bone rotations because HumanIK
    captured the original stance at the previous SetCharacterizeOn call.

    Both arguments here are ``False`` ("don't change active state"),
    which keeps the existing characterization mapping intact while
    refreshing the stance reference.
    """
    try:
        character.SetCharacterizeOff(False)
        character.SetCharacterizeOn(False)
    except Exception:
        pass


def _align_chain(
    character,
    chain: ChainId,
    options: AlignOptions,
    result: AlignResult,
    evaluate,
) -> None:
    """Align every bone in a single chain, parent first."""
    names = CHAIN_TO_NODE_NAMES[chain]
    for name in names:
        canonical = canonical_dir_for(name)
        if canonical is None:
            continue
        node_id = resolve_node_id(name)
        if node_id is None:
            result.skipped.append((name, "FBBodyNodeId not in this MotionBuilder version"))
            continue

        bone = character.GetModel(node_id)
        if bone is None:
            result.skipped.append((name, "Character has no bone mapped to this node"))
            continue

        evaluate()
        bone_mat = _get_world_matrix(bone)
        if bone_mat is None:
            result.skipped.append((name, "Could not read world matrix"))
            continue

        child = _first_skeleton_child(bone)
        if child is None:
            result.skipped.append((name, "No child bone for direction reference"))
            continue
        child_pos = _world_position(child)
        if child_pos is None:
            result.skipped.append((name, "Could not read child world position"))
            continue

        current_primary, current_quat = _measure_current_orientation(bone_mat, child_pos)
        if m.vec_length(current_primary) < m.EPSILON:
            result.skipped.append((name, "Bone has zero length to its child"))
            continue

        target_quat = _build_target_quaternion(current_primary, current_quat, canonical)
        if options.preserve_micro_bend:
            target_quat = _apply_micro_bend(
                name, target_quat, canonical, options.micro_bend_deg,
            )

        q_offset = m.quat_relative(current_quat, target_quat)
        if options.handle_wrist_flip:
            q_offset = m.shortest_equivalent(q_offset)
            target_quat = m.quat_normalize(m.quat_mul(q_offset, current_quat))

        _apply_world_rotation(bone, target_quat)
        ox, oy, oz = m.quat_to_euler_xyz_deg(q_offset)
        result.offsets[name] = (ox, oy, oz)
        result.grades[name] = categorize_offset(
            ox, oy, oz,
            options.warn_threshold_deg, options.high_threshold_deg,
        )
        if result.grades[name].severity != Severity.OK:
            result.warnings.append((name, result.grades[name].note))

    evaluate()


def _sync_twist_bones(character, options: AlignOptions, result: AlignResult, evaluate) -> None:
    """No-op under the bone-rotation approach.

    Twist / roll bones are children of the elbow / shoulder / hip / knee
    joints in the HumanIK skeleton. When we rotate the parent bone the
    twist children inherit that rotation automatically, so no separate
    "sync" pass is required. We keep the function so callers don't have
    to be conditional, and so future schemes that need explicit twist
    handling have a clear hook.
    """
    if options.chains is not None and ChainId.TWIST not in options.chains:
        return
    evaluate()


_HAND_FINGER_REFS: Dict[str, Tuple[str, str]] = {
    "kFBLeftWristNodeId": ("kFBLeftIndexANodeId", "kFBLeftPinkyANodeId"),
    "kFBRightWristNodeId": ("kFBRightIndexANodeId", "kFBRightPinkyANodeId"),
}


def _orient_palm_down(character, options: AlignOptions, result: AlignResult, evaluate) -> None:
    """Tilt the wrist around its primary axis until the palm faces ``-Y``."""
    if not options.palms_down:
        return

    for wrist_name, (index_name, pinky_name) in _HAND_FINGER_REFS.items():
        wrist_id = resolve_node_id(wrist_name)
        index_id = resolve_node_id(index_name)
        pinky_id = resolve_node_id(pinky_name)
        if wrist_id is None or index_id is None or pinky_id is None:
            continue

        wrist = character.GetModel(wrist_id)
        index = character.GetModel(index_id)
        pinky = character.GetModel(pinky_id)
        if wrist is None or index is None or pinky is None:
            continue

        evaluate()
        wrist_mat = _get_world_matrix(wrist)
        if wrist_mat is None:
            continue
        wrist_pos = m.matrix_translation(wrist_mat)
        index_pos = _world_position(index)
        pinky_pos = _world_position(pinky)
        if index_pos is None or pinky_pos is None:
            continue

        canonical = canonical_dir_for(wrist_name)
        if canonical is None:
            continue
        primary = canonical.primary

        across = m.vec_normalize(m.vec_sub(pinky_pos, index_pos))
        finger_dir = m.vec_normalize(
            m.vec_sub(
                m.vec_scale(m.vec_add(index_pos, pinky_pos), 0.5),
                wrist_pos,
            ),
        )
        if m.vec_length(across) < m.EPSILON or m.vec_length(finger_dir) < m.EPSILON:
            continue
        palm_normal_current = m.vec_normalize(m.vec_cross(across, finger_dir))
        if m.vec_length(palm_normal_current) < m.EPSILON:
            continue

        palm_target = (0.0, -1.0, 0.0)
        q_twist = _quat_align_around_axis(palm_normal_current, palm_target, primary)
        if m.degrees_magnitude(q_twist) < 0.5:
            continue

        evaluate()
        wrist_mat_after = _get_world_matrix(wrist)
        if wrist_mat_after is None:
            continue
        current_quat = m.quat_from_matrix(wrist_mat_after)
        target_quat = m.quat_normalize(m.quat_mul(q_twist, current_quat))
        try:
            _apply_world_rotation(wrist, target_quat)
            ox, oy, oz = m.quat_to_euler_xyz_deg(q_twist)
            existing = result.offsets.get(wrist_name, (0.0, 0.0, 0.0))
            combined = (existing[0] + ox, existing[1] + oy, existing[2] + oz)
            result.offsets[wrist_name] = combined
            result.grades[wrist_name] = categorize_offset(
                *combined,
                options.warn_threshold_deg, options.high_threshold_deg,
            )
        except Exception as exc:
            result.skipped.append((wrist_name, f"Palm orient failed: {exc}"))
    evaluate()


_FOOT_TOE_REFS: Dict[str, str] = {
    "kFBLeftAnkleNodeId": "kFBLeftFootNodeId",
    "kFBRightAnkleNodeId": "kFBRightFootNodeId",
}


def _orient_feet_flat(character, options: AlignOptions, result: AlignResult, evaluate) -> None:
    """Make sure the foot's forward (toe) direction is ``+Z`` and sole down."""
    if not options.feet_flat_forward:
        return

    for ankle_name, toe_name in _FOOT_TOE_REFS.items():
        ankle_id = resolve_node_id(ankle_name)
        toe_id = resolve_node_id(toe_name)
        if ankle_id is None or toe_id is None:
            continue

        ankle = character.GetModel(ankle_id)
        toe = character.GetModel(toe_id)
        if ankle is None or toe is None:
            continue

        evaluate()
        ankle_mat = _get_world_matrix(ankle)
        if ankle_mat is None:
            continue
        ankle_pos = m.matrix_translation(ankle_mat)
        toe_pos = _world_position(toe)
        if toe_pos is None:
            continue

        forward_current = m.vec_normalize(m.vec_sub(toe_pos, ankle_pos))
        forward_target = (0.0, 0.0, 1.0)
        q_align = m.quat_from_two_vectors(forward_current, forward_target)
        if m.degrees_magnitude(q_align) < 0.5:
            continue

        evaluate()
        ankle_mat_after = _get_world_matrix(ankle)
        if ankle_mat_after is None:
            continue
        current_quat = m.quat_from_matrix(ankle_mat_after)
        target_quat = m.quat_normalize(m.quat_mul(q_align, current_quat))
        try:
            _apply_world_rotation(ankle, target_quat)
            ox, oy, oz = m.quat_to_euler_xyz_deg(q_align)
            existing = result.offsets.get(ankle_name, (0.0, 0.0, 0.0))
            combined = (existing[0] + ox, existing[1] + oy, existing[2] + oz)
            result.offsets[ankle_name] = combined
            result.grades[ankle_name] = categorize_offset(
                *combined,
                options.warn_threshold_deg, options.high_threshold_deg,
            )
        except Exception as exc:
            result.skipped.append((ankle_name, f"Foot orient failed: {exc}"))
    evaluate()


def _make_evaluator():
    """Return a callable that evaluates the scene once."""
    from pyfbsdk import FBSystem  # type: ignore
    scene = FBSystem().Scene
    return scene.Evaluate


def align_character_to_canonical_tpose(
    character,
    options: Optional[AlignOptions] = None,
) -> AlignResult:
    """Align a single character's stance pose to the canonical T-Pose.

    Returns an :class:`AlignResult` describing what was applied. Raises
    ``RuntimeError`` if ``options.require_y_up`` is True and the scene is
    not Y-up.
    """
    options = options or AlignOptions()
    if options.require_y_up:
        assert_y_up_scene()

    result = AlignResult(character_name=character.LongName)
    result.pre_snapshot = capture(character)

    previous_active = bool(getattr(character, "ActiveInput", False))
    try:
        character.ActiveInput = False
    except Exception:
        pass

    if options.clear_existing:
        reset_all_offsets(character)

    try:
        character.GoToStancePose(options.push_undo, True)
    except TypeError:
        try:
            character.GoToStancePose(options.push_undo)
        except Exception:
            pass
    except Exception:
        pass

    evaluate = _make_evaluator()
    evaluate()

    for chain in _selected_chains(options):
        if chain is ChainId.TWIST:
            continue
        _align_chain(character, chain, options, result, evaluate)

    _orient_palm_down(character, options, result, evaluate)
    _orient_feet_flat(character, options, result, evaluate)
    _sync_twist_bones(character, options, result, evaluate)

    if options.update_stance_after:
        _recharacterize_to_capture_stance(character)
        evaluate()

    try:
        character.ActiveInput = previous_active
    except Exception:
        pass

    result.post_snapshot = capture(character)
    return result


def align_pair(
    source,
    target,
    options: Optional[AlignOptions] = None,
) -> Tuple[AlignResult, AlignResult]:
    """Align both characters with the same options.

    The two characters are aligned independently, but because both target
    the same canonical T-Pose the *relative* alignment between them is
    automatically what HumanIK retargeting expects.
    """
    options = options or AlignOptions()
    src_result = align_character_to_canonical_tpose(source, options)
    tgt_result = align_character_to_canonical_tpose(target, options)
    return src_result, tgt_result


def connect_for_retarget(source, target, activate: bool = True) -> None:
    """Wire ``target.InputCharacter = source`` for live preview / plot."""
    from pyfbsdk import FBCharacterInputType  # type: ignore
    target.InputCharacter = source
    target.InputType = FBCharacterInputType.kFBCharacterInputCharacter
    target.ActiveInput = bool(activate)
