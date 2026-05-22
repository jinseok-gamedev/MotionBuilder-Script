"""FBX import / export glue.

Three responsibilities live in this module:

1. ``import_animation_only(fbx_path)`` -- merge an FBX file into the current
   scene bringing **only animation curves** with it. The scene's existing
   source skeleton (defined by the setting file) acts as the target; bones
   are matched by name and the keys land on them.

2. ``export_take_to_fbx(...)`` -- save a single take to its own FBX file,
   selecting only the target character's skeleton so the output FBX is
   minimal (no source rig, no characters, no scene clutter).

3. ``inject_metadata(...)`` -- decorate a bone with custom user properties so
   downstream DCCs (Maya / Max / UE5) can trace which source file, take,
   author, plot rate, and root motion mode produced the FBX.
"""

from __future__ import annotations

import getpass
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from pyfbsdk import (  # type: ignore
    FBApplication,
    FBCharacterInputType,
    FBElementAction,
    FBFbxOptions,
    FBModelList,
    FBNamespaceAction,
    FBPropertyType,
    FBSystem,
    FBTake,
    FBVector3d,
)

from .scene_utils import (
    collect_scene_bone_long_names,
    collect_scene_bone_names,
    collect_scene_models_in_namespace,
    find_character_by_name,
    get_character_namespace,
    get_target_skeleton_models,
)
from .take_manager import all_take_names, get_take_by_name, takes_added_since


_METADATA_PROPERTY_NAME = "RetargetInfo"
_EXPORT_OFFSET_ROOT_NAMES = {"root_offset", "root offset", "root-offset"}


@dataclass
class ExportMetadata:
    """Provenance metadata attached to each exported FBX.

    Stored as a JSON string in a single custom property so it survives FBX
    round-trips through Maya / Max / UE5 without depending on how those tools
    serialise multiple string attributes.
    """

    source_path: str = ""
    source_take: str = ""
    target_character: str = ""
    plot_rate: int = 30
    root_motion_mode: str = "keep"
    tool_version: str = "0.1.0"
    author: str = field(default_factory=lambda: _safe_user())
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    extras: Dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        d = {
            "source_path": self.source_path,
            "source_take": self.source_take,
            "target_character": self.target_character,
            "plot_rate": self.plot_rate,
            "root_motion_mode": self.root_motion_mode,
            "tool_version": self.tool_version,
            "author": self.author,
            "timestamp": self.timestamp,
        }
        if self.extras:
            d["extras"] = dict(self.extras)
        return json.dumps(d, ensure_ascii=False)


def _safe_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


# ----------------------------------------------------------------------------
# Import
# ----------------------------------------------------------------------------


def _snapshot_target_transforms(target_character) -> Dict[str, Tuple]:
    """Snapshot every target-character bone's Translation/Rotation/Scaling.

    Returns a dict keyed by the bone's ``LongName`` (stable across the
    merge because target bones already exist in the scene) carrying
    ``(t_xyz, r_xyz, s_xyz)`` tuples of floats. Each component slot may be
    ``None`` if that property could not be read (we still record the bone
    so :func:`_restore_target_transforms` can skip it gracefully).

    Why ``LongName``: short names alone are ambiguous when source and
    target characters share bone names (UE4 vs UE5 mannequin both having
    ``hand_l``); the full ``Namespace:Hierarchy:Name`` path is the only
    handle that uniquely points at the *target* copy.
    """
    out: Dict[str, Tuple] = {}
    if target_character is None:
        return out
    for model in get_target_skeleton_models(target_character):
        try:
            long_name = model.LongName
        except Exception:
            continue
        t_val = _read_vector3(model, "Translation")
        r_val = _read_vector3(model, "Rotation")
        s_val = _read_vector3(model, "Scaling")
        out[long_name] = (t_val, r_val, s_val)
    return out


def _read_vector3(model, prop_name: str) -> Optional[Tuple[float, float, float]]:
    """Read an FBVector3d property as a plain tuple, or ``None`` on failure."""
    prop = getattr(model, prop_name, None)
    if prop is None:
        return None
    try:
        data = prop.Data
    except Exception:
        return None
    try:
        return (float(data[0]), float(data[1]), float(data[2]))
    except Exception:
        return None


def _restore_target_transforms(
    snapshot: Dict[str, Tuple],
    logger=None,
    epsilon: float = 1e-5,
) -> Tuple[int, List[Tuple]]:
    """Restore any target bone whose transform differs from ``snapshot``.

    Returns ``(restored_count, changed_records)`` where ``changed_records``
    is a list of ``(long_name, before_tuple, after_tuple)`` for callers
    (e.g. pipeline diagnostics) to surface. ``before`` and ``after`` use
    the ``(t, r, s)`` shape produced by :func:`_snapshot_target_transforms`.

    ``epsilon`` controls the per-component tolerance for "no real change";
    FBX float round-trip can introduce sub-millimeter noise that is not a
    real mutation and should not trigger a warning or a restore.
    """
    if not snapshot:
        return 0, []
    restored = 0
    changed: List[Tuple] = []
    for long_name, before in snapshot.items():
        model = _find_model_by_long_name(long_name)
        if model is None:
            continue
        after_t = _read_vector3(model, "Translation")
        after_r = _read_vector3(model, "Rotation")
        after_s = _read_vector3(model, "Scaling")
        after = (after_t, after_r, after_s)
        if not _transforms_differ(before, after, epsilon):
            continue
        changed.append((long_name, before, after))
        # Restore each component independently in case only one channel
        # was clobbered. Skip ``None`` slots (couldn't read at snapshot
        # time, nothing meaningful to restore to).
        for prop_name, value in (
            ("Translation", before[0]),
            ("Rotation", before[1]),
            ("Scaling", before[2]),
        ):
            if value is None:
                continue
            try:
                prop = getattr(model, prop_name, None)
                if prop is None:
                    continue
                prop.Data = FBVector3d(value[0], value[1], value[2])
            except Exception as exc:
                if logger is not None:
                    try:
                        logger.warn(
                            f"  protect_target: restore {prop_name} failed on "
                            f"'{long_name}': {exc!r}"
                        )
                    except Exception:
                        pass
        restored += 1
    if logger is not None and changed:
        try:
            logger.warn(
                f"  protect_target: {len(changed)} target bone(s) were mutated by "
                f"the merge; restored {restored} to pre-import values. This "
                "almost always means an incoming FBX bone collided with a "
                "target-character bone short name."
            )
            for long_name, before, after in changed[:5]:
                logger.warn(
                    f"    '{long_name}' before T={_fmt_v(before[0])} "
                    f"R={_fmt_v(before[1])} S={_fmt_v(before[2])}"
                )
                logger.warn(
                    f"    '{long_name}' after  T={_fmt_v(after[0])} "
                    f"R={_fmt_v(after[1])} S={_fmt_v(after[2])}"
                )
            if len(changed) > 5:
                logger.warn(f"    ... (+{len(changed) - 5} more)")
        except Exception:
            pass
    return restored, changed


def _transforms_differ(before: Tuple, after: Tuple, epsilon: float) -> bool:
    for b, a in zip(before, after):
        if b is None or a is None:
            if b is not a:
                return True
            continue
        for bv, av in zip(b, a):
            if abs(float(bv) - float(av)) > epsilon:
                return True
    return False


def _fmt_v(v) -> str:
    if v is None:
        return "?"
    try:
        return f"({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f})"
    except Exception:
        return repr(v)


def _isolate_all_character_inputs() -> List[Tuple]:
    """Disconnect every HIK character's live input and snapshot prior state.

    MotionBuilder crashes inside ``FBApplication.FileMerge`` when the incoming
    skeleton binds to slots of a character whose ``ActiveInput`` is True. The
    repro is deterministic on a setting scene that hosts both a source and a
    target HIK character; the same FBX merges cleanly via ``File > Merge`` and
    via Python when no character is active.

    Returns a list of ``(character, active, input_type, input_character)``
    tuples to be passed verbatim to :func:`_restore_character_inputs`.

    The ``InputCharacter = None`` assignment is intentionally NOT attempted
    here: some MotionBuilder Python builds (Boost.Python) reject ``None`` with
    ``Boost.Python.ArgumentError: None.None(FBCharacter, NoneType)``. Setting
    ``ActiveInput = False`` and ``InputType = kFBCharacterInputStance`` is
    enough to make the merge safe in practice.
    """
    snapshot: List[Tuple] = []
    try:
        characters = list(FBSystem().Scene.Characters)
    except Exception:
        return snapshot

    for char in characters:
        try:
            active = bool(char.ActiveInput)
        except Exception:
            active = None
        try:
            itype = char.InputType
        except Exception:
            itype = None
        try:
            inchar = char.InputCharacter
        except Exception:
            inchar = None
        snapshot.append((char, active, itype, inchar))
        try:
            char.ActiveInput = False
        except Exception:
            pass
        try:
            char.InputType = FBCharacterInputType.kFBCharacterInputStance
        except Exception:
            pass
    return snapshot


def _restore_character_inputs(snapshot: List[Tuple]) -> None:
    """Inverse of :func:`_isolate_all_character_inputs`.

    Restore InputCharacter first, then InputType, then ActiveInput so that the
    final flip to active happens on a fully reattached input. Every step is
    best-effort; failures are silently skipped because the merge is already
    done and we should not block the caller on a cosmetic restore.
    """
    for char, active, itype, inchar in snapshot:
        if inchar is not None:
            try:
                char.InputCharacter = inchar
            except Exception:
                pass
        if itype is not None:
            try:
                char.InputType = itype
            except Exception:
                pass
        if active is not None:
            try:
                char.ActiveInput = bool(active)
            except Exception:
                pass


def import_animation_only(
    fbx_path: str,
    source_character_name: str = "",
    target_character_name: str = "",
    protect_target_transforms: bool = True,
    import_base_models_animation: bool = True,
    diagnostics: Optional[Dict] = None,
    logger=None,
) -> List[FBTake]:
    """Merge ``fbx_path`` bringing animation only, return new takes.

    The existing source rig in the scene receives the keys via name-based
    binding. Geometry, materials, lights, cameras and characters in the source
    file are discarded.

    ``source_character_name`` is used to discover the source rig's namespace
    so incoming bones can be prefixed into it (otherwise ``Hips`` in the FBX
    would not bind to ``SrcRig:Hips`` in the scene and MotionBuilder would
    create a new bone tree).

    ``target_character_name`` enables a sanity check: when
    ``protect_target_transforms`` is True (default), every target bone's
    Translation/Rotation/Scaling is snapshotted right before the merge and
    compared right after. Any bone the merge mutated is restored to its
    pre-import value. This is a guard against the failure mode where the
    incoming FBX's short bone names collide with the *target* character's
    bones (UE4 source FBX merging into a scene that also has UE5 mannequin
    targets, e.g. shared ``hand_l`` short name) and the merge resets those
    target bones to (0,0,0).

    ``import_base_models_animation`` toggles ``FBFbxOptions.BaseModelsAnimation``;
    see :func:`_build_import_options` for details.

    ``diagnostics``, when provided, is populated with merge bookkeeping:
        - ``new_models``: names of FBModels that did not exist before merge
        - ``namespace_target``: namespace the merger was asked to remap into
        - ``namespace_remap_applied``: bool, did the assignment take effect
        - ``namespace_remap_error``: str, repr(exc) when the call failed
        - ``source_bones``: bone short names bound to a HumanIK character slot
        - ``source_bone_long_names``: matching ``LongName`` set
        - ``source_namespace_bones``: short names of EVERY model under the
          source rig's namespace, including non-HIK helper bones
          (ik_hand_root, ik_foot_root, root, ...). The cleanup pass matches
          incoming duplicates against this wider set so helper bones get
          reconciled too.
        - ``source_namespace_bone_long_names``: matching ``LongName`` set,
          used as the "do not delete" exclusion list during cleanup.
        - ``target_transforms_restored``: int, count of target bones rescued
        - ``target_transforms_changed``: list of (long_name, before, after)
          tuples for diagnostic logging

    Raises ``IOError`` if MotionBuilder reports that the merge failed.
    """
    if not os.path.isfile(fbx_path):
        raise IOError(f"FBX not found: {fbx_path}")

    source_char = (
        find_character_by_name(source_character_name) if source_character_name else None
    )
    target_char = (
        find_character_by_name(target_character_name) if target_character_name else None
    )
    target_namespace = get_character_namespace(source_char) if source_char else ""
    source_bones = collect_scene_bone_names(source_char) if source_char else []
    source_bone_long_names = (
        collect_scene_bone_long_names(source_char) if source_char else []
    )

    # Wider snapshot: every model currently sitting under the source rig's
    # namespace, including helper bones that are not bound into a HumanIK
    # slot (ik_hand_root, ik_foot_root, interaction, center_of_mass, root).
    # Used by the post-merge cleanup so an incremented-namespace
    # duplicate of one of those helpers (e.g. UE6:ik_hand_root) still gets
    # transferred back onto UE5:ik_hand_root instead of being left behind.
    source_namespace_models = (
        collect_scene_models_in_namespace(target_namespace) if target_namespace else []
    )
    source_namespace_bones: List[str] = []
    source_namespace_bone_long_names: List[str] = []
    for _m in source_namespace_models:
        try:
            _short = _m.Name or ""
        except Exception:
            _short = ""
        try:
            _long = _m.LongName or ""
        except Exception:
            _long = ""
        if _short:
            source_namespace_bones.append(_short)
        if _long:
            source_namespace_bone_long_names.append(_long)

    target_transform_snapshot: Dict[str, Tuple] = {}
    if protect_target_transforms and target_char is not None:
        target_transform_snapshot = _snapshot_target_transforms(target_char)

    pre_models = _snapshot_scene_model_names()
    snapshot = all_take_names()
    # Snapshot each existing take's end time so we can tell which take MoBu
    # piped animation onto when the merge does not create a new take (typical
    # for binary FBXs whose internal take name already exists in the scene).
    pre_take_durations = _snapshot_take_durations()
    app = FBApplication()
    use_raw_merge = not target_namespace

    # Do not construct FBFbxOptions for no-namespace imports. Some valid FBXs
    # crash MotionBuilder during FBFbxOptions(True, path) even though manual
    # File > Merge succeeds. With an empty source namespace we do not need the
    # options object for remapping, so use the raw merge path.
    fbx_namespaces: List[str] = []
    fbx_take_names = _scan_fbx_take_names(fbx_path)

    # HIK characters with an active input cause MotionBuilder to crash inside
    # FileMerge when the incoming bones bind to characterized slots (observed
    # repro: a UE5 mannequin animation FBX merged into a scene with both a
    # source and a target HIK character active). Disable every character's
    # live input around the merge and restore it afterwards.
    char_input_snapshot = _isolate_all_character_inputs()
    try:
        if use_raw_merge:
            success = app.FileMerge(fbx_path, False)
        else:
            opts = _build_import_options(
                fbx_path,
                target_namespace=target_namespace,
                diagnostics=diagnostics,
                import_base_models_animation=import_base_models_animation,
            )
            fbx_namespaces = _collect_fbx_namespaces(opts)
            if not fbx_take_names:
                fbx_take_names = _collect_fbx_take_names(opts)
            success = app.FileMerge(fbx_path, False, opts)
    finally:
        _restore_character_inputs(char_input_snapshot)
    if not success:
        raise IOError(f"FileMerge returned False for {fbx_path}")

    restored_count = 0
    changed_records: List[Tuple] = []
    if target_transform_snapshot:
        restored_count, changed_records = _restore_target_transforms(
            target_transform_snapshot, logger=logger
        )

    if diagnostics is not None:
        post_models = _snapshot_scene_model_names()
        new_models = sorted(post_models - pre_models)
        diagnostics["new_models"] = new_models
        diagnostics["namespace_target"] = target_namespace
        diagnostics["source_bones"] = source_bones
        diagnostics["source_bone_long_names"] = source_bone_long_names
        diagnostics["source_namespace_bones"] = source_namespace_bones
        diagnostics["source_namespace_bone_long_names"] = source_namespace_bone_long_names
        diagnostics["fbx_namespaces"] = fbx_namespaces
        diagnostics["fbx_take_names"] = fbx_take_names
        diagnostics["import_mode"] = "raw_merge" if use_raw_merge else "options_merge"
        diagnostics["target_transforms_restored"] = restored_count
        diagnostics["target_transforms_changed"] = changed_records
        diagnostics.setdefault("namespace_remap_applied", False)
        diagnostics.setdefault("namespace_remap_error", "")
        # Use the namespace-wide bone set so the increment detector also
        # spots cases where the only incoming duplicates are helper bones
        # (ik_hand_root, ik_foot_root, ...) that are not bound to any HIK
        # slot. Falls back to the slot set if the rig has no namespace.
        _detect_bones = source_namespace_bones or source_bones
        _detect_long_names = (
            source_namespace_bone_long_names or source_bone_long_names
        )
        diagnostics["namespace_incremented_to"] = _detect_namespace_increment(
            new_models, target_namespace, _detect_bones, _detect_long_names
        )

    new_takes = takes_added_since(snapshot)
    if new_takes:
        return new_takes

    fallback_take = _resolve_existing_import_take(
        snapshot, fbx_take_names, pre_take_durations
    )
    if fallback_take is not None:
        if diagnostics is not None:
            diagnostics["reused_existing_take"] = getattr(fallback_take, "Name", "")
        return [fallback_take]

    return []


def _collect_fbx_take_names(opts: FBFbxOptions) -> List[str]:
    """Return take names advertised by the FBX import options."""
    out: List[str] = []
    try:
        for i in range(opts.GetTakeCount()):
            try:
                name = str(opts.GetTakeName(i) or "")
            except Exception:
                continue
            if name:
                out.append(name)
    except Exception:
        pass
    return out


def _scan_fbx_take_names(fbx_path: str) -> List[str]:
    """Lightweight take-name scan that avoids MotionBuilder's FBX parser."""
    try:
        with open(fbx_path, "rb") as fh:
            text = fh.read().decode("latin1", "ignore")
    except Exception:
        return []

    out: List[str] = []
    seen = set()
    for pattern in (
        r'Take:\s*"([^"]+)"',
        r'AnimStack::([^"]+)"',
        r'AnimationStack[^\n\r"]*"AnimStack::([^"]+)"',
    ):
        for match in re.finditer(pattern, text):
            name = match.group(1).strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _snapshot_take_durations() -> Dict[str, float]:
    """End-time (in seconds) of every take currently in the scene.

    Used to detect which take a ``FileMerge`` piped animation onto when no
    new take was created. By snapshotting durations before and after the
    merge, any take whose end time changed must be the one that received
    the incoming animation. This survives binary FBXs whose internal take
    names we cannot sniff with the ASCII regex.
    """
    out: Dict[str, float] = {}
    try:
        takes = list(FBSystem().Scene.Takes)
    except Exception:
        return out
    for take in takes:
        try:
            span = take.LocalTimeSpan
            out[take.Name] = float(span.GetStop().GetSecondDouble())
        except Exception:
            continue
    return out


def _resolve_existing_import_take(
    snapshot_names: Iterable[str],
    fbx_take_names: List[str],
    pre_take_durations: Optional[Dict[str, float]] = None,
):
    """Handle FBXs that merge animation into an existing/current take.

    MotionBuilder sometimes does not create a new take when the incoming FBX
    take name already exists in the scene. The animation is still merged onto
    that existing take, so return it instead of treating the import as empty.

    Resolution order, first match wins:

    1. **FBX-advertised take name** (only useful for ASCII FBX or when the
       options object successfully populated ``fbx_take_names``).
    2. **Duration delta** -- any take whose LocalTimeSpan grew between
       ``pre_take_durations`` and now. This is the robust path for binary
       FBXs because it does not depend on string sniffing.
    3. **Scene's current take** -- last-ditch fallback if MoBu silently piped
       keys onto the active take without changing its time span.
    """
    snapshot = set(snapshot_names)

    for take_name in fbx_take_names or []:
        if take_name not in snapshot:
            continue
        take = get_take_by_name(take_name)
        if take is not None:
            return take

    if pre_take_durations:
        post = _snapshot_take_durations()
        # Prefer the take whose duration grew the most: a 215s anim landing on
        # a 1s placeholder is unmistakable, whereas tiny floating-point drift
        # on unrelated takes should not win.
        best_name: Optional[str] = None
        best_delta = 1e-6
        for name, after_end in post.items():
            if name not in snapshot:
                continue
            before_end = pre_take_durations.get(name, 0.0)
            delta = after_end - before_end
            if delta > best_delta:
                best_delta = delta
                best_name = name
        if best_name is not None:
            take = get_take_by_name(best_name)
            if take is not None:
                return take

    try:
        current_take = FBSystem().CurrentTake
    except Exception:
        current_take = None
    if current_take is not None and getattr(current_take, "Name", "") in snapshot:
        return current_take
    return None


def _snapshot_scene_model_names() -> set:
    """Return every model's ``LongName`` in the current scene.

    Used to diff scene contents around a FileMerge call so we can report which
    bones the merge actually created (vs. bound to existing ones).
    """
    from pyfbsdk import FBSystem  # type: ignore

    out: set = set()
    root = FBSystem().Scene.RootModel
    if root is None:
        return out
    _walk_model_long_names(root, out)
    return out


def _walk_model_long_names(node, out: set) -> None:
    try:
        children = list(node.Children)
    except Exception:
        return
    for c in children:
        try:
            out.add(c.LongName)
        except Exception:
            pass
        _walk_model_long_names(c, out)


def _build_import_options(
    fbx_path: str,
    target_namespace: str = "",
    diagnostics: Optional[Dict] = None,
    import_base_models_animation: bool = True,
) -> FBFbxOptions:
    """Configure FBFbxOptions for an animation-only merge.

    ``target_namespace`` (if non-empty) is used to prefix every loaded
    object with that namespace so its bones merge into the in-scene
    source rig. Without this, bones like ``Hips`` in the FBX cannot bind
    to ``SrcRig:Hips`` in the scene and MotionBuilder creates a parallel
    skeleton.

    ``import_base_models_animation`` (default True) toggles the
    ``BaseModelsAnimation`` option on ``FBFbxOptions``. When two characters
    in the scene share short bone names (UE4 vs UE5 mannequin), a buggy
    merge can let the FBX's base transform overwrite the target rig's
    bones. Setting this to False makes the merge import only animation
    curves (no base T/R/S), which is the safer option in pure
    "drop animation onto an existing rig" workflows. The default keeps
    the historical behaviour because some setups rely on base transforms
    arriving with the merge.
    """
    opts = FBFbxOptions(True, fbx_path)

    # Keep MotionBuilder's default import profile close to manual File > Merge.
    # Some FBXs crash MoBu when we first call SettingsByDefault(False) and then
    # selectively re-enable categories. We still force the essentials below and
    # discard non-animation scene clutter where the SDK exposes a stable flag.
    _set_action(opts, "Models", FBElementAction.kFBElementActionMerge)
    _set_bool(opts, "ModelsAnimation", True)
    _set_bool(opts, "BaseModelsAnimation", bool(import_base_models_animation))
    _set_bool(opts, "Animation", True)

    # Everything else stays out of the setting file when the option exists.
    # Model geometry shares the Models bucket with skeletons in many MoBu
    # versions, so meshes are ignored later by selection/export cleanup rather
    # than by disabling Models here.
    for attr, action in (
        ("Lights", FBElementAction.kFBElementActionDiscard),
        ("Cameras", FBElementAction.kFBElementActionDiscard),
        ("Materials", FBElementAction.kFBElementActionDiscard),
        ("Textures", FBElementAction.kFBElementActionDiscard),
        ("Shaders", FBElementAction.kFBElementActionDiscard),
        ("Audio", FBElementAction.kFBElementActionDiscard),
        ("Characters", FBElementAction.kFBElementActionDiscard),
        ("CharactersExtensions", FBElementAction.kFBElementActionDiscard),
        ("Constraints", FBElementAction.kFBElementActionDiscard),
        ("Devices", FBElementAction.kFBElementActionDiscard),
        ("FileReferences", FBElementAction.kFBElementActionDiscard),
        ("Notes", FBElementAction.kFBElementActionDiscard),
        ("ActorFaces", FBElementAction.kFBElementActionDiscard),
        ("Actors", FBElementAction.kFBElementActionDiscard),
        ("Solvers", FBElementAction.kFBElementActionDiscard),
        ("PhysicalProperties", FBElementAction.kFBElementActionDiscard),
        ("Groups", FBElementAction.kFBElementActionDiscard),
    ):
        _set_action(opts, attr, action)

    _set_bool(opts, "CameraSwitcherSettings", False)
    _set_bool(opts, "CurrentCameraSettings", False)
    _set_bool(opts, "GlobalLightingSettings", False)
    _set_bool(opts, "TransportSettings", False)

    # Select all takes in the file so we don't miss one.
    try:
        for i in range(opts.GetTakeCount()):
            opts.SetTakeSelect(i, True)
    except Exception:
        pass

    _apply_namespace_remap(opts, target_namespace, diagnostics=diagnostics)

    return opts


def _apply_namespace_remap(
    opts: FBFbxOptions,
    target_namespace: str,
    diagnostics: Optional[Dict] = None,
) -> None:
    """Tell MoBu to prefix every loaded object with ``target_namespace``.

    The correct MotionBuilder API is a single-line string assignment::

        opts.NamespaceList = "UE4"

    which makes ``FileMerge`` load every incoming object into the ``UE4:``
    namespace. With the setting file's source HumanIK character already
    sitting in ``UE4:Hips`` etc., this is exactly what ``kFBElementActionMerge``
    needs to bind by ``LongName`` (see ``FBFbxOptions.NamespaceList`` in the
    MotionBuilder Python SDK reference, and the ``ImportWithNamespace.py``
    sample script).

    The earlier implementation of this function tried pair-list assignments
    (``[(old_ns, new_ns), ...]``) and called methods (``SetNamespace``,
    ``AddNamespaceMatch``, ``AddNamespaceTransfer``) that do not exist on
    ``FBFbxOptions``. Both silently failed inside ``try/except``, so every
    FBX merged without a namespace prefix and produced the two symptoms
    that motivated this rewrite: a duplicated bone tree (LongName mismatch)
    and target bones reset to (0,0,0) when their short names collided with
    the FBX's bone short names.

    ``diagnostics`` (optional): populated with::

        {
            "namespace_remap_applied": bool,   # True iff assignment took
            "namespace_remap_error":   str,    # repr(exc) on failure
        }

    so the caller can surface success/failure in the per-import log.
    """
    if diagnostics is not None:
        diagnostics.setdefault("namespace_remap_applied", False)
        diagnostics.setdefault("namespace_remap_error", "")

    if not target_namespace:
        return

    try:
        opts.NamespaceList = target_namespace
    except Exception as exc:
        if diagnostics is not None:
            diagnostics["namespace_remap_applied"] = False
            diagnostics["namespace_remap_error"] = repr(exc)
        return

    if diagnostics is not None:
        diagnostics["namespace_remap_applied"] = True
        diagnostics["namespace_remap_error"] = ""


def _collect_fbx_namespaces(opts: FBFbxOptions) -> List[str]:
    """Best-effort listing of namespaces present inside the FBX about to merge.

    Kept for diagnostics only. If the FBX itself was authored with an
    internal namespace (e.g. ``OldNS:Hips``) and we then assign
    ``NamespaceList = "UE4"``, MotionBuilder *prefixes* (does not replace)
    so the bone arrives as ``UE4:OldNS:Hips`` and still fails to bind. The
    caller should log a WARN when this situation is detected so the
    operator can re-export the FBX without the internal namespace.
    """
    out: List[str] = []
    getter = getattr(opts, "GetNamespaceCount", None)
    name_getter = getattr(opts, "GetNamespaceName", None) or getattr(
        opts, "GetNamespace", None
    )
    if callable(getter) and callable(name_getter):
        try:
            for i in range(int(getter())):
                try:
                    out.append(str(name_getter(i)))
                except Exception:
                    continue
        except Exception:
            pass
    return out


def _set_action(opts: FBFbxOptions, name: str, action) -> None:
    if hasattr(opts, name):
        try:
            setattr(opts, name, action)
        except Exception:
            pass


def _set_bool(opts: FBFbxOptions, name: str, value: bool) -> None:
    if hasattr(opts, name):
        try:
            setattr(opts, name, value)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Post-merge duplicate cleanup
# ----------------------------------------------------------------------------
#
# Why this exists
# ---------------
# When ``FileMerge`` runs ``kFBElementActionMerge`` for Models, MotionBuilder
# binds incoming bones to existing scene models by **LongName** (full parent
# path). If the FBX's hierarchy differs from the scene's source rig -- even
# slightly, even when short names and namespaces match -- the merger gives up
# and appends ``" <N>"`` (single space + digit) to the new bone's short name
# to break the clash. The animation lands on those duplicates, so the source
# HumanIK character (still slotted on the originals) sees an empty rig at
# plot time.
#
# We can't fix the merge after the fact, but we can:
# 1. Detect the duplicates (trailing " <N>" suffix + a name that matches an
#    existing source-character bone).
# 2. Copy translation/rotation/scaling FCurves from each duplicate back onto
#    the matching original, for every take that was added by the import.
# 3. Delete the duplicate model and its descendants (which absorbs the "20
#    new bones that don't match" group too: ik_foot_l 1, ik_hand_l 1, etc.,
#    because they're parented under the duplicates).

_DUP_SUFFIX_RE = re.compile(r"^(.+?)(\s+\d+)$")


def _candidate_original_short(
    short_name: str,
    source_bone_set: set,
) -> Optional[str]:
    """Decide whether ``short_name`` should be cleaned up onto a source bone.

    Two acceptance rules, in order:

    1. ``" <N>"`` suffix stripping. MotionBuilder appends ``" 1"``, ``" 2"`` to
       break LongName clashes when an FBX bone's LongName matches an existing
       scene model but the parent chain differs. ``"ball_l 1"`` -> ``"ball_l"``.
    2. Direct match. The new bone's short name is literally one of the source
       character's bone short names. Happens when ``FBFbxOptions.NamespaceList``
       isolates the incoming bones into a freshly-incremented namespace (e.g.
       ``UE6:pelvis`` while the source rig owns ``UE5:pelvis``); the bone is
       *not* suffix-renamed because the LongName clash was resolved by the new
       namespace, but the short name still matches.

    Returns the source bone's short name to transfer onto, or ``None`` if
    neither rule matched.
    """
    m = _DUP_SUFFIX_RE.match(short_name)
    if m and m.group(1) in source_bone_set:
        return m.group(1)
    if short_name in source_bone_set:
        return short_name
    return None


def _detect_namespace_increment(
    new_models: List[str],
    target_namespace: str,
    source_bones: List[str],
    source_bone_long_names: List[str],
) -> str:
    """Detect when MotionBuilder isolated incoming bones into a NEW namespace.

    ``FBFbxOptions.NamespaceList = "UE5"`` does NOT merge incoming objects
    into the existing ``UE5:`` namespace if one already exists in the
    scene; MotionBuilder instead increments to ``UE6:``, ``UE7:``, ... to
    keep the new objects isolated. This function spots that case by:

    * Looking at every newly-created model (post-merge ``new_models``).
    * Skipping source-character bones (would have arrived via re-emit, not
      a real increment).
    * Bucketing by innermost namespace prefix.
    * Counting only buckets whose member bones' short names match a
      source-character bone (so we're confident this prefix carries the
      incoming source FBX, not unrelated helper objects).
    * Returning whichever non-``target_namespace`` prefix has the highest
      hit count, or ``""`` if the merge bound cleanly into
      ``target_namespace`` (which is the happy path when the source
      namespace did not yet exist in scene).

    Returned value is consumed by :func:`_log_import_diagnostics` to emit
    a "namespace was incremented to 'UE6'; cleanup will reconcile" INFO
    line, and by the cleanup helpers to recognise the increment case as
    expected behaviour rather than a hard failure.
    """
    if not target_namespace or not new_models:
        return ""
    source_bone_set = set(source_bones)
    source_long_set = set(source_bone_long_names)
    counts: Dict[str, int] = {}
    for long_name in new_models:
        if long_name in source_long_set:
            continue
        if ":" not in long_name:
            continue
        prefix_full = long_name.rsplit(":", 1)[0]
        prefix = prefix_full.split(":")[-1] if ":" in prefix_full else prefix_full
        if prefix == target_namespace:
            continue
        short = long_name.rsplit(":", 1)[-1]
        if short in source_bone_set:
            counts[prefix] = counts.get(prefix, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _summarize_long_name_namespaces(long_names: List[str]) -> Dict[str, int]:
    """Bucket ``long_names`` by their innermost namespace prefix.

    Mirrors the pipeline-side ``_summarize_namespace_prefixes`` so the cleanup
    log can say "transferred from {UE6: 88, UE7: 88} into source rig" in the
    same format the import diagnostics already use. A bone with no colon
    falls under ``(no namespace)``.
    """
    counts: Dict[str, int] = {}
    for long_name in long_names:
        if ":" in long_name:
            prefix = long_name.rsplit(":", 1)[0]
            if ":" in prefix:
                prefix = prefix.split(":")[-1]
        else:
            prefix = "(no namespace)"
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def cleanup_duplicate_bones(
    diagnostics: Dict,
    source_character_name: str = "",
    take_names: Optional[List[str]] = None,
    logger=None,
) -> Dict[str, int]:
    """Transfer animation off ``" <N>"``-renamed duplicates back to source bones.

    ``diagnostics`` must be the dict populated by :func:`import_animation_only`.
    Two source bone sets are consulted, in priority order:

    1. ``source_namespace_bones`` (preferred): every model under the source
       rig's namespace, including non-HIK helper bones. This is what lets us
       reconcile incremented-namespace duplicates of ``ik_hand_root`` /
       ``ik_foot_root`` / ``interaction`` / ``center_of_mass`` / ``root``
       back onto the original ``UE5:`` rig. Without this set the cleanup
       would silently leave those helper bones behind because the HumanIK
       character does not bind them to slots.
    2. ``source_bones`` (fallback): the narrower "bones bound to a HumanIK
       slot" list, used only when the source rig has no namespace and the
       namespace walk therefore returns nothing.

    ``take_names`` limits which takes are processed; defaults to every take
    currently in the scene. In typical use the caller passes only the takes
    that the latest import created, so previously-imported takes are left
    alone.

    Returns ``{"transferred": int, "deleted": int, "skipped": int}``.
    """
    result = {"transferred": 0, "deleted": 0, "skipped": 0}

    new_models = list(diagnostics.get("new_models") or [])
    namespace_bones = list(diagnostics.get("source_namespace_bones") or [])
    namespace_long_names = list(
        diagnostics.get("source_namespace_bone_long_names") or []
    )
    slot_bones = list(diagnostics.get("source_bones") or [])
    slot_long_names = list(diagnostics.get("source_bone_long_names") or [])

    # Prefer the namespace-wide set so helper bones (ik_hand_root etc.)
    # are matched; fall back to the HIK-slot set when the source rig
    # has no namespace at all (namespace walk returns empty).
    if namespace_bones:
        source_bone_set = set(namespace_bones)
        source_long_name_set = set(namespace_long_names)
    else:
        source_bone_set = set(slot_bones)
        source_long_name_set = set(slot_long_names)

    if not new_models or not source_bone_set:
        return result

    candidate_pairs: List[Tuple[str, str]] = []
    for long_name in new_models:
        # Never treat the source rig's own bones as duplicates;
        # transferring a bone's animation onto itself and then deleting
        # the bone would destroy the source rig.
        if long_name in source_long_name_set:
            continue
        short = long_name.rsplit(":", 1)[-1]
        original_short = _candidate_original_short(short, source_bone_set)
        if original_short is None:
            continue
        candidate_pairs.append((long_name, original_short))

    if not candidate_pairs:
        if logger is not None:
            logger.info("  cleanup: no duplicates to clean.")
        return result

    # Build the source-side transfer target map. Use the namespace-wide
    # model walk when we have a namespace, so helper bones are reachable;
    # otherwise fall back to the HIK character slot models.
    source_char = (
        find_character_by_name(source_character_name) if source_character_name else None
    )
    target_namespace = diagnostics.get("namespace_target") or ""
    src_models: List = []
    if target_namespace:
        src_models = collect_scene_models_in_namespace(target_namespace)
    if not src_models and source_char is not None:
        src_models = get_target_skeleton_models(source_char)

    src_bone_map: Dict[str, object] = {}
    for m in src_models:
        name = getattr(m, "Name", "") or ""
        if name and name not in src_bone_map:
            src_bone_map[name] = m

    system = FBSystem()
    if take_names is None:
        takes_to_process = list(system.Scene.Takes)
    else:
        wanted = set(take_names)
        takes_to_process = [t for t in system.Scene.Takes if t.Name in wanted]

    if not takes_to_process:
        if logger is not None:
            logger.warn("  cleanup: no takes resolved; nothing to transfer.")
        return result

    duplicates_to_delete: List[object] = []
    prev_take = system.CurrentTake

    # We dump verbose anim-state diagnostics for the FIRST resolved pair only;
    # otherwise the log explodes (88 bones * 3 props * N takes). Once that one
    # dump is in the log the user (or us) can see why transfer is returning
    # nothing.
    verbose_dumped = False

    try:
        for dup_long_name, original_short in candidate_pairs:
            dup_model = _find_model_by_long_name(dup_long_name)
            original_model = src_bone_map.get(original_short)

            if dup_model is None or original_model is None:
                result["skipped"] += 1
                continue

            is_first_pair = not verbose_dumped
            if is_first_pair and logger is not None:
                verbose_dumped = True
                _diagnose_anim_state(dup_model, original_model, takes_to_process, logger)

            transferred_any_take = False
            for tk_idx, take in enumerate(takes_to_process):
                system.CurrentTake = take
                # Verbose-log the first pair's first take only; otherwise log
                # volume blows up to 88 * num_takes entries per import.
                v_logger = logger if (is_first_pair and tk_idx == 0) else None
                try:
                    if _transfer_anim_curves(
                        dup_model, original_model, verbose_logger=v_logger
                    ):
                        transferred_any_take = True
                except Exception as exc:
                    if logger is not None:
                        logger.warn(
                            f"  cleanup: transfer failed on '{dup_long_name}' "
                            f"take '{take.Name}': {exc!r}"
                        )

            if transferred_any_take:
                result["transferred"] += 1
            duplicates_to_delete.append(dup_model)
    finally:
        if prev_take is not None:
            try:
                system.CurrentTake = prev_take
            except Exception:
                pass

    for dup_model in duplicates_to_delete:
        try:
            result["deleted"] += _delete_model_subtree(dup_model)
        except Exception as exc:
            if logger is not None:
                logger.warn(f"  cleanup: delete failed: {exc!r}")

    if logger is not None:
        logger.info(
            "  cleanup: transferred animation on "
            f"{result['transferred']} bone(s), deleted {result['deleted']} "
            f"model(s), skipped {result['skipped']}."
        )
        if result["transferred"] > 0 or result["deleted"] > 0:
            transferred_long_names = [pair[0] for pair in candidate_pairs]
            ns_counts = _summarize_long_name_namespaces(transferred_long_names)
            ns_line = ", ".join(
                f"{k}: {v}"
                for k, v in sorted(ns_counts.items(), key=lambda kv: -kv[1])
            )
            logger.info(
                f"  cleanup: transferred from {{{ns_line}}} into source rig."
            )
    return result


def delete_duplicate_bone_models(
    diagnostics: Dict,
    logger=None,
) -> Dict[str, int]:
    """Delete duplicate models from a merge that did not create usable takes.

    Some FBX files merge a skeleton into the scene but do not add a take. In
    that case there is no imported take to transfer animation from, but leaving
    the duplicate skeleton in the scene can destabilize the next HIK plot.

    Mirrors :func:`cleanup_duplicate_bones` matching: prefer the
    namespace-wide bone set so non-HIK helper bones (``ik_hand_root``,
    ``ik_foot_root``, ``interaction``, ``center_of_mass``, ``root``) are
    still considered duplicates of their incremented-namespace twins. Fall
    back to the HIK slot set when the source rig has no namespace.
    """
    result = {"deleted": 0, "skipped": 0}
    new_models = list(diagnostics.get("new_models") or [])
    namespace_bones = list(diagnostics.get("source_namespace_bones") or [])
    namespace_long_names = list(
        diagnostics.get("source_namespace_bone_long_names") or []
    )
    slot_bones = list(diagnostics.get("source_bones") or [])
    slot_long_names = list(diagnostics.get("source_bone_long_names") or [])

    if namespace_bones:
        source_bone_set = set(namespace_bones)
        source_long_name_set = set(namespace_long_names)
    else:
        source_bone_set = set(slot_bones)
        source_long_name_set = set(slot_long_names)

    if not new_models or not source_bone_set:
        return result

    duplicates_to_delete: List[object] = []
    for long_name in new_models:
        # Never delete a source-rig bone (LongName-exact match means the
        # new_models snapshot picked up a re-emitted source rig bone
        # rather than a true duplicate).
        if long_name in source_long_name_set:
            continue
        short = long_name.rsplit(":", 1)[-1]
        if _candidate_original_short(short, source_bone_set) is None:
            continue
        dup_model = _find_model_by_long_name(long_name)
        if dup_model is None:
            result["skipped"] += 1
            continue
        duplicates_to_delete.append(dup_model)

    seen = set()
    for dup_model in duplicates_to_delete:
        key = id(dup_model)
        if key in seen:
            continue
        seen.add(key)
        try:
            result["deleted"] += _delete_model_subtree(dup_model)
        except Exception as exc:
            result["skipped"] += 1
            if logger is not None:
                logger.warn(f"  cleanup: orphan delete failed: {exc!r}")

    if logger is not None:
        logger.info(
            "  cleanup: deleted "
            f"{result['deleted']} orphan duplicate model(s), "
            f"skipped {result['skipped']}."
        )
    return result


def _find_model_by_long_name(long_name: str):
    """Walk the scene tree once looking for a model with this exact LongName."""
    root = FBSystem().Scene.RootModel
    if root is None:
        return None
    return _walk_find_long_name(root, long_name)


def _walk_find_long_name(node, target: str):
    try:
        children = list(node.Children)
    except Exception:
        return None
    for c in children:
        try:
            if c.LongName == target:
                return c
        except Exception:
            pass
        found = _walk_find_long_name(c, target)
        if found is not None:
            return found
    return None


def _fcurve_key_count(fc) -> int:
    """Return key count from an ``FBFCurve`` across MoBu binding variants.

    Different MoBu / pyfbsdk releases expose the underlying ``Keys``
    collection differently:

    * Older versions: ``fc.Keys.GetCount()`` (C++-style)
    * Most versions:  ``len(fc.Keys)``
    * Some bindings:  ``fc.KeyGetCount()`` flat method on FCurve

    We probe each until one returns a number. Returns ``-1`` if every probe
    failed, which the caller treats as "skip this curve".
    """
    if fc is None:
        return -1
    try:
        return int(fc.Keys.GetCount())
    except Exception:
        pass
    try:
        return int(len(fc.Keys))
    except Exception:
        pass
    try:
        return int(fc.KeyGetCount())
    except Exception:
        pass
    return -1


def _fcurve_get_key(fc, idx):
    """Index into an ``FBFCurve`` portably across MoBu binding variants."""
    if fc is None:
        return None
    try:
        return fc.Keys[idx]
    except Exception:
        pass
    try:
        return fc.KeyGet(idx)
    except Exception:
        pass
    return None


def _diagnose_anim_state(dup_model, original_model, takes, logger) -> None:
    """Dump T/R/S animation state of one duplicate/original pair to the log.

    Helps us see *why* a transfer ends up copying zero keys. We only run this
    against the first pair so the log stays readable; once it tells us which
    layer is empty (prop missing, IsAnimated False, anim node None, sub-nodes
    empty, or FCurve key count zero) we know whether the issue is on the
    MoBu API surface (version difference) or on the actual data (e.g. keys
    live on Lcl* aliases instead of Translation/Rotation/Scaling, or driven
    by a constraint).
    """
    logger.info(f"  cleanup verbose: src='{dup_model.LongName}' dst='{original_model.LongName}'")
    system = FBSystem()

    inspect_takes = takes[:1] if takes else []
    for take in inspect_takes:
        try:
            system.CurrentTake = take
        except Exception:
            pass
        take_label = getattr(take, "Name", "?")
        logger.info(f"    take='{take_label}':")
        for prop_name in ("Translation", "Rotation", "Scaling"):
            for label, model in (("src", dup_model), ("dst", original_model)):
                prop = getattr(model, prop_name, None)
                if prop is None:
                    logger.info(f"      {label}.{prop_name}: <missing>")
                    continue
                try:
                    is_anim = bool(prop.IsAnimated())
                except Exception as exc:
                    logger.info(f"      {label}.{prop_name}: IsAnimated() raised {exc!r}")
                    continue
                if not is_anim:
                    logger.info(f"      {label}.{prop_name}: not animated")
                    continue
                node = prop.GetAnimationNode()
                if node is None:
                    logger.info(f"      {label}.{prop_name}: anim node is None")
                    continue
                try:
                    subs = list(node.Nodes)
                except Exception as exc:
                    logger.info(f"      {label}.{prop_name}: node.Nodes raised {exc!r}")
                    continue
                key_counts: List[str] = []
                for sub in subs:
                    fc = getattr(sub, "FCurve", None)
                    if fc is None:
                        key_counts.append("noFC")
                        continue
                    count = _fcurve_key_count(fc)
                    key_counts.append("?" if count < 0 else str(count))
                logger.info(
                    f"      {label}.{prop_name}: animated, "
                    f"{len(subs)} sub-nodes, keys={key_counts}"
                )


def _transfer_anim_curves(src_model, dst_model, verbose_logger=None) -> bool:
    """Copy T/R/S FCurves from ``src_model`` to ``dst_model`` in the current take.

    Only Time/Value pairs are copied. Tangent metadata (mode, derivatives) is
    intentionally NOT copied so we don't have to translate between MoBu API
    surfaces that differ across versions; the plot step downstream will
    rebuild interpolation curves anyway.

    ``verbose_logger`` (optional): if supplied, every step that aborts the
    copy emits a log line so we can see exactly which API surface failed
    instead of swallowing the exception in a bare ``except``.

    Returns True iff at least one curve had keys copied.
    """
    def _v(msg: str) -> None:
        if verbose_logger is not None:
            verbose_logger.info(f"      transfer: {msg}")

    any_copied = False
    for prop_name in ("Translation", "Rotation", "Scaling"):
        src_prop = getattr(src_model, prop_name, None)
        dst_prop = getattr(dst_model, prop_name, None)
        if src_prop is None or dst_prop is None:
            _v(f"{prop_name}: prop missing (src={src_prop is not None}, dst={dst_prop is not None})")
            continue
        try:
            if not src_prop.IsAnimated():
                _v(f"{prop_name}: src not animated")
                continue
        except Exception as exc:
            _v(f"{prop_name}: src.IsAnimated raised {exc!r}")
            continue
        try:
            if not dst_prop.IsAnimated():
                dst_prop.SetAnimated(True)
        except Exception as exc:
            _v(f"{prop_name}: dst.SetAnimated raised {exc!r}")
            continue

        src_node = src_prop.GetAnimationNode()
        dst_node = dst_prop.GetAnimationNode()
        if src_node is None or dst_node is None:
            _v(f"{prop_name}: anim node None (src={src_node is not None}, dst={dst_node is not None})")
            continue
        try:
            src_subs = list(src_node.Nodes)
            dst_subs = list(dst_node.Nodes)
        except Exception as exc:
            _v(f"{prop_name}: node.Nodes raised {exc!r}")
            continue

        # XYZ channels live as child animation nodes (3 each for T/R/S).
        for i in range(min(len(src_subs), len(dst_subs))):
            src_fc = getattr(src_subs[i], "FCurve", None)
            dst_fc = getattr(dst_subs[i], "FCurve", None)
            if src_fc is None or dst_fc is None:
                _v(f"{prop_name}[{i}]: FCurve attr missing (src={src_fc is not None}, dst={dst_fc is not None})")
                continue
            key_count = _fcurve_key_count(src_fc)
            if key_count < 0:
                _v(f"{prop_name}[{i}]: src key_count probe failed")
                continue
            if key_count == 0:
                _v(f"{prop_name}[{i}]: src has 0 keys")
                continue
            try:
                dst_fc.EditClear()
            except Exception:
                try:
                    dst_fc.KeyClear()
                except Exception:
                    pass
            copied_this_curve = 0
            for k_idx in range(key_count):
                src_key = _fcurve_get_key(src_fc, k_idx)
                if src_key is None:
                    continue
                try:
                    dst_fc.KeyAdd(src_key.Time, float(src_key.Value))
                    copied_this_curve += 1
                    any_copied = True
                except Exception as exc:
                    if k_idx == 0:
                        _v(f"{prop_name}[{i}]: KeyAdd raised {exc!r}")
                    continue
            if copied_this_curve > 0:
                _v(f"{prop_name}[{i}]: copied {copied_this_curve}/{key_count} keys")
    return any_copied


def _delete_model_subtree(model) -> int:
    """Recursively destroy ``model`` and every descendant. Returns count deleted."""
    count = 0
    try:
        children = list(model.Children)
    except Exception:
        children = []
    for c in children:
        count += _delete_model_subtree(c)
    try:
        model.FBDelete()
        count += 1
    except Exception:
        pass
    return count


# ----------------------------------------------------------------------------
# Metadata injection
# ----------------------------------------------------------------------------


def inject_metadata(host_model, metadata: ExportMetadata) -> bool:
    """Attach metadata as a single JSON custom property on ``host_model``.

    Custom string properties on bones survive FBX round-trip: Maya reads them
    as ``extraAttr``, Max as ``UserProperties``, Unreal as asset metadata on
    the imported Skeletal Mesh / AnimSequence.

    Returns True if the property was set successfully.
    """
    if host_model is None:
        return False
    prop = host_model.PropertyList.Find(_METADATA_PROPERTY_NAME)
    if prop is None:
        try:
            prop = host_model.PropertyCreate(
                _METADATA_PROPERTY_NAME,
                FBPropertyType.kFBPT_charptr,
                "",
                False,
                True,
                None,
            )
        except Exception:
            return False
    try:
        prop.Data = metadata.to_json()
        return True
    except Exception:
        return False


def read_metadata(host_model) -> Optional[Dict]:
    """Inverse of :func:`inject_metadata` for round-trip verification."""
    if host_model is None:
        return None
    prop = host_model.PropertyList.Find(_METADATA_PROPERTY_NAME)
    if prop is None:
        return None
    try:
        raw = str(prop.Data)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


# ----------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------


@dataclass
class ExportConfig:
    fbx_version: str = "FBX201800"  # informational; MoBu uses scene default
    ascii: bool = False
    embed_media: bool = False
    save_character: bool = False  # the character def lives in the setting file
    save_control_set: bool = False
    save_character_extension: bool = False
    # Strip every ``<ns>:`` namespace prefix from the exported skeleton's
    # bone names so the output FBX lands with clean ``pelvis`` / ``hand_l``
    # short names instead of ``Camp4:pelvis``. The namespaces are
    # re-attached in a ``finally`` after ``FileSave`` so the in-scene rig
    # is left exactly as before. Turn off only if a downstream consumer
    # depends on the source-scene namespace being baked into the FBX.
    strip_namespace_on_export: bool = True


def export_take_to_fbx(
    take_name: str,
    target_character,
    out_dir: str,
    config: Optional[ExportConfig] = None,
    metadata: Optional[ExportMetadata] = None,
    filename_override: Optional[str] = None,
    logger=None,
) -> str:
    """Save ``take_name`` to ``<out_dir>/<take_name>.fbx``.

    Only the target character's skeleton bones are selected; the source rig,
    helper geometry, lights, etc. are excluded so the output FBX is small and
    targeted at downstream DCC import. To preserve hierarchy, every ancestor
    of an HIK-slotted bone is also pulled into the selection (otherwise a
    non-HIK intermediate bone like a Biped ``pelvis`` would drop out and its
    children would be re-parented to the scene root on save).

    Returns the absolute path that was written.

    Raises ``IOError`` if MotionBuilder reports a save failure.
    """
    if config is None:
        config = ExportConfig()
    take = get_take_by_name(take_name)
    if take is None:
        raise IOError(f"Take '{take_name}' not found in scene.")

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    filename = filename_override or f"{take_name}.fbx"
    if not filename.lower().endswith(".fbx"):
        filename += ".fbx"
    out_path = os.path.abspath(os.path.join(out_dir, filename))

    system = FBSystem()
    prev_take = system.CurrentTake
    system.CurrentTake = take

    skeleton = get_target_skeleton_models(target_character)
    prev_selection_snapshot = _snapshot_selection()
    _clear_selection()
    _select_models(skeleton)
    added_ancestors = _select_ancestors(skeleton)
    added_offset_models = _select_offset_root_subtrees(skeleton)
    if logger is not None:
        logger.info(
            f"  export: {len(skeleton)} HIK-slotted bone(s) selected, "
            f"+{len(added_ancestors)} ancestor(s) added to preserve hierarchy, "
            f"+{len(added_offset_models)} root_Offset subtree model(s)."
        )

    if metadata is not None and skeleton:
        inject_metadata(skeleton[0], metadata)

    namespace_snapshot: List[Tuple] = []
    try:
        if getattr(config, "strip_namespace_on_export", True):
            # Strip namespaces only from the models that will actually be
            # written. Recollect from the live selection so ancestors and
            # root_Offset subtree models (added above) are covered too.
            export_models = _collect_selected_models()
            namespace_snapshot = _strip_namespace_for_export(export_models)
            if logger is not None:
                logger.info(
                    f"  export: stripped namespaces from "
                    f"{len(namespace_snapshot)} selected model(s); "
                    "originals restored after save."
                )

        opts = _build_export_options(take_name, config)
        success = FBApplication().FileSave(out_path, opts)
        if not success:
            raise IOError(f"FileSave returned False for {out_path}")
        return out_path
    finally:
        # Restore namespaces BEFORE clearing/restoring selection so a
        # failure in selection restore can't leave the rig stripped.
        if namespace_snapshot:
            try:
                _restore_namespace_after_export(namespace_snapshot)
            except Exception as exc:
                if logger is not None:
                    logger.warn(
                        f"  export: namespace restore failed: {exc!r} "
                        "(rig short names may be left without namespace prefix)"
                    )
        system.CurrentTake = prev_take
        _clear_selection()
        _restore_selection(prev_selection_snapshot)


def _build_export_options(take_name: str, config: ExportConfig) -> FBFbxOptions:
    opts = FBFbxOptions(False)
    _set_bool(opts, "SaveSelectedModelsOnly", True)
    _set_bool(opts, "SaveCharacter", bool(config.save_character))
    _set_bool(opts, "SaveControlSet", bool(config.save_control_set))
    _set_bool(opts, "SaveCharacterExtension", bool(config.save_character_extension))
    _set_bool(opts, "ShowFileDialog", False)
    _set_bool(opts, "ShowOptionsDialog", False)
    _set_bool(opts, "EmbedMedia", bool(config.embed_media))
    _set_bool(opts, "UseASCIIFormat", bool(config.ascii))

    # Only the target take should be in the output.
    try:
        for i in range(opts.GetTakeCount()):
            opts.SetTakeSelect(i, opts.GetTakeName(i) == take_name)
    except Exception:
        pass

    return opts


def _select_models(models: Iterable) -> None:
    for m in models:
        try:
            m.Selected = True
        except Exception:
            continue


def _select_ancestors(models: Iterable) -> List:
    """Walk each model's parent chain and select every ancestor along the way.

    Why this exists
    ---------------
    ``export_take_to_fbx`` uses ``SaveSelectedModelsOnly=True``, which only
    serialises the bones that are currently selected. ``get_target_skeleton_models``
    returns just the bones that are wired into HumanIK slots (Hips, Spine,
    LeftUpLeg, ...). If a non-HIK intermediate bone sits between two HIK
    bones in the skeleton tree -- e.g. a Biped ``pelvis`` that isn't slotted
    on the character -- it gets dropped from the export and its children
    lose their parent connection. MoBu then re-parents the orphans to the
    scene root on save, producing the flattened ``Bip_001 / Spine /
    L Thigh / R Thigh`` hierarchy the user observed.

    Selecting every ancestor up to (but not including) the scene root
    preserves the full chain. Returns the list of ancestor models that were
    actually selected (for diagnostics / logging).
    """
    try:
        scene_root = FBSystem().Scene.RootModel
    except Exception:
        scene_root = None

    seen = set()
    added: List = []
    for m in models:
        cur = getattr(m, "Parent", None)
        while cur is not None and cur is not scene_root:
            key = id(cur)
            if key in seen:
                break
            seen.add(key)
            try:
                if not cur.Selected:
                    cur.Selected = True
                    added.append(cur)
            except Exception:
                pass
            cur = getattr(cur, "Parent", None)
    return added


def _select_offset_root_subtrees(skeleton: Iterable) -> List:
    """Select any ``root_Offset`` subtree that owns the target skeleton.

    A manually added parent offset is a common way to correct axis orientation
    without touching the characterized bones. HIK slots do not know about that
    helper, so explicitly include its whole subtree when it is an ancestor of
    the target skeleton; otherwise the exported FBX can lose the correction.
    """
    skeleton_list = [m for m in skeleton if m is not None]
    if not skeleton_list:
        return []

    added: List = []
    for candidate in _find_models_by_short_names(_EXPORT_OFFSET_ROOT_NAMES):
        if not _model_contains_any(candidate, skeleton_list):
            continue
        _select_subtree(candidate, added)
    return added


def _find_models_by_short_names(names: Iterable[str]) -> List:
    wanted = {n.lower() for n in names}
    root = FBSystem().Scene.RootModel
    if root is None:
        return []
    out: List = []
    _walk_collect_by_short_name(root, wanted, out)
    return out


def _walk_collect_by_short_name(node, names: set, out: List) -> None:
    try:
        children = list(node.Children)
    except Exception:
        return
    for child in children:
        short = str(getattr(child, "Name", "") or "").lower()
        if short in names:
            out.append(child)
        _walk_collect_by_short_name(child, names, out)


def _model_contains_any(root_model, targets: Iterable) -> bool:
    target_ids = {id(m) for m in targets}
    return _walk_contains_any(root_model, target_ids)


def _walk_contains_any(node, target_ids: set) -> bool:
    if id(node) in target_ids:
        return True
    try:
        children = list(node.Children)
    except Exception:
        return False
    for child in children:
        if _walk_contains_any(child, target_ids):
            return True
    return False


def _select_subtree(root_model, added: List) -> None:
    try:
        if not root_model.Selected:
            root_model.Selected = True
            added.append(root_model)
    except Exception:
        pass
    try:
        children = list(root_model.Children)
    except Exception:
        return
    for child in children:
        _select_subtree(child, added)


def _collect_selected_models() -> List:
    """Return every model that is currently flagged ``Selected`` in scene."""
    model_list = FBModelList()
    try:
        from pyfbsdk import FBGetSelectedModels  # type: ignore

        FBGetSelectedModels(model_list, None, True, False)
    except Exception:
        return []
    return [m for m in model_list]


def _strip_namespace_for_export(models: Iterable) -> List[Tuple]:
    """Detach every namespace prefix from each model in ``models``.

    Why model-by-model
    ------------------
    A scene-wide ``FBSystem().Scene.NamespaceImportRename(ns, "", True)``
    would also touch the source rig (and anything else sitting under the
    same namespace), risking collateral damage and a renamed-back drift
    when the restore step runs. ``ProcessObjectNamespace`` operates on a
    single model so we never reach beyond the export selection.

    Nested namespaces (``A:B:bone``) get stripped innermost-first in a
    loop because each ``kFBRemoveAllNamespace`` call only peels one level.

    Returns a snapshot list of ``(model, [ns_inner, ns_outer, ...])`` so
    :func:`_restore_namespace_after_export` can re-attach them in the
    correct order. The boost.python ``None`` fallback (two-arg call) is
    tried whenever the four-arg form rejects the trailing ``None``s,
    mirroring the same defensive pattern used by other MoBu API wrappers
    in this module.
    """
    snapshot: List[Tuple] = []
    for model in models:
        try:
            long_name = model.LongName or ""
        except Exception:
            continue
        if ":" not in long_name:
            continue
        applied: List[str] = []
        # Cap the loop just in case ``ProcessObjectNamespace`` ever
        # returns silently without actually removing the namespace;
        # otherwise a misbehaving SDK build could spin forever.
        for _ in range(16):
            try:
                current = model.LongName or ""
            except Exception:
                break
            if ":" not in current:
                break
            ns_chain = current.rsplit(":", 1)[0]
            ns = ns_chain.split(":")[-1] if ":" in ns_chain else ns_chain
            if not ns:
                break
            if not _process_object_namespace(
                model, FBNamespaceAction.kFBRemoveAllNamespace, ns
            ):
                break
            try:
                after = model.LongName or ""
            except Exception:
                after = ""
            if after == current:
                break
            applied.append(ns)
        if applied:
            snapshot.append((model, applied))
    return snapshot


def _restore_namespace_after_export(snapshot: List[Tuple]) -> None:
    """Re-attach namespaces removed by :func:`_strip_namespace_for_export`.

    Walks ``applied`` in reverse so the innermost namespace (the one
    stripped first) is concatenated last, leaving the model with the
    same ``A:B:bone`` ordering it had before export.
    """
    for model, namespaces in snapshot:
        for ns in reversed(namespaces):
            _process_object_namespace(
                model, FBNamespaceAction.kFBConcatNamespace, ns
            )


def _process_object_namespace(model, action, namespace: str) -> bool:
    """Call ``ProcessObjectNamespace`` resiliently across MoBu Python builds.

    Some boost.python wrappers reject trailing ``None`` arguments with
    ``ArgumentError: None.None(...)``; in that case the two-arg call
    still works and is the documented overload for the actions we use
    (``kFBRemoveAllNamespace`` / ``kFBConcatNamespace`` only need the
    action and the namespace name). Returns True on success.
    """
    try:
        model.ProcessObjectNamespace(action, namespace, None, None)
        return True
    except Exception:
        pass
    try:
        model.ProcessObjectNamespace(action, namespace)
        return True
    except Exception:
        return False


def _snapshot_selection() -> List:
    model_list = FBModelList()
    try:
        from pyfbsdk import FBGetSelectedModels  # type: ignore

        FBGetSelectedModels(model_list, None, True, False)
    except Exception:
        return []
    return [m for m in model_list]


def _restore_selection(models: Iterable) -> None:
    for m in models:
        try:
            m.Selected = True
        except Exception:
            continue


def _clear_selection() -> None:
    try:
        from pyfbsdk import FBGetSelectedModels  # type: ignore

        ml = FBModelList()
        FBGetSelectedModels(ml, None, True, False)
        for m in ml:
            try:
                m.Selected = False
            except Exception:
                pass
    except Exception:
        pass
