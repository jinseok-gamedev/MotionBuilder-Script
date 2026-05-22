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
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from . import feedback_log
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
    apply_hik_options,
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

    # Snapshot every target-character bone's Translation/Rotation/Scaling
    # right before each FileMerge and restore any bone the merge mutated.
    # This is a safety net for the scenario where a source FBX bone short
    # name collides with a target-character bone short name (UE4 vs UE5
    # mannequin share ``hand_l`` etc.) and ``BaseModelsAnimation=True``
    # lets the merge overwrite the target bone's base transform. Turn
    # off only if you intentionally want the merge to drive target rig
    # base transforms (rare; the plot step normally handles that via HIK).
    protect_target_transforms: bool = True

    # Pass-through to ``FBFbxOptions.BaseModelsAnimation``. The historical
    # behaviour (True) imports the FBX's static T-pose alongside its
    # animation curves. If you observe target bones snapping to (0,0,0)
    # right after a merge, flipping this to False forces the merge to
    # import animation curves only, leaving the existing rig's base
    # transforms untouched.
    import_base_models_animation: bool = True

    # HumanIK extra options the advisor recommends. Each key is a logical
    # name handled by :func:`retarget_engine.apply_hik_options` which knows
    # how to resolve the actual property/attribute name on the live rig.
    # Empty dict means "do not touch any HIK option" (legacy behaviour).
    hik_options: Dict[str, bool] = field(default_factory=dict)

    # Opt-in post-plot per-take quality metrics. They are several times more
    # expensive than the plot itself on long takes, so they stay off by
    # default and only turn on when the operator ticks the option in the UI.
    compute_metrics: bool = False
    # If True, every plotted take's features/options/(metrics) are appended
    # to ``{out_dir}/_retarget_feedback.jsonl`` for later model training.
    write_feedback_jsonl: bool = True

    # Names of the option fields that the Auto recommend advisor most
    # recently *changed* relative to the operator's prior values. Purely
    # informational: the dialog fills this in when the user pressed Auto,
    # the pipeline echoes it back into the feedback log so stats_summary
    # can show which knobs the advisor is moving most often.
    advisor_changed_fields: List[str] = field(default_factory=list)
    advisor_version: str = "rules-v1"

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
            "protect_target_transforms",
            "import_base_models_animation",
            "compute_metrics",
            "write_feedback_jsonl",
            "dry_run",
            "write_log_file",
            "engine_preset",
            "advisor_version",
        ):
            if attr in data:
                setattr(cfg, attr, data[attr])
        if "fbx_files" in data:
            cfg.fbx_files = list(data["fbx_files"])
        if isinstance(data.get("hik_options"), dict):
            cfg.hik_options = {str(k): bool(v) for k, v in data["hik_options"].items()}
        if isinstance(data.get("advisor_changed_fields"), (list, tuple)):
            cfg.advisor_changed_fields = [str(x) for x in data["advisor_changed_fields"]]
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
    # Weak label hint produced by quality_metrics.suggest_label() when
    # RunConfig.compute_metrics is on. ``None`` if metrics were not
    # computed or the metric signals were ambiguous; the UI uses this to
    # pre-fill the Quality column with a softly-highlighted suggestion.
    label_hint: Optional[str] = None

    def to_csv_row(self) -> Dict[str, str]:
        return {
            "take": self.take_name,
            "source_file": self.source_file,
            "exported": "1" if self.exported else "0",
            "out_path": self.out_path,
            "status": self.status,
            "error": self.error,
            "notes": " | ".join(self.notes),
            "label_hint": self.label_hint or "",
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
    cancel_check: Optional[Callable[[], bool]] = None,
) -> RunReport:
    """Execute the full retargeting pipeline.

    ``progress_cb`` receives ``(done, total, message)`` after each take phase
    so UIs can drive a progress bar without coupling to MotionBuilder.

    ``cancel_check`` is an optional zero-arg callable returning ``True`` when
    the operator has requested cancellation. It is polled at the start of
    each take iteration of every phase; the *currently* running take is
    allowed to finish so the scene is left in a consistent state, then the
    pipeline returns early with whatever ``RunReport`` it has so far.
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
        return _run_inner(config, logger, hooks, report, progress_cb, cancel_check)
    finally:
        if log_path:
            csv_path = log_path.replace(".txt", ".csv")
            try:
                report.write_csv(csv_path)
                logger.info(f"Manifest: {csv_path}")
            except Exception as exc:
                logger.warn(f"Could not write manifest CSV: {exc!r}")
        logger.close_file()


def _is_cancelled(cancel_check: Optional[Callable[[], bool]], logger: Logger) -> bool:
    """Safely poll the cancel callback; never crash the run on a buggy cb."""
    if cancel_check is None:
        return False
    try:
        if cancel_check():
            logger.warn("Cancellation requested by user; stopping at next safe point.")
            return True
    except Exception as exc:
        logger.warn(f"cancel_check crashed: {exc!r}")
    return False


def _run_inner(
    config: RunConfig,
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
    progress_cb: Optional[Callable[[int, int, str], None]],
    cancel_check: Optional[Callable[[], bool]] = None,
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

    created_takes = _import_phase(config, logger, hooks, report, cancel_check)
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
        _export_phase(config, target_char, selected_takes, logger, hooks, report, progress_cb, cancel_check)
        return report
    if not created_takes:
        logger.warn("No takes were created; nothing to plot.")
        return report

    run_id = feedback_log.new_run_id()
    _plot_phase(
        config,
        source_char,
        target_char,
        created_takes,
        logger,
        hooks,
        report,
        cancel_check,
        run_id=run_id,
    )
    _export_phase(config, target_char, created_takes, logger, hooks, report, progress_cb, cancel_check)

    return report


def _import_phase(
    config: RunConfig,
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[str]:
    """Import each source FBX as new take(s). Returns final take names."""
    created: List[str] = []
    for fbx_path in config.fbx_files:
        if _is_cancelled(cancel_check, logger):
            return created
        logger.info(f"Importing: {fbx_path}")
        _safe_call(hooks.get("pre_import"), logger, "pre_import", fbx_path)
        diagnostics: Dict = {}
        try:
            new_takes = import_animation_only(
                fbx_path,
                source_character_name=config.source_character_name,
                target_character_name=config.target_character_name,
                protect_target_transforms=config.protect_target_transforms,
                import_base_models_animation=config.import_base_models_animation,
                diagnostics=diagnostics,
                logger=logger,
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
        applied = diagnostics.get("namespace_remap_applied", False)
        if applied:
            logger.info(
                f"  namespace remap APPLIED: incoming objects prefixed with "
                f"'{namespace_target}:'"
            )
        else:
            err = diagnostics.get("namespace_remap_error") or ""
            logger.error(
                "  namespace remap FAILED: FBFbxOptions.NamespaceList "
                f"assignment did not take. error={err or '(no exception)'}"
            )
        incremented = diagnostics.get("namespace_incremented_to") or ""
        if incremented and incremented != namespace_target:
            logger.info(
                f"  namespace was incremented to '{incremented}:' "
                f"('{namespace_target}' already exists in scene); "
                "cleanup will reconcile incoming bones onto the source rig."
            )
    else:
        logger.info(
            "  namespace remap target: (none - source character has no namespace)"
        )

    fbx_namespaces = diagnostics.get("fbx_namespaces") or []
    if fbx_namespaces:
        logger.info(f"  FBX internal namespaces: {fbx_namespaces}")
        if namespace_target:
            logger.warn(
                "  FBX already carries an internal namespace; the remap will "
                f"prefix it again, producing '{namespace_target}:{fbx_namespaces[0]}:<bone>'. "
                "Re-export the FBX without its namespace to get a clean bind."
            )
    else:
        logger.info("  FBX internal namespaces: (none reported)")

    restored = int(diagnostics.get("target_transforms_restored") or 0)
    if restored > 0:
        logger.warn(
            f"  target character transforms changed during merge: {restored} bone(s) "
            "restored to pre-import values. This indicates an FBX bone short name "
            "collided with a target-character bone short name."
        )

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
    namespace_bones = list(diagnostics.get("source_namespace_bones") or [])
    logger.info(
        f"  source character bones in scene: {len(source_bones)} "
        f"(sample: {source_bones[:6]})"
    )
    if namespace_bones:
        extras = sorted(set(namespace_bones) - set(source_bones))
        if extras:
            sample_extras = extras[:6]
            more = "" if len(extras) <= 6 else f" (+{len(extras) - 6} more)"
            logger.info(
                f"  source namespace also owns {len(extras)} non-HIK bone(s): "
                f"{sample_extras}{more} (helpers like ik_hand_root, ik_foot_root, "
                "root; cleanup will reconcile their duplicates too)"
            )

    new_models = diagnostics.get("new_models") or []
    if not new_models:
        logger.info("  merge added 0 new model(s).")
        return

    # MotionBuilder breaks name clashes by appending " <N>" (single space
    # followed by digits). Strip that so ``ball_l 1`` matches existing
    # ``ball_l`` in source_bones. Use the namespace-wide set (which
    # includes helper bones not bound to a HumanIK slot) when available so
    # the duplicate / non-duplicate split mirrors what cleanup will see.
    suffix_re = re.compile(r"\s+\d+$")
    source_bone_set = set(namespace_bones) if namespace_bones else set(source_bones)

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

    prefix_counts = _summarize_namespace_prefixes(new_models)
    if prefix_counts:
        line = ", ".join(
            f"{k}: {v}" for k, v in sorted(prefix_counts.items(), key=lambda kv: -kv[1])
        )
        logger.info(f"  new bones namespace prefix distribution: {line}")
        no_ns = prefix_counts.get("(no namespace)", 0)
        if namespace_target and no_ns > 0:
            logger.warn(
                f"  {no_ns} new bone(s) arrived without a namespace despite "
                f"target='{namespace_target}'. The remap did not bind those "
                "bones to the source rig."
            )

    if duplicates:
        total_dup_models = sum(len(v) for v in duplicates.values())
        incremented_ns = diagnostics.get("namespace_incremented_to") or ""
        remap_applied = diagnostics.get("namespace_remap_applied", False)
        if incremented_ns and remap_applied:
            # Normal case: MoBu isolated incoming bones into a new namespace
            # because the requested one already existed in scene. The cleanup
            # pass below will reconcile them. Keep the log at INFO so it does
            # not look like a problem.
            logger.info(
                f"  {total_dup_models} new bone(s) landed under '{incremented_ns}:' "
                "because MoBu incremented the namespace. Animation is on those "
                "duplicates; cleanup will transfer it onto the source rig."
            )
        else:
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
            if namespace_target and remap_applied:
                logger.warn(
                    "  Duplicates appeared even though namespace remap reported "
                    "applied. Likely cause: FBX bone hierarchy (parent chain LongName) "
                    "does not match the source character's hierarchy in the setting "
                    "file. The downstream cleanup step will transfer the animation "
                    "back onto the source rig, but you should align the rigs to "
                    "avoid this overhead."
                )
            else:
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


def _summarize_namespace_prefixes(long_names: List[str]) -> Dict[str, int]:
    """Count new bones by their immediate namespace prefix.

    LongName is ``ns1:ns2:...:short`` (colon-separated). We bucket by the
    *innermost* namespace (the part right before the final colon) so a
    bone like ``UE4:pelvis`` shows up under ``UE4`` and a bare ``pelvis``
    shows up under ``(no namespace)``. The pipeline log uses this bucket
    count to surface "remap actually took effect" at a glance: a healthy
    run lands every new bone under the source-character namespace and
    zero under ``(no namespace)``.
    """
    counts: Dict[str, int] = {}
    for long_name in long_names:
        if ":" in long_name:
            prefix = long_name.rsplit(":", 1)[0]
            # Strip any further hierarchy path so we only show the actual
            # namespace, not the full parent chain.
            if ":" in prefix:
                prefix = prefix.split(":")[-1]
        else:
            prefix = "(no namespace)"
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def _extract_pair_features_for_run(source_char, target_char, logger: Logger):
    """Lazy-import + best-effort PairFeatures extraction for the JSONL log.

    Imported lazily so the pipeline keeps working even if a future MoBu
    upgrade breaks ``skeleton_features`` (one optional dataset row is much
    less important than the plot itself).
    """
    try:
        from .skeleton_features import extract_pair_features

        return extract_pair_features(source_char, target_char)
    except Exception as exc:
        logger.warn(f"skeleton_features.extract_pair_features failed: {exc!r}")
        return None


def _record_take_feedback(
    *,
    config: RunConfig,
    logger: Logger,
    report: RunReport,
    run_id: str,
    take_name: str,
    source_char,
    target_char,
    pair_features_dict: Dict,
    source_height_m: Optional[float],
    hik_applied: Dict[str, bool],
    plot_ok: bool,
    plot_error: str,
) -> None:
    """Write one TakeFeedback line per take. Never raises."""
    if not config.write_feedback_jsonl or not config.out_dir:
        return
    metrics_dict = None
    label_hint: Optional[str] = None
    if plot_ok and config.compute_metrics:
        try:
            from .quality_metrics import compute_metrics, suggest_label

            metrics = compute_metrics(
                source_char, target_char, source_height_m=source_height_m
            )
            metrics_dict = metrics.to_dict()
            try:
                label_hint = suggest_label(metrics)
            except Exception as exc:
                # suggest_label is intentionally cheap; if it explodes we
                # still want the metric numbers in the log.
                logger.warn(f"suggest_label failed for '{take_name}': {exc!r}")
        except Exception as exc:
            logger.warn(f"compute_metrics failed for '{take_name}': {exc!r}")

    tr = _find_result(report, take_name)
    source_file = tr.source_file if tr is not None else ""
    if tr is not None and label_hint:
        tr.label_hint = label_hint

    try:
        record = feedback_log.TakeFeedback(
            take=take_name,
            source_file=source_file,
            source_char=getattr(source_char, "LongName", "") or "",
            target_char=getattr(target_char, "LongName", "") or "",
            features=pair_features_dict or {},
            options_used={
                "plot": asdict(config.plot),
                "match_source": bool(config.match_source),
                "hik": dict(config.hik_options or {}),
                "hik_applied": dict(hik_applied or {}),
                "advisor": {
                    "version": config.advisor_version,
                    "changed_fields": list(config.advisor_changed_fields or []),
                },
            },
            metrics=metrics_dict,
            advisor_version=config.advisor_version,
            pipeline_status="ok" if plot_ok else "failed",
            pipeline_error=plot_error,
        )
        feedback_log.append_run_record(config.out_dir, record, run_id=run_id)
    except Exception as exc:
        logger.warn(f"feedback_log append failed for '{take_name}': {exc!r}")


def _plot_phase(
    config: RunConfig,
    source_char,
    target_char,
    take_names: List[str],
    logger: Logger,
    hooks: Dict[str, Callable],
    report: RunReport,
    cancel_check: Optional[Callable[[], bool]] = None,
    run_id: str = "",
) -> None:
    match_source_warned = False

    # Capture features once per run: source/target rigs do not change between
    # takes within a single run, so we save N-1 redundant evaluations.
    pair_features = _extract_pair_features_for_run(source_char, target_char, logger)
    pair_features_dict = pair_features.to_dict() if pair_features is not None else {}
    source_height_m = (
        pair_features.source.height_m if pair_features is not None else None
    )

    for tn in take_names:
        if _is_cancelled(cancel_check, logger):
            return
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
        hik_applied: Dict[str, bool] = {}
        if config.hik_options:
            hik_applied = apply_hik_options(target_char, config.hik_options, logger=logger)
            missing = [k for k, ok in hik_applied.items() if not ok]
            if missing:
                logger.warn(
                    f"  HIK options not exposed on target: {missing}. "
                    "Recommendation recorded in feedback log but not applied."
                )
        link_input(target_char, source_char)
        plot_ok = False
        plot_error = ""
        try:
            logger.info(f"Plotting take '{tn}' ...")
            _safe_call(hooks.get("pre_plot"), logger, "pre_plot", target_char, source_char, take)
            try:
                plot_ok = bool(plot_to_skeleton(target_char, config.plot))
            except Exception as exc:
                plot_error = repr(exc)
                logger.error(f"Plot crashed for '{tn}': {plot_error}")
                _set_status(report, tn, "failed", error=str(exc))
                continue
            if not plot_ok:
                plot_error = "PlotAnimation returned False"
                logger.error(f"Plot returned False for '{tn}'.")
                _set_status(report, tn, "failed", error=plot_error)
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

        _record_take_feedback(
            config=config,
            logger=logger,
            report=report,
            run_id=run_id,
            take_name=tn,
            source_char=source_char,
            target_char=target_char,
            pair_features_dict=pair_features_dict,
            source_height_m=source_height_m,
            hik_applied=hik_applied,
            plot_ok=plot_ok,
            plot_error=plot_error,
        )


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
    cancel_check: Optional[Callable[[], bool]] = None,
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
        if _is_cancelled(cancel_check, logger):
            return
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
