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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from pyfbsdk import (  # type: ignore
    FBApplication,
    FBElementAction,
    FBFbxOptions,
    FBModelList,
    FBPropertyType,
    FBSystem,
    FBTake,
)

from .scene_utils import (
    collect_scene_bone_names,
    find_character_by_name,
    get_character_namespace,
    get_target_skeleton_models,
)
from .take_manager import all_take_names, get_take_by_name, takes_added_since


_METADATA_PROPERTY_NAME = "RetargetInfo"


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


def import_animation_only(
    fbx_path: str,
    source_character_name: str = "",
    diagnostics: Optional[Dict] = None,
) -> List[FBTake]:
    """Merge ``fbx_path`` bringing animation only, return new takes.

    The existing source rig in the scene receives the keys via name-based
    binding. Geometry, materials, lights, cameras and characters in the source
    file are discarded.

    ``source_character_name`` is used to discover the source rig's namespace
    so incoming bones can be remapped into it (otherwise ``Hips`` in the FBX
    would not bind to ``SrcRig:Hips`` in the scene and MotionBuilder would
    create a new bone tree).

    ``diagnostics``, when provided, is populated with merge bookkeeping:
        - ``new_models``: names of FBModels that did not exist before merge
        - ``namespace_target``: namespace the merger was asked to remap into
        - ``source_bones``: bone short names belonging to the source character

    Raises ``IOError`` if MotionBuilder reports that the merge failed.
    """
    if not os.path.isfile(fbx_path):
        raise IOError(f"FBX not found: {fbx_path}")

    source_char = (
        find_character_by_name(source_character_name) if source_character_name else None
    )
    target_namespace = get_character_namespace(source_char) if source_char else ""
    source_bones = collect_scene_bone_names(source_char) if source_char else []

    pre_models = _snapshot_scene_model_names()
    snapshot = all_take_names()
    app = FBApplication()
    opts = _build_import_options(fbx_path, target_namespace=target_namespace)
    success = app.FileMerge(fbx_path, False, opts)
    if not success:
        raise IOError(f"FileMerge returned False for {fbx_path}")

    if diagnostics is not None:
        post_models = _snapshot_scene_model_names()
        new_models = sorted(post_models - pre_models)
        diagnostics["new_models"] = new_models
        diagnostics["namespace_target"] = target_namespace
        diagnostics["source_bones"] = source_bones

    return takes_added_since(snapshot)


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
    fbx_path: str, target_namespace: str = ""
) -> FBFbxOptions:
    """Configure FBFbxOptions for an animation-only merge.

    ``target_namespace`` (if non-empty) is used to remap every namespace that
    exists inside the FBX so its bones merge into the in-scene source rig.
    Without this, bones like ``Hips`` in the FBX cannot bind to ``SrcRig:Hips``
    in the scene and MotionBuilder creates a parallel skeleton.
    """
    opts = FBFbxOptions(True, fbx_path)

    # Disable every category first; selectively re-enable what we need below.
    try:
        opts.SettingsByDefault(False)
    except AttributeError:
        pass

    # Bring in animation curves and let them bind to existing bones by name.
    _set_action(opts, "Models", FBElementAction.kFBElementActionMerge)
    _set_bool(opts, "ModelsAnimation", True)
    _set_bool(opts, "BaseModelsAnimation", True)
    _set_bool(opts, "Animation", True)

    # Everything else stays out of the setting file.
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

    _apply_namespace_remap(opts, target_namespace)

    return opts


def _apply_namespace_remap(opts: FBFbxOptions, target_namespace: str) -> None:
    """Tell MoBu to remap incoming namespaces so bones bind to the scene rig.

    The FBX may carry no namespace, a different one, or even multiple. We map
    every namespace found in the file to ``target_namespace`` (and also map
    ``""`` -> target so root-level bones get the same prefix) which lets
    ``kFBElementActionMerge`` actually find existing models by LongName.

    The exact API name and shape of the namespace remap on ``FBFbxOptions``
    varies across MotionBuilder versions, so we try a handful of well-known
    spellings and silently skip if none is supported. Callers should still
    log via ``diagnostics`` so users can see when remap had no effect.
    """
    if not target_namespace:
        return

    namespaces = _collect_fbx_namespaces(opts)
    pairs = []
    seen = set()
    for ns in list(namespaces) + [""]:
        key = (ns, target_namespace)
        if key in seen or ns == target_namespace:
            continue
        seen.add(key)
        pairs.append(key)

    if not pairs:
        return

    if _try_namespace_list(opts, pairs):
        return
    _try_namespace_method(opts, pairs)


def _collect_fbx_namespaces(opts: FBFbxOptions) -> List[str]:
    """Best-effort listing of namespaces present inside the FBX about to merge."""
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


def _try_namespace_list(opts: FBFbxOptions, pairs: List) -> bool:
    """Try the ``opts.NamespaceList`` property style (newer MoBu)."""
    if not hasattr(opts, "NamespaceList"):
        return False
    try:
        opts.NamespaceList = [list(p) for p in pairs]
        return True
    except Exception:
        try:
            opts.NamespaceList = pairs
            return True
        except Exception:
            return False


def _try_namespace_method(opts: FBFbxOptions, pairs: List) -> bool:
    """Try call-style namespace remap helpers."""
    for method_name in (
        "SetNamespace",
        "AddNamespaceMatch",
        "AddNamespaceTransfer",
    ):
        fn = getattr(opts, method_name, None)
        if not callable(fn):
            continue
        ok_any = False
        for old, new in pairs:
            try:
                fn(old, new)
                ok_any = True
            except Exception:
                continue
        if ok_any:
            return True
    return False


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


def export_take_to_fbx(
    take_name: str,
    target_character,
    out_dir: str,
    config: Optional[ExportConfig] = None,
    metadata: Optional[ExportMetadata] = None,
    filename_override: Optional[str] = None,
) -> str:
    """Save ``take_name`` to ``<out_dir>/<take_name>.fbx``.

    Only the target character's skeleton bones are selected; the source rig,
    helper geometry, lights, etc. are excluded so the output FBX is small and
    targeted at downstream DCC import.

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

    if metadata is not None and skeleton:
        inject_metadata(skeleton[0], metadata)

    try:
        opts = _build_export_options(take_name, config)
        success = FBApplication().FileSave(out_path, opts)
        if not success:
            raise IOError(f"FileSave returned False for {out_path}")
        return out_path
    finally:
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
