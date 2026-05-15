"""Batch retargeting - drive a target character with every animation in a folder.

Combines TPoseAligner's non-destructive alignment with the proven workflow
from ``eksod/Retargeter``: open the target file, merge each source FBX into
its own namespace, optionally auto-characterize the source, align both
characters to canonical T-Pose, plot the result onto the target, and save
to the output folder.

The implementation is intentionally defensive - any single file failing
records its error in :class:`BatchReport` and processing continues with
the next file.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.tpose_align import (
    AlignOptions,
    AlignResult,
    align_pair,
    connect_for_retarget,
)


class NamingConvention(Enum):
    """Bone naming styles supported when auto-characterizing a source.

    The maps are deliberately minimal copies of the ones from eksod's
    classic script: only enough bones to satisfy HumanIK's required slots,
    so we work for the most common cases without bringing in a Skeleton
    Definition XML dependency.
    """

    MOTIONBUILDER = "motionbuilder"
    BIPED_3DSMAX = "biped"


_MOBU_REQUIRED = {
    "Hips": "Hips",
    "LeftUpLeg": "LeftUpLeg",
    "LeftLeg": "LeftLeg",
    "LeftFoot": "LeftFoot",
    "RightUpLeg": "RightUpLeg",
    "RightLeg": "RightLeg",
    "RightFoot": "RightFoot",
    "Spine": "Spine",
    "LeftArm": "LeftArm",
    "LeftForeArm": "LeftForeArm",
    "LeftHand": "LeftHand",
    "RightArm": "RightArm",
    "RightForeArm": "RightForeArm",
    "RightHand": "RightHand",
    "Head": "Head",
    "LeftShoulder": "LeftShoulder",
    "RightShoulder": "RightShoulder",
    "Neck": "Neck",
    "Spine1": "Spine1",
    "Spine2": "Spine2",
}

_BIPED_REQUIRED = {
    "Hips": "",
    "LeftUpLeg": "L Thigh",
    "LeftLeg": "L Calf",
    "LeftFoot": "L Foot",
    "RightUpLeg": "R Thigh",
    "RightLeg": "R Calf",
    "RightFoot": "R Foot",
    "Spine": "Spine",
    "LeftArm": "L UpperArm",
    "LeftForeArm": "L Forearm",
    "LeftHand": "L Hand",
    "RightArm": "R UpperArm",
    "RightForeArm": "R Forearm",
    "RightHand": "R Hand",
    "Head": "Head",
    "LeftShoulder": "L Clavicle",
    "RightShoulder": "R Clavicle",
    "Neck": "Neck",
    "Spine1": "Spine1",
    "Spine2": "Spine2",
}


@dataclass
class BatchFileResult:
    """Outcome of processing a single source FBX."""

    source_path: Path
    output_path: Optional[Path] = None
    success: bool = False
    error: str = ""
    elapsed_seconds: float = 0.0
    align_warnings: List[Tuple[str, str]] = field(default_factory=list)
    high_severity_count: int = 0


@dataclass
class BatchReport:
    """Aggregate result of a full batch run."""

    files: List[BatchFileResult] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    @property
    def num_succeeded(self) -> int:
        return sum(1 for f in self.files if f.success)

    @property
    def num_failed(self) -> int:
        return sum(1 for f in self.files if not f.success)

    @property
    def total_elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


def _get_required_bone_map(convention: NamingConvention) -> Dict[str, str]:
    if convention == NamingConvention.BIPED_3DSMAX:
        return _BIPED_REQUIRED
    return _MOBU_REQUIRED


def _try_auto_characterize(
    namespace: str,
    convention: NamingConvention,
    name_prefix: str = "",
):
    """Best-effort: build an FBCharacter from a merged source skeleton.

    Returns the new ``FBCharacter`` if characterization succeeded, ``None``
    otherwise. Largely mirrors the logic in ``eksod/Retargeter`` but we
    accept either MotionBuilder or 3dsMax Biped naming.
    """
    from pyfbsdk import FBCharacter, FBFindModelByLabelName  # type: ignore

    bone_map = _get_required_bone_map(convention)
    char = FBCharacter(f"{namespace.rstrip(':')}_AutoChar")

    found_any = False
    for slot, joint_name in bone_map.items():
        full_name = f"{namespace}{name_prefix}{joint_name}" if joint_name else f"{namespace}{name_prefix}".rstrip(":")
        joint = FBFindModelByLabelName(full_name)
        if joint is None:
            continue
        prop = char.PropertyList.Find(slot + "Link")
        if prop is None:
            continue
        try:
            prop.append(joint)
            found_any = True
        except Exception:
            pass

    if not found_any:
        try:
            char.FBDelete()
        except Exception:
            pass
        return None

    if not char.SetCharacterizeOn(True):
        try:
            char.FBDelete()
        except Exception:
            pass
        return None
    return char


def _build_plot_options(plot_options: Optional[Dict] = None):
    from pyfbsdk import FBPlotOptions, FBRotationFilter, FBTime  # type: ignore

    opts = FBPlotOptions()
    opts.ConstantKeyReducerKeepOneKey = True
    opts.PlotAllTakes = bool((plot_options or {}).get("plot_all_takes", False))
    opts.PlotOnFrame = bool((plot_options or {}).get("plot_on_frame", True))
    opts.PlotPeriod = FBTime(0, 0, 0, 1)
    opts.PreciseTimeDiscontinuities = True
    opts.UseConstantKeyReducer = bool((plot_options or {}).get("use_constant_key_reducer", False))
    opts.PlotTranslationOnRootOnly = bool((plot_options or {}).get("plot_translation_on_root_only", True))
    if (plot_options or {}).get("rotation_filter") == "gimbal":
        opts.RotationFilterToApply = FBRotationFilter.kFBRotationFilterGimbleKiller
    elif (plot_options or {}).get("rotation_filter") == "unroll":
        opts.RotationFilterToApply = FBRotationFilter.kFBRotationFilterUnroll
    return opts


def _list_source_files(folder: Path) -> List[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".fbx" and p.is_file())


def _merge_source(source_path: Path, namespace: str) -> bool:
    from pyfbsdk import FBApplication, FBFbxOptions  # type: ignore

    app = FBApplication()
    options = FBFbxOptions(True)
    options.CustomImportNamespace = namespace.rstrip(":")
    return bool(app.FileMerge(str(source_path), False, options))


def _save_target(
    output_path: Path,
    target_character,
) -> None:
    from pyfbsdk import FBApplication, FBFbxOptions  # type: ignore

    app = FBApplication()
    options = FBFbxOptions(False)
    options.SaveCharacter = True
    options.SaveControlSet = False
    options.SaveCharacterExtension = False
    options.ShowFileDialog = False
    options.ShowOptionsDialog = False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    app.SaveCharacterRigAndAnimation(str(output_path), target_character, options)


def batch_retarget(
    target_fbx: Path,
    source_anim_folder: Path,
    output_folder: Path,
    align_options: Optional[AlignOptions] = None,
    plot_options: Optional[Dict] = None,
    naming_convention: NamingConvention = NamingConvention.MOTIONBUILDER,
    progress_callback=None,
) -> BatchReport:
    """Retarget every ``.fbx`` in ``source_anim_folder`` onto ``target_fbx``.

    Args:
        target_fbx: Path to a Characterized target ``.fbx`` file. Reopened
            fresh for every source so each retarget is isolated.
        source_anim_folder: Directory containing source animation FBXs.
        output_folder: Where to save the retargeted files. Created if it
            does not exist.
        align_options: Options forwarded to :func:`align_pair`. ``None``
            uses defaults appropriate for batch (no fingers, micro-bend).
        plot_options: Optional dict that maps to :class:`FBPlotOptions`
            (see :func:`_build_plot_options` for accepted keys).
        naming_convention: Name map used to auto-characterize the source
            if it is not already Characterized.
        progress_callback: Optional ``callable(index, total, file_result)``
            invoked after each file finishes. Useful for UI progress bars.
    """
    from pyfbsdk import (  # type: ignore
        FBApplication,
        FBCharacterPlotWhere,
        FBSystem,
    )

    target_fbx = Path(target_fbx)
    source_anim_folder = Path(source_anim_folder)
    output_folder = Path(output_folder)
    if not target_fbx.is_file():
        raise FileNotFoundError(f"Target FBX not found: {target_fbx}")
    if not source_anim_folder.is_dir():
        raise NotADirectoryError(f"Source animation folder not found: {source_anim_folder}")
    output_folder.mkdir(parents=True, exist_ok=True)

    files = _list_source_files(source_anim_folder)
    align_options = align_options or AlignOptions(include_fingers=False)
    plot_opts = _build_plot_options(plot_options)

    report = BatchReport()
    app = FBApplication()
    scene = FBSystem().Scene
    namespace = "src:"

    for idx, source_path in enumerate(files):
        result = BatchFileResult(source_path=source_path)
        start = time.time()
        try:
            app.FileNew()
            scene.Evaluate()
            if not app.FileOpen(str(target_fbx)):
                raise RuntimeError(f"Failed to open target file {target_fbx}")
            target_character = app.CurrentCharacter
            if target_character is None:
                raise RuntimeError("Target FBX has no current character set")

            if not _merge_source(source_path, namespace):
                raise RuntimeError(f"Failed to merge source {source_path.name}")
            scene.Evaluate()

            source_character = None
            for ch in scene.Characters:
                if ch is target_character:
                    continue
                source_character = ch
                break

            if source_character is None:
                source_character = _try_auto_characterize(namespace, naming_convention)
                if source_character is None:
                    raise RuntimeError(
                        "Source has no character and auto-characterization failed - "
                        "check bone naming or pre-Characterize the source files."
                    )

            src_align, tgt_align = align_pair(source_character, target_character, align_options)
            result.align_warnings = list(src_align.warnings) + list(tgt_align.warnings)
            result.high_severity_count = (
                src_align.num_high_warnings + tgt_align.num_high_warnings
            )

            connect_for_retarget(source_character, target_character, activate=True)
            scene.Evaluate()

            if not target_character.PlotAnimation(
                FBCharacterPlotWhere.kFBCharacterPlotOnSkeleton,
                plot_opts,
            ):
                raise RuntimeError("PlotAnimation returned False")

            output_path = output_folder / source_path.name
            _save_target(output_path, target_character)

            result.output_path = output_path
            result.success = True
        except Exception as exc:
            result.error = "{}: {}".format(type(exc).__name__, exc)
            result.error += "\n" + traceback.format_exc(limit=4)
        finally:
            result.elapsed_seconds = time.time() - start
            report.files.append(result)
            if progress_callback is not None:
                try:
                    progress_callback(idx + 1, len(files), result)
                except Exception:
                    pass

    report.finished = time.time()
    return report
