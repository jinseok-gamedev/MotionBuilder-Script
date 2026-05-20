"""End-to-end retargeting pipeline.

The orchestration that ties together :mod:`scene_utils`, :mod:`fbx_io`,
:mod:`take_manager`, :mod:`retarget_engine` and :mod:`root_motion` lives
here. UI layers call :func:`run` with a :class:`RunConfig` describing what
the user picked in the panel.

Phases per source file
----------------------

1. ``pre_import`` hook
2. Animation-only merge -> new takes
3. Rename takes to filename (with optional prefix/suffix and conflict policy)
4. ``post_import`` hook

After every file is imported, for each new take:

5. Set current take, ``link_input``, optional ``apply_match_source``
6. ``pre_plot`` hook
7. ``plot_to_skeleton``
8. ``unbind_input``, ``post_plot`` hook
9. Root motion application (Keep/Strip/Extract)

Finally, for each take the user has marked for export:

10. ``pre_export`` hook
11. ``export_take_to_fbx`` (with optional metadata injection)
12. ``post_export`` hook

Each take's failures are isolated: a thrown exception is captured into the
:class:`RunReport` and the next take is processed normally.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .fbx_io import (
    ExportConfig,
    ExportMetadata,
    cleanup_duplicate_bones,
    delete_duplicate_bone_models,
    export_take_to_fbx,
    import_animation_only,
)
from .logger import Logger, make_run_log_path
from .retarget_engine import (
    PlotConfig,
    apply_match_source,
    link_input,
    plot_to_skeleton,
    unbind_input,
)
from .root_motion import MODE_KEEP, VALID_MODES, apply as apply_root_motion
from .scene_utils import find_character_by_name, validate_setup
from .take_manager import (
    all_take_names,
    clean_all_takes,
    get_take_by_name,
    rename_take,
    set_current_take,
    take_name_from_fbx_path,
    unique_take_name,
)


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------


@dataclass
class TakePlan:
    """One row of the take table: how a single take should be processed."""

    take_name: str
    source_file: str = ""
    export: bool = True
    root_motion_mode: str = MODE_KEEP

    def normalised_mode(self) -> str:
        m = (self.root_motion_mode or MODE_KEEP).lower()
        return m if m in VALID_MODES else MODE_KEEP


@dataclass
class RunConfig:
    source_character_name: str = ""
    target_character_name: str = ""
    fbx_files: List[str] = field(default_factory=list)
    out_dir: str = ""

    plot: PlotConfig = field(default_factory=PlotConfig)
    export: ExportConfig = field(default_factory=ExportConfig)

    take_prefix: str = ""
    take_suffix: str = ""
    take_filename_template: str = "{take}"
    on_conflict: str = "increment"  # "increment" | "overwrite" | "skip"

    default_root_motion: str = MODE_KEEP
    match_source: bool = True
    clean_existing_takes: bool = False
    inject_metadata: bool = True
    # Transfer animation off ``" <N>"`` suffix-renamed duplicate bones back
    # onto the source character's original bones (and delete the duplicates)
    # right after each FileMerge. Without this, FBX files whose hierarchy
    # diverges from the source rig will plot empty even though the merge
    # appeared to succeed.
    cleanup_duplicate_bones: bool = True

    dry_run: bool = False
    write_log_file: bool = True
    engine_preset: str = "ue5"

    # If non-empty, only these take names are processed/exported. Filled in by
    # the UI from the take table; in non-UI runs leave empty to process all
    # newly created takes.
    take_plans: List[TakePlan] = field(default_factory=list)

    def take_plan_for(self, take_name: str) -> Optional[TakePlan]:
        for tp in self.take_plans:
            if tp.take_name == take_name:
                return tp
        return None

    @classmethod
    def from_dict(cls, data: Dict) -> "RunConfig":
        plot = PlotConfig(**(data.get("plot") or {}))
        export = ExportConfig(**(data.get("export") or {}))
        cfg = cls(plot=plot, export=export)
        for attr in (
            "source_character_name",
            "target_character_name",
            "out_dir",
            "take_prefix",
            "take_suffix",
            "take_filename_template",
            "on_conflict",
            "default_root_motion",
            "match_source",
            "clean_existing_takes",
            "inject_metadata",
            "cleanup_duplicate_bones",
            "dry_run",
            "write_log_file",
            "engine_preset",
        ):
            if attr in data:
                setattr(cfg, attr, data[attr])
        if "fbx_files" in data:
            cfg.fbx_files = list(data["fbx_files"])
        return cfg


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------


@dataclass
class TakeResult:
    take_name: str
    source_file: str
    exported: bool = False
    out_path: str = ""
    status: str = "pending"  # pending | ok | skipped | failed
    error: str = ""
    notes: List[str] = field(default_factory=list)

    def to_csv_row(self) -> Dict[str, str]:
        return {
            "take": self.take_name,
            "source_file": self.source_file,
            "exported": "1" if self.exported else "0",
            "out_path": self.out_path,
            "status": self.status,
            "error": self.error,
            "notes": " | ".join(self.notes),
        }


@dataclass
class RunReport:
    config: RunConfig
    results: List[TakeResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status in ("ok", "skipped") for r in self.results)

    def write_csv(self, path: str) -> None:
        import csv

        if not self.results:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["take", "source_file", "exported", "out_path", "status", "error", "notes"],
            )
            writer.writeheader()
            for r in self.results:
                writer.writerow(r.to_csv_row())


# ----------------------------------------------------------------------------
# Hooks
# ----------------------------------------------------------------------------


_HOOK_NAMES = (
    "pre_import",
    "post_import",
    "pre_plot",
    "post_plot",
    "pre_export",
    "post_export",
)


def _load_hooks():
    """Return a dict ``{hook_name: callable}`` from ``config.hooks``."""
    try:
        mod = importlib.import_module("Retargeter.config.hooks")
    except Exception:
        return {}
    out = {}
    for name in _HOOK_NAMES:
        fn = getattr(mod, name, None)
        if callable(fn):
            out[name] = fn
    return out


def _safe_call(hook, logger: Logger, name: str, *args, **kwargs) -> None:
    if hook is None:
        return
    try:
        hook(*args, **kwargs)
    except Exception as exc:
        logger.warn(f"hook {name!r} raised: {exc!r}")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _apply_naming(stem: str, prefix: str, suffix: str) -> str:
    return f"{prefix or ''}{stem}{suffix or ''}"


def _resolve_filename(take_name: str, template: str) -> str:
    template = (template or "{take}").strip()
    try:
        return template.format(take=take_name)
    except Exception:
        return take_name


def _resolve_out_path(out_dir: str, filename: str, on_conflict: str, logger: Logger) -> Optional[str]:
    """Apply conflict policy. Returns the final path, or None if skipping."""
    if not filename.lower().endswith(".fbx"):
        filename += ".fbx"
    candidate = os.path.abspath(os.path.join(out_dir, filename))
    if not os.path.exists(candidate):
        return candidate

    on_conflict = (on_conflict or "increment").lower()
    if on_conflict == "overwrite":
        return candidate
    if on_conflict == "skip":
        logger.warn(f"Output exists, skipping: {candidate}")
        return None
    # increment
    base, ext = os.path.splitext(candidate)
    idx = 1
    while True:
        bumped = f"{base}_{idx:02d}{ext}"
        if not os.path.exists(bumped):
            return bumped
        idx += 1


# ----------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------


def run(
    config: RunConfig,
    logger: Optional[Logger] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> RunReport:
    """Execute the full retargeting pipeline.

    ``progress_cb`` receives ``(done, total, message)`` after each take phase
    so UIs can drive a progress bar without coupling to MotionBuilder.
    """
    logger = logger or Logger()
    report = RunReport(config=config)
    hooks = _load_hooks()

    log_path = None
    if config.write_log_file and config.out_dir and not config.dry_run:
        try:
            log_path = make_run_log_path(config.out_dir)
            logger.open_file(log_path)
            logger.info(f"Log file: {log_path}")
        except Exception as exc:
            logger.warn(f"Could not open log file: {exc!r}")

    try:
        return _run_inner(config, logger, hooks, report, progress_cb)
    finally:
        if log_path:
            csv_path = log_path.replace(".txt", ".csv")
            try:
                report.write_csv(csv_path)
                logger.info(f"Manifest: {csv_path}")
            except Exception as exc:
                logger.warn(f"Could not write manifest CSV: {exc!r}")
        logger.close_file()


def _run_inner(
    config: RunConfig,
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
    progress_cb: Optional[Callable[[int, int, str], None]],
) -> RunReport:
    logger.info(
        f"Retarget run | source='{config.source_character_name}' "
        f"target='{config.target_character_name}' "
        f"files={len(config.fbx_files)} dry_run={config.dry_run}"
    )

    validation = validate_setup(config.source_character_name, config.target_character_name)
    for issue in validation.issues:
        if issue.severity == "error":
            logger.error(issue.message)
        else:
            logger.warn(issue.message)
    if not validation.ok:
        logger.error("Validation failed; aborting.")
        return report

    source_char = find_character_by_name(config.source_character_name)
    target_char = find_character_by_name(config.target_character_name)
    assert source_char is not None and target_char is not None  # validated above

    if config.dry_run:
        _dry_run_summary(config, logger, report)
        return report

    if config.clean_existing_takes:
        logger.info("Clean import: removing existing takes...")
        clean_all_takes()

    created_takes = _import_phase(config, logger, hooks, report)
    if not created_takes and config.take_plans and config.out_dir:
        selected_takes = [tp.take_name for tp in config.take_plans if tp.export]
        if not selected_takes:
            logger.warn("No takes selected for export.")
            return report
        logger.info(f"Exporting {len(selected_takes)} selected existing take(s).")
        for tp in config.take_plans:
            if not tp.export:
                continue
            if _find_result(report, tp.take_name) is None:
                report.results.append(
                    TakeResult(
                        take_name=tp.take_name,
                        source_file=tp.source_file,
                        status="pending",
                    )
                )
        _export_phase(config, target_char, selected_takes, logger, hooks, report, progress_cb)
        return report
    if not created_takes:
        logger.warn("No takes were created; nothing to plot.")
        return report

    _plot_phase(config, source_char, target_char, created_takes, logger, hooks, report)
    _export_phase(config, target_char, created_takes, logger, hooks, report, progress_cb)

    return report


def _import_phase(
    config: RunConfig,
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
) -> List[str]:
    """Import each source FBX as new take(s). Returns final take names."""
    created: List[str] = []
    for fbx_path in config.fbx_files:
        logger.info(f"Importing: {fbx_path}")
        _safe_call(hooks.get("pre_import"), logger, "pre_import", fbx_path)
        diagnostics: Dict = {}
        try:
            new_takes = import_animation_only(
                fbx_path,
                source_character_name=config.source_character_name,
                diagnostics=diagnostics,
            )
        except Exception as exc:
            logger.error(f"Import failed for {fbx_path}: {exc!r}")
            tr = TakeResult(
                take_name=take_name_from_fbx_path(fbx_path),
                source_file=fbx_path,
                status="failed",
                error=str(exc),
            )
            report.results.append(tr)
            continue

        _log_import_diagnostics(diagnostics, logger)

        if not new_takes:
            logger.warn(f"No takes appeared after merging {fbx_path}.")
            if config.cleanup_duplicate_bones:
                try:
                    delete_duplicate_bone_models(diagnostics, logger=logger)
                except Exception as exc:
                    logger.warn(f"  cleanup orphan duplicate models crashed: {exc!r}")
            continue

        # Rename takes BEFORE cleanup so we can pass the final take names
        # straight to the cleanup pass (which limits its FCurve work to those
        # takes and leaves previously imported takes untouched).
        target_stem = take_name_from_fbx_path(fbx_path)
        imported_take_names: List[str] = []
        for i, take in enumerate(new_takes):
            stem = target_stem if i == 0 else f"{target_stem}_p{i:02d}"
            renamed = _apply_naming(stem, config.take_prefix, config.take_suffix)
            unique = unique_take_name(renamed, [t for t in all_take_names() if t != take.Name])
            rename_take(take, unique)
            created.append(unique)
            imported_take_names.append(unique)
            logger.info(f"  -> take '{unique}'")
            _safe_call(hooks.get("post_import"), logger, "post_import", fbx_path, take)
            tr = TakeResult(
                take_name=unique,
                source_file=fbx_path,
                status="pending",
            )
            report.results.append(tr)

        if config.cleanup_duplicate_bones:
            try:
                cleanup_duplicate_bones(
                    diagnostics,
                    source_character_name=config.source_character_name,
                    take_names=imported_take_names,
                    logger=logger,
                )
            except Exception as exc:
                logger.warn(f"  cleanup_duplicate_bones crashed: {exc!r}")
    return created


def _log_import_diagnostics(diagnostics: Dict, logger: Logger) -> None:
    """Summarise what the FBX merge did so users can spot mis-bindings.

    Key signals we surface:

    * ``namespace_target`` / ``fbx_namespaces`` -- whether the remap had a
      destination and what the FBX itself carried.
    * ``new_models`` partitioned into:
        - duplicates of existing source-rig bones (e.g. ``ball_l 1`` while
          the rig already owns ``ball_l``). MoBu appends ``" <N>"`` to break
          name clashes, so we strip that suffix before matching.
        - everything else (likely UE-vs-MoBu naming mismatch, accessories,
          weapons, IK helpers, etc.).
    """
    if not diagnostics:
        return

    namespace_target = diagnostics.get("namespace_target") or ""
    if namespace_target:
        logger.info(f"  namespace remap target: '{namespace_target}'")
    else:
        logger.info(
            "  namespace remap target: (none - source character has no namespace)"
        )

    fbx_namespaces = diagnostics.get("fbx_namespaces") or []
    if fbx_namespaces:
        logger.info(f"  FBX internal namespaces: {fbx_namespaces}")
    else:
        logger.info("  FBX internal namespaces: (none reported)")

    fbx_take_names = diagnostics.get("fbx_take_names") or []
    if fbx_take_names:
        logger.info(f"  FBX take names: {fbx_take_names}")
    else:
        logger.info("  FBX take names: (none reported)")

    import_mode = diagnostics.get("import_mode") or ""
    if import_mode:
        logger.info(f"  import mode: {import_mode}")

    reused_take = diagnostics.get("reused_existing_take") or ""
    if reused_take:
        logger.warn(
            f"  merge reused existing take '{reused_take}' instead of creating a new one."
        )

    source_bones = list(diagnostics.get("source_bones") or [])
    logger.info(
        f"  source character bones in scene: {len(source_bones)} "
        f"(sample: {source_bones[:6]})"
    )

    new_models = diagnostics.get("new_models") or []
    if not new_models:
        logger.info("  merge added 0 new model(s).")
        return

    # MotionBuilder breaks name clashes by appending " <N>" (single space
    # followed by digits). Strip that so ``ball_l 1`` matches existing
    # ``ball_l`` in source_bones.
    suffix_re = re.compile(r"\s+\d+$")
    source_bone_set = set(source_bones)

    duplicates: Dict[str, List[str]] = {}
    non_duplicates: List[str] = []
    for long_name in new_models:
        short = long_name.rsplit(":", 1)[-1]
        normalized = suffix_re.sub("", short)
        if short in source_bone_set or normalized in source_bone_set:
            duplicates.setdefault(normalized, []).append(long_name)
        else:
            non_duplicates.append(long_name)

    sample = new_models[:6]
    more = "" if len(new_models) <= 6 else f" (+{len(new_models) - 6} more)"
    logger.info(f"  merge added {len(new_models)} new model(s): {sample}{more}")

    if duplicates:
        total_dup_models = sum(len(v) for v in duplicates.values())
        logger.warn(
            f"  {total_dup_models} new bone(s) duplicate {len(duplicates)} "
            "existing source-rig bone name(s). Animation is keyed on the "
            "duplicates, NOT the existing rig."
        )
        for original_name in list(duplicates.keys())[:10]:
            dup_long_names = duplicates[original_name][:3]
            logger.warn(f"    '{original_name}' duplicated as: {dup_long_names}")
        if len(duplicates) > 10:
            logger.warn(f"    ... (+{len(duplicates) - 10} more duplicated names)")
        logger.warn(
            "  Likely cause: namespace mismatch between the source character "
            "and the FBX (or both share the same empty namespace, which forces "
            "MoBu to suffix-rename instead of binding by LongName)."
        )

    if non_duplicates:
        sample_nd = non_duplicates[:10]
        more_nd = (
            "" if len(non_duplicates) <= 10 else f" (+{len(non_duplicates) - 10} more)"
        )
        logger.info(
            f"  {len(non_duplicates)} new bone(s) do NOT match any source "
            f"character bone name: {sample_nd}{more_nd}"
        )


def _plot_phase(
    config: RunConfig,
    source_char,
    target_char,
    take_names: List[str],
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
) -> None:
    match_source_warned = False
    for tn in take_names:
        take = get_take_by_name(tn)
        if take is None:
            logger.warn(f"Take '{tn}' disappeared before plot.")
            continue
        set_current_take(take)
        _evaluate_scene()
        if config.match_source:
            if not apply_match_source(target_char, True) and not match_source_warned:
                logger.warn("Match Source property not found on target; HIK defaults will be used.")
                match_source_warned = True
        link_input(target_char, source_char)
        try:
            logger.info(f"Plotting take '{tn}' ...")
            _safe_call(hooks.get("pre_plot"), logger, "pre_plot", target_char, source_char, take)
            try:
                ok = plot_to_skeleton(target_char, config.plot)
            except Exception as exc:
                logger.error(f"Plot crashed for '{tn}': {exc!r}")
                _set_status(report, tn, "failed", error=str(exc))
                continue
            if not ok:
                logger.error(f"Plot returned False for '{tn}'.")
                _set_status(report, tn, "failed", error="PlotAnimation returned False")
                continue
            _safe_call(hooks.get("post_plot"), logger, "post_plot", target_char, take)

            plan = config.take_plan_for(tn)
            mode = plan.normalised_mode() if plan else (config.default_root_motion or MODE_KEEP)
            if mode != MODE_KEEP:
                logger.info(f"Root motion '{mode}' on '{tn}' ...")
                try:
                    rm_result = apply_root_motion(target_char, mode, take)
                    for note in rm_result.notes:
                        logger.info(f"  rm: {note}")
                    _append_notes(report, tn, rm_result.notes)
                except Exception as exc:
                    logger.error(f"Root motion failed for '{tn}': {exc!r}")
                    _append_notes(report, tn, [f"root_motion error: {exc!r}"])
        finally:
            unbind_input(target_char)
            _evaluate_scene()


def _evaluate_scene() -> None:
    """Best-effort scene evaluation after changing takes or HIK input state."""
    try:
        from pyfbsdk import FBSystem  # type: ignore

        FBSystem().Scene.Evaluate()
    except Exception:
        pass


def _export_phase(
    config: RunConfig,
    target_char,
    take_names: List[str],
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
    progress_cb: Optional[Callable[[int, int, str], None]],
) -> None:
    if not config.out_dir:
        logger.warn("No output directory set; skipping export phase.")
        return

    to_export = []
    for tn in take_names:
        plan = config.take_plan_for(tn)
        if plan is None or plan.export:
            to_export.append(tn)

    total = len(to_export)
    for i, tn in enumerate(to_export):
        if progress_cb is not None:
            try:
                progress_cb(i, total, f"Exporting {tn}")
            except Exception:
                pass

        plan = config.take_plan_for(tn)
        rm_mode = plan.normalised_mode() if plan else (config.default_root_motion or MODE_KEEP)

        metadata = None
        if config.inject_metadata:
            tr = _find_result(report, tn)
            metadata = ExportMetadata(
                source_path=(tr.source_file if tr else ""),
                source_take=tn,
                target_character=config.target_character_name,
                plot_rate=int(config.plot.plot_rate),
                root_motion_mode=rm_mode,
                extras={"engine_preset": config.engine_preset},
            )

        filename = _resolve_filename(tn, config.take_filename_template)
        resolved = _resolve_out_path(config.out_dir, filename, config.on_conflict, logger)
        if resolved is None:
            _set_status(report, tn, "skipped", error="output exists")
            continue
        out_dir, final_name = os.path.split(resolved)

        _safe_call(
            hooks.get("pre_export"),
            logger,
            "pre_export",
            get_take_by_name(tn),
            target_char,
            resolved,
            metadata,
        )

        try:
            saved = export_take_to_fbx(
                take_name=tn,
                target_character=target_char,
                out_dir=out_dir,
                config=config.export,
                metadata=metadata,
                filename_override=final_name,
                logger=logger,
            )
        except Exception as exc:
            logger.error(f"Export failed for '{tn}': {exc!r}")
            _set_status(report, tn, "failed", error=str(exc))
            continue

        _set_status(report, tn, "ok", out_path=saved, exported=True)
        logger.info(f"Exported: {saved}")
        _safe_call(
            hooks.get("post_export"),
            logger,
            "post_export",
            get_take_by_name(tn),
            target_char,
            saved,
            metadata,
        )

    if progress_cb is not None:
        try:
            progress_cb(total, total, "Done")
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Dry run
# ----------------------------------------------------------------------------


def _dry_run_summary(config: RunConfig, logger: Logger, report: RunReport) -> None:
    logger.info("[DRY-RUN] No changes will be made.")
    existing_names = set(all_take_names())
    for fbx in config.fbx_files:
        stem = take_name_from_fbx_path(fbx)
        renamed = _apply_naming(stem, config.take_prefix, config.take_suffix)
        unique = unique_take_name(renamed, existing_names)
        existing_names.add(unique)
        filename = _resolve_filename(unique, config.take_filename_template)
        if not filename.lower().endswith(".fbx"):
            filename += ".fbx"
        out_path = os.path.abspath(os.path.join(config.out_dir or ".", filename))
        logger.info(f"  WOULD create take '{unique}' from {fbx}")
        logger.info(f"    -> {out_path}")
        report.results.append(
            TakeResult(
                take_name=unique,
                source_file=fbx,
                exported=False,
                out_path=out_path,
                status="skipped",
                error="dry-run",
            )
        )


# ----------------------------------------------------------------------------
# Report helpers
# ----------------------------------------------------------------------------


def _find_result(report: RunReport, take_name: str) -> Optional[TakeResult]:
    for r in report.results:
        if r.take_name == take_name:
            return r
    return None


def _set_status(
    report: RunReport,
    take_name: str,
    status: str,
    error: str = "",
    out_path: str = "",
    exported: bool = False,
) -> None:
    r = _find_result(report, take_name)
    if r is None:
        r = TakeResult(take_name=take_name, source_file="")
        report.results.append(r)
    r.status = status
    if error:
        r.error = error
    if out_path:
        r.out_path = out_path
    if exported:
        r.exported = True


def _append_notes(report: RunReport, take_name: str, notes: List[str]) -> None:
    r = _find_result(report, take_name)
    if r is None:
        r = TakeResult(take_name=take_name, source_file="")
        report.results.append(r)
    r.notes.extend(notes)


# ----------------------------------------------------------------------------
# Settings JSON load
# ----------------------------------------------------------------------------


def load_default_settings() -> Dict:
    """Read ``config/default_settings.json`` next to the package."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "config", "default_settings.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}
