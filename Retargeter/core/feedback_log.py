"""Append-only JSONL log of per-take retargeting outcomes.

This is the durable side of the option-advisor feedback loop: every plotted
take writes one JSON line capturing the inputs the advisor saw (features),
the options that were actually used, the optional auto-computed quality
metrics, and (optionally) a Good/Bad label the operator added in the UI.

Why JSONL and not the existing ``Logger``
-----------------------------------------

:class:`Retargeter.core.logger.Logger` is intentionally text-only for human
log reading. Feeding it structured data would couple two responsibilities
(human log + machine training data) and make either side harder to evolve.
JSONL gives us:

* append-safe writes that survive a MoBu crash mid-run,
* trivially loadable from pandas / DuckDB / a SQL importer for stage 2,
* schema evolution (just add a key; readers must tolerate missing keys).

Label semantics
---------------

A run-line and a later label-line for the same ``(out_dir, run_id, take)``
are stored as separate records. Readers should treat the **latest** label
record per ``take`` as the current label so the operator can re-grade a take
without rewriting old lines.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


FEEDBACK_FILENAME = "_retarget_feedback.jsonl"
RECORD_TYPE_RUN = "run"
RECORD_TYPE_LABEL = "label"

# Environment variable override for the central (cross-project) feedback
# log. Set this to a shared / network path for team-wide accumulation, or
# leave unset to use the per-user default under the home directory.
CENTRAL_PATH_ENV = "RETARGETER_FEEDBACK_PATH"
_CENTRAL_DEFAULT_DIRNAME = ".retargeter"
_CENTRAL_DEFAULT_FILENAME = "feedback.jsonl"


@dataclass
class TakeFeedback:
    """All fields a single 'run' record carries.

    ``label`` is normally ``None`` at run time and filled in later via
    :func:`update_label` (which writes a separate ``label`` record).
    """

    take: str
    source_file: str = ""
    source_char: str = ""
    target_char: str = ""
    features: Dict[str, Any] = field(default_factory=dict)
    options_used: Dict[str, Any] = field(default_factory=dict)
    metrics: Optional[Dict[str, Any]] = None
    advisor_version: str = ""
    pipeline_status: str = ""        # "ok" | "failed" | "skipped"
    pipeline_error: str = ""
    label: Optional[str] = None
    label_note: str = ""

    def to_record(self, *, run_id: str) -> Dict[str, Any]:
        return {
            "schema": 1,
            "type": RECORD_TYPE_RUN,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,
            "take": self.take,
            "source_file": self.source_file,
            "source_char": self.source_char,
            "target_char": self.target_char,
            "features": self.features,
            "options_used": self.options_used,
            "metrics": self.metrics,
            "advisor_version": self.advisor_version,
            "pipeline_status": self.pipeline_status,
            "pipeline_error": self.pipeline_error,
            "label": self.label,
            "label_note": self.label_note,
        }


def new_run_id() -> str:
    """One identifier per pipeline run. Pair it with each take record so a
    reader can group records back into runs even if multiple users append
    to the same JSONL concurrently."""
    return uuid.uuid4().hex[:12]


def feedback_path(out_dir: str) -> str:
    """Per-project copy of the feedback log (lives inside ``out_dir``)."""
    return os.path.join(out_dir, FEEDBACK_FILENAME)


def central_feedback_path() -> str:
    """Cross-project, append-forever feedback log used for stage-2 training.

    Resolution order:

    1. ``$RETARGETER_FEEDBACK_PATH`` (lets a team point this at a shared
       drive without code changes).
    2. ``~/.retargeter/feedback.jsonl`` (per-user default on every OS).

    The directory is *not* created here; ``_append_json`` lazily creates
    the parent on first write, so a non-existent path is fine until the
    first feedback line is actually written.
    """
    env = os.environ.get(CENTRAL_PATH_ENV, "").strip()
    if env:
        return os.path.normpath(os.path.expanduser(env))
    home = os.path.expanduser("~")
    return os.path.join(home, _CENTRAL_DEFAULT_DIRNAME, _CENTRAL_DEFAULT_FILENAME)


def append_run_record(
    out_dir: str,
    feedback: TakeFeedback,
    *,
    run_id: str,
) -> Optional[str]:
    """Write one ``run`` record to both the per-project log (if out_dir is
    set) and the central log.

    Returns the **central** path on success (or None if both writes failed),
    because the central log is the durable training store; the per-project
    copy is a convenience snapshot for that specific batch's audit trail.
    """
    record = feedback.to_record(run_id=run_id)
    return _append_to_both(out_dir, record)


def update_label(
    out_dir: str,
    take: str,
    label: Optional[str],
    *,
    note: str = "",
    run_id: str = "",
) -> Optional[str]:
    """Append a ``label`` record to both per-project and central logs.

    Label semantics unchanged: a fresh line is appended (never rewriting
    old ones), and readers should treat the latest label per take as the
    current truth.
    """
    record = {
        "schema": 1,
        "type": RECORD_TYPE_LABEL,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "take": take,
        "label": label,
        "label_note": note,
    }
    return _append_to_both(out_dir, record)


def _append_to_both(out_dir: str, record: Dict[str, Any]) -> Optional[str]:
    """Write one record to the per-project log (if ``out_dir``) and to the
    central log. Returns the central path if either central write succeeded.

    Failures are silent so a temporarily missing network share or a
    read-only output folder never crashes the plot loop; the other sink
    still records the data point.
    """
    project_ok = False
    central_ok = False
    if out_dir:
        project_ok = _append_json(feedback_path(out_dir), record) is not None
    central_path = central_feedback_path()
    central_ok = _append_json(central_path, record) is not None
    if central_ok:
        return central_path
    if project_ok:
        return feedback_path(out_dir)
    return None


def read_records(out_dir: str = "", *, central: bool = False) -> List[Dict[str, Any]]:
    """Read every record from one feedback file.

    * ``read_records(out_dir)`` -> the per-project log inside ``out_dir``.
    * ``read_records(central=True)`` -> the cross-project central log.
    * ``read_records()`` (both empty) -> central log (handy default for
      stage-2 training scripts).
    """
    if central or not out_dir:
        path = central_feedback_path()
    else:
        path = feedback_path(out_dir)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def latest_labels(out_dir: str = "", *, central: bool = False) -> Dict[str, str]:
    """Resolve "what is the *current* label of each take?" by replaying."""
    labels: Dict[str, str] = {}
    for rec in read_records(out_dir, central=central):
        if rec.get("type") != RECORD_TYPE_LABEL:
            continue
        take = rec.get("take") or ""
        if not take:
            continue
        lbl = rec.get("label")
        if lbl is None:
            labels.pop(take, None)
        else:
            labels[take] = str(lbl)
    return labels


def _append_json(path: str, record: Dict[str, Any]) -> Optional[str]:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except Exception:
        return None


def stats_summary(out_dir: str = "", *, central: bool = True) -> Dict[str, Any]:
    """Aggregate the feedback log into a small JSON-friendly summary.

    Default scope is the central log because the Data page in the dialog
    is meant to show "how much have we accumulated across all projects"
    rather than just the current run folder.

    Returned shape::

        {
            "path": ".../feedback.jsonl",   # the file we actually read
            "exists": True,
            "lines": 1240,                  # raw JSONL lines
            "run_records": 1100,
            "label_records": 140,
            "unique_takes": 73,
            "label_counts": {"good": 51, "bad": 22},
            "good_ratio": 0.70,             # good / (good + bad), or None
            "advisor_change_top": [
                ("plot.use_constant_key_reducer", 612),
                ("hik.HIKForceActorSpaceId", 540),
                ("match_source", 488),
            ],
        }

    All counts are best-effort; malformed lines are silently skipped.
    """
    if central or not out_dir:
        path = central_feedback_path()
    else:
        path = feedback_path(out_dir)

    summary: Dict[str, Any] = {
        "path": path,
        "exists": False,
        "lines": 0,
        "run_records": 0,
        "label_records": 0,
        "unique_takes": 0,
        "label_counts": {},
        "good_ratio": None,
        "advisor_change_top": [],
    }
    if not os.path.isfile(path):
        return summary
    summary["exists"] = True

    take_keys: set = set()
    label_keys: set = set()
    label_counts: Dict[str, int] = {}
    advisor_changes: Dict[str, int] = {}

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            summary["lines"] += 1
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rtype = rec.get("type")
            take = rec.get("take") or ""
            if take:
                take_keys.add(take)
            if rtype == RECORD_TYPE_RUN:
                summary["run_records"] += 1
                # Per-take change counters live under
                # options_used.advisor.changed_fields when the advisor was
                # used; fall back to options_used.changed_fields to stay
                # forward-compatible with future schema tweaks.
                opts = rec.get("options_used") or {}
                advisor = opts.get("advisor") or {}
                changed = advisor.get("changed_fields") or opts.get("changed_fields") or []
                for field_key in changed:
                    if not isinstance(field_key, str):
                        continue
                    advisor_changes[field_key] = advisor_changes.get(field_key, 0) + 1
            elif rtype == RECORD_TYPE_LABEL:
                summary["label_records"] += 1
                lbl = rec.get("label")
                if lbl is not None:
                    label = str(lbl)
                    label_counts[label] = label_counts.get(label, 0) + 1
                    if take:
                        label_keys.add(take)

    summary["unique_takes"] = len(take_keys | label_keys)
    summary["label_counts"] = label_counts
    g = int(label_counts.get("good", 0))
    b = int(label_counts.get("bad", 0))
    if (g + b) > 0:
        summary["good_ratio"] = round(g / float(g + b), 3)
    summary["advisor_change_top"] = sorted(
        advisor_changes.items(), key=lambda kv: kv[1], reverse=True
    )[:3]
    return summary
