"""Options dialog for the Retargeter panel.

Carries every per-run option except the engine preset (kept on the top
toolbar of the main panel because it is the single most frequently changed
control).

The dialog is structured as a left ``QListWidget`` of categories +
right ``QStackedWidget`` of pages so adding a new category is a one-line
``addItem`` change. Page order matches the order operators conceptually
think about a run:

    Plot -> Retargeting -> Naming -> Export -> Presets

The bottom row exposes:

* **Reset to Defaults** - re-read ``config/default_settings.json``
* **Save as Default**   - persist the current widget state back to the JSON
* **Apply**             - emit ``settingsApplied`` (host can react if needed)
* **Close**             - hide the dialog (modeless)

Presets live in ``QSettings`` (``Retargeter/Presets/<name>``) so each user
has their own. Each preset is a JSON snapshot of the current widget state.

Backwards-compatible widget access
----------------------------------

The main panel previously created option widgets as ``self.<name>`` on
``RetargeterPanel``. After this refactor the widgets live on the dialog
instead, and ``_build_config`` reads them via ``self.options_dialog.<name>``.
The take-actions row also reads ``cmb_root_motion`` via the dialog.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional, Tuple

from ._qt import QtCore, QtGui, QtWidgets  # type: ignore

from ..core.option_advisor import HIK_OPTION_KEYS, default_recommender
from ..core.pipeline import load_default_settings
from ..core.retarget_engine import PlotConfig
from ..core.root_motion import MODE_EXTRACT, MODE_KEEP, MODE_STRIP


_PAGE_TITLES = ("Plot", "Retargeting", "Naming", "Export", "Presets", "Data")
_DEFAULT_SETTINGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "config", "default_settings.json")
)

# Background tint we paint onto Auto-touched widgets so the operator can see
# at a glance which options the advisor changed (vs. their own picks).
_AUTO_HIGHLIGHT_STYLE = "background-color: #fff59d;"   # soft amber

# Human labels for the four HIK option keys, kept in display order matching
# HIK_OPTION_KEYS so a future addition is a single tuple entry.
_HIK_OPTION_LABELS: Tuple[Tuple[str, str], ...] = (
    ("HIKForceActorSpaceId", "Force Actor Space (어깨/팔 actor-space 보존)"),
    ("HIKScaleCompensationId", "Scale Compensation (키 차이 보정)"),
    ("HIKTopSpineCorrectionId", "Top Spine Correction (상체 비틀림 보정)"),
    ("HIKFingerPropagationId", "Finger Propagation (손가락 전파 차단)"),
)

# Fields the operator can lock against Auto recommend changes.
# Keep in sync with OptionsDialog._changed_field_to_widget mappings.
_LOCKABLE_FIELD_LABELS: Tuple[Tuple[str, str], ...] = (
    ("match_source", "Match Source"),
    ("plot.plot_rate", "Plot rate"),
    ("plot.plot_translation", "Plot translation"),
    ("plot.use_constant_key_reducer", "Constant key reduction"),
)

# QSettings key for persisting the set of currently locked field keys.
_QSETTINGS_LOCK_KEY = "AutoRecommend/lockedFields"


def _h_row(label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """``[right-aligned label][widget][stretch]`` helper used inside pages."""
    row = QtWidgets.QWidget()
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    lab = QtWidgets.QLabel(label)
    lab.setMinimumWidth(140)
    lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    h.addWidget(lab)
    h.addWidget(widget, 1)
    return row


class OptionsDialog(QtWidgets.QDialog):
    """Modeless options dialog with sidebar + paged content."""

    settingsApplied = QtCore.Signal()
    settingsSavedAsDefault = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None,
                 settings: Optional[Dict] = None,
                 qsettings: Optional[QtCore.QSettings] = None):
        super().__init__(parent)
        self.setObjectName("RetargeterOptionsDialog")
        self.setWindowTitle("Retargeter - Options")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(720, 460)

        self._settings = settings or load_default_settings()
        self._qsettings = qsettings or QtCore.QSettings(
            "Retargeter", "Retargeter"
        )
        # Optional callable returning a dict of additional top-level keys to
        # merge into ``snapshot()`` output (e.g. the engine preset that lives
        # on the main panel toolbar rather than inside this dialog).
        self.extras_provider: Optional[Callable[[], Dict]] = None

        # Auto-recommender plumbing. The panel injects character_pair_provider
        # so the dialog stays decoupled from the panel's character combos.
        self.character_pair_provider: Optional[Callable[[], Tuple[str, str]]] = None
        self._recommender = default_recommender()
        self._snapshot_before_auto: Optional[Dict] = None
        self._last_advisor_changed: List[str] = []
        # Widgets we yellow-highlight per Auto pass and their unhighlight
        # signal connections (so we can disconnect cleanly on Undo).
        self._auto_highlighted: list = []

        self._build_ui()
        self._wire_auto_unhighlight()
        self._populate_widgets_from(self._settings)
        self._refresh_preset_list()
        self._restore_locks()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, stretch=1)

        self.list_pages = QtWidgets.QListWidget()
        self.list_pages.setFixedWidth(140)
        for title in _PAGE_TITLES:
            self.list_pages.addItem(title)
        body.addWidget(self.list_pages)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_plot_page())
        self.stack.addWidget(self._build_retarget_page())
        self.stack.addWidget(self._build_naming_page())
        self.stack.addWidget(self._build_export_page())
        self.stack.addWidget(self._build_presets_page())
        self.stack.addWidget(self._build_data_page())
        body.addWidget(self.stack, stretch=1)

        self.list_pages.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.list_pages.setCurrentRow(0)

        outer.addWidget(self._build_separator())

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_reset = QtWidgets.QPushButton("Reset to Defaults")
        self.btn_save_default = QtWidgets.QPushButton("Save as Default")
        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.btn_save_default)
        btn_row.addStretch(1)
        self.btn_apply = QtWidgets.QPushButton("Apply")
        self.btn_close = QtWidgets.QPushButton("Close")
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_close)
        outer.addLayout(btn_row)

        self.btn_reset.clicked.connect(self._on_reset_defaults)
        self.btn_save_default.clicked.connect(self._on_save_as_default)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_close.clicked.connect(self.hide)

    @staticmethod
    def _build_separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def _new_page(self, title: str) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        header = QtWidgets.QLabel(f"<b>{title}</b>")
        header.setStyleSheet("font-size: 11pt;")
        v.addWidget(header)
        v.addWidget(self._build_separator())
        return page

    def _build_plot_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Plot")
        v = page.layout()

        self.spn_plot_rate = QtWidgets.QSpinBox()
        self.spn_plot_rate.setRange(1, 240)
        self.spn_plot_rate.setSuffix(" fps")
        self.spn_plot_rate.setMinimumWidth(100)
        v.addWidget(_h_row("Plot rate:", self.spn_plot_rate))

        self.cmb_rotation_filter = QtWidgets.QComboBox()
        self.cmb_rotation_filter.addItems(["none", "gimble_killer", "unroll"])
        self.cmb_rotation_filter.setToolTip(
            "FBPlotOptions.RotationFilterToApply. 'gimble_killer' is the safe "
            "default; 'unroll' helps long continuous spins; 'none' preserves "
            "the raw HIK output."
        )
        self.cmb_rotation_filter.setMinimumWidth(140)
        v.addWidget(_h_row("Rotation filter:", self.cmb_rotation_filter))

        self.chk_plot_translation = QtWidgets.QCheckBox("Plot translation (Hips XYZ)")
        v.addWidget(self.chk_plot_translation)

        self.chk_const_key_reducer = QtWidgets.QCheckBox("Constant key reduction")
        v.addWidget(self.chk_const_key_reducer)

        self.chk_const_key_keep_one = QtWidgets.QCheckBox("  -> keep at least one key per channel")
        self.chk_const_key_keep_one.setToolTip(
            "FBPlotOptions.ConstantKeyReducerKeepOneKey. Prevents a fully "
            "static channel from being reduced to zero keys, which some "
            "downstream tools mis-handle."
        )
        v.addWidget(self.chk_const_key_keep_one)

        self.chk_precise_time_disc = QtWidgets.QCheckBox("Precise time discontinuities")
        self.chk_precise_time_disc.setToolTip(
            "FBPlotOptions.PreciseTimeDiscontinuities. Recommended on for HIK "
            "plots; preserves keys at exact step times instead of interpolating."
        )
        v.addWidget(self.chk_precise_time_disc)

        self.chk_plot_all_takes = QtWidgets.QCheckBox("Plot all takes in one call")
        self.chk_plot_all_takes.setToolTip(
            "FBPlotOptions.PlotAllTakes. Off by default because this script "
            "drives takes one-by-one through link_input/plot/unbind; turn on "
            "only if you understand the implications for per-take HIK state."
        )
        v.addWidget(self.chk_plot_all_takes)

        self.chk_compute_metrics = QtWidgets.QCheckBox(
            "Compute post-plot quality metrics (foot contact / wrist flip / hips slide / ...)"
        )
        self.chk_compute_metrics.setToolTip(
            "Off by default: re-evaluating every frame for source and target adds "
            "noticeable cost on long takes. Enable to record numeric quality signals "
            "into _retarget_feedback.jsonl alongside the options used."
        )
        v.addWidget(self.chk_compute_metrics)

        v.addStretch(1)
        return page

    def _build_retarget_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Retargeting")
        v = page.layout()

        # ----- Auto recommend toolbar -----------------------------------
        auto_row = QtWidgets.QHBoxLayout()
        auto_row.setContentsMargins(0, 0, 0, 0)
        auto_row.setSpacing(6)
        self.btn_auto_recommend = QtWidgets.QPushButton("Auto recommend from characters")
        self.btn_auto_recommend.setToolTip(
            "Inspect the current Source / Target HumanIK characters and propose "
            "PlotConfig / HIK option values. Touched widgets are highlighted; "
            "press Undo Auto to revert."
        )
        self.btn_undo_auto = QtWidgets.QPushButton("Undo Auto")
        self.btn_undo_auto.setEnabled(False)
        self.btn_diagnose_hik = QtWidgets.QPushButton("Diagnose HIK")
        self.btn_diagnose_hik.setToolTip(
            "Resolve every HIK option key on the current target character "
            "(attribute or PropertyList scan) and dump the result into the "
            "reasons panel below. Read-only; does not modify the rig."
        )
        auto_row.addWidget(self.btn_auto_recommend)
        auto_row.addWidget(self.btn_undo_auto)
        auto_row.addWidget(self.btn_diagnose_hik)
        auto_row.addStretch(1)
        auto_wrap = QtWidgets.QWidget()
        auto_wrap.setLayout(auto_row)
        v.addWidget(auto_wrap)

        self.btn_auto_recommend.clicked.connect(self._on_auto_recommend)
        self.btn_undo_auto.clicked.connect(self._on_undo_auto)
        self.btn_diagnose_hik.clicked.connect(self._on_diagnose_hik)

        v.addWidget(self._build_separator())

        # ----- Existing HumanIK toggles --------------------------------
        self.chk_match_source = QtWidgets.QCheckBox("HumanIK Match Source")
        v.addWidget(self.chk_match_source)

        self.chk_clean_takes = QtWidgets.QCheckBox("Remove existing takes before import")
        v.addWidget(self.chk_clean_takes)

        self.chk_cleanup_dups = QtWidgets.QCheckBox("Cleanup duplicate bones after merge")
        self.chk_cleanup_dups.setToolTip(
            "When FBX hierarchy differs from the source rig, FileMerge appends ' <N>' "
            "to clashing bone names and the animation lands on those duplicates. "
            "Enable this to transfer the keys back onto the source character bones "
            "and delete the leftover duplicates so plot has data to read."
        )
        v.addWidget(self.chk_cleanup_dups)

        # ----- HIK 4 advisor-controlled options -------------------------
        hik_group = QtWidgets.QGroupBox("HumanIK extra options")
        hik_v = QtWidgets.QVBoxLayout(hik_group)
        hik_v.setContentsMargins(8, 14, 8, 8)
        hik_v.setSpacing(4)
        self.chk_hik: Dict[str, QtWidgets.QCheckBox] = {}
        self.lbl_hik_indicator: Dict[str, QtWidgets.QLabel] = {}
        for key, label in _HIK_OPTION_LABELS:
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            indicator = QtWidgets.QLabel()
            indicator.setFixedWidth(14)
            indicator.setAlignment(QtCore.Qt.AlignCenter)
            self._paint_indicator(indicator, exposed=None)
            row.addWidget(indicator)
            chk = QtWidgets.QCheckBox(label)
            chk.setObjectName(f"chk_hik_{key}")
            chk.setToolTip(
                f"HumanIK property '{key}'. Resolved via attribute lookup or "
                "PropertyList alias scan; if the rig does not expose this "
                "property the value is recorded in feedback but not applied."
            )
            row.addWidget(chk, stretch=1)
            wrap = QtWidgets.QWidget()
            wrap.setLayout(row)
            hik_v.addWidget(wrap)
            self.chk_hik[key] = chk
            self.lbl_hik_indicator[key] = indicator
        v.addWidget(hik_group)

        self.cmb_root_motion = QtWidgets.QComboBox()
        self.cmb_root_motion.addItems([MODE_KEEP, MODE_STRIP, MODE_EXTRACT])
        self.cmb_root_motion.setMinimumWidth(140)
        v.addWidget(_h_row("Default root motion:", self.cmb_root_motion))

        # ----- Locks (Auto skip) ---------------------------------------
        lock_group = QtWidgets.QGroupBox("Locks (Auto recommend will skip these)")
        lock_v = QtWidgets.QVBoxLayout(lock_group)
        lock_v.setContentsMargins(8, 14, 8, 8)
        lock_v.setSpacing(2)
        intro = QtWidgets.QLabel(
            "Tick a row to keep your current value across future Auto runs. "
            "Persists per-user in QSettings."
        )
        intro.setStyleSheet("color: #888; font-size: 9pt;")
        intro.setWordWrap(True)
        lock_v.addWidget(intro)

        self.chk_lock_field: Dict[str, QtWidgets.QCheckBox] = {}
        # Plain rows for non-HIK options.
        for field_key, label in _LOCKABLE_FIELD_LABELS:
            chk = QtWidgets.QCheckBox(label)
            chk.setObjectName(f"chk_lock_{field_key}")
            chk.toggled.connect(self._persist_locks)
            lock_v.addWidget(chk)
            self.chk_lock_field[field_key] = chk
        # HIK 4 share the same look; keep a slight indent to match the layout.
        for key, _label in _HIK_OPTION_LABELS:
            field_key = f"hik.{key}"
            chk = QtWidgets.QCheckBox(f"HIK: {key.replace('HIK', '').replace('Id', '')}")
            chk.setObjectName(f"chk_lock_{field_key}")
            chk.toggled.connect(self._persist_locks)
            lock_v.addWidget(chk)
            self.chk_lock_field[field_key] = chk
        v.addWidget(lock_group)

        # ----- Reason panel --------------------------------------------
        reason_label = QtWidgets.QLabel("Recommendation reasons:")
        reason_label.setStyleSheet("color: #888;")
        v.addWidget(reason_label)
        self.txt_reasons = QtWidgets.QPlainTextEdit()
        self.txt_reasons.setReadOnly(True)
        self.txt_reasons.setMaximumHeight(140)
        self.txt_reasons.setPlaceholderText(
            "Press 'Auto recommend from characters' to fill option suggestions and "
            "their reasoning here. Yellow-highlighted widgets above were changed "
            "by the advisor; you can override any of them by hand."
        )
        v.addWidget(self.txt_reasons)

        v.addStretch(1)
        return page

    def _build_naming_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Naming")
        v = page.layout()

        self.txt_take_prefix = QtWidgets.QLineEdit()
        self.txt_take_suffix = QtWidgets.QLineEdit()
        self.txt_take_prefix.setPlaceholderText("prefix")
        self.txt_take_suffix.setPlaceholderText("suffix")
        self.txt_take_prefix.setMinimumWidth(80)
        self.txt_take_suffix.setMinimumWidth(80)

        prefix_widget = QtWidgets.QWidget()
        prefix_row = QtWidgets.QHBoxLayout(prefix_widget)
        prefix_row.setContentsMargins(0, 0, 0, 0)
        prefix_row.setSpacing(4)
        prefix_row.addWidget(self.txt_take_prefix, 1)
        plus_label = QtWidgets.QLabel("+ take +")
        plus_label.setAlignment(QtCore.Qt.AlignCenter)
        plus_label.setMinimumWidth(60)
        prefix_row.addWidget(plus_label, 0)
        prefix_row.addWidget(self.txt_take_suffix, 1)
        v.addWidget(_h_row("Take name:", prefix_widget))

        self.txt_filename_template = QtWidgets.QLineEdit()
        self.txt_filename_template.setToolTip("Available placeholders: {take}")
        self.txt_filename_template.setMinimumWidth(160)
        v.addWidget(_h_row("Filename template:", self.txt_filename_template))

        self.cmb_on_conflict = QtWidgets.QComboBox()
        self.cmb_on_conflict.addItems(["increment", "overwrite", "skip"])
        self.cmb_on_conflict.setMinimumWidth(140)
        v.addWidget(_h_row("On file conflict:", self.cmb_on_conflict))

        v.addStretch(1)
        return page

    def _build_export_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Export")
        v = page.layout()

        self.cmb_fbx_version = QtWidgets.QComboBox()
        self.cmb_fbx_version.addItems(["FBX202000", "FBX201800", "FBX201600", "FBX201400"])
        self.cmb_fbx_version.setMinimumWidth(160)
        v.addWidget(_h_row("FBX version:", self.cmb_fbx_version))

        self.chk_ascii = QtWidgets.QCheckBox("ASCII FBX")
        v.addWidget(self.chk_ascii)

        self.chk_inject_metadata = QtWidgets.QCheckBox("Inject metadata into FBX")
        v.addWidget(self.chk_inject_metadata)

        info = QtWidgets.QLabel(
            "Engine preset moved to the panel toolbar (top-left)."
        )
        info.setStyleSheet("color: #888; font-style: italic;")
        info.setWordWrap(True)
        v.addWidget(info)

        v.addStretch(1)
        return page

    def _build_presets_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Presets")
        v = page.layout()

        intro = QtWidgets.QLabel(
            "Save a named snapshot of all option values. Stored per-user "
            "(QSettings) so each operator can keep their own presets."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888;")
        v.addWidget(intro)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QtWidgets.QLabel("Preset:"))
        self.cmb_presets = QtWidgets.QComboBox()
        self.cmb_presets.setMinimumWidth(200)
        row.addWidget(self.cmb_presets, 1)
        self.btn_preset_load = QtWidgets.QPushButton("Load")
        self.btn_preset_save = QtWidgets.QPushButton("Save Current As...")
        self.btn_preset_delete = QtWidgets.QPushButton("Delete")
        row.addWidget(self.btn_preset_load)
        row.addWidget(self.btn_preset_save)
        row.addWidget(self.btn_preset_delete)
        v.addLayout(row)

        v.addStretch(1)

        self.btn_preset_load.clicked.connect(self._on_preset_load)
        self.btn_preset_save.clicked.connect(self._on_preset_save_as)
        self.btn_preset_delete.clicked.connect(self._on_preset_delete)
        return page

    def _build_data_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Data")
        v = page.layout()

        intro = QtWidgets.QLabel(
            "Aggregate view of the central feedback log "
            "(<code>~/.retargeter/feedback.jsonl</code> or the path in "
            "<code>RETARGETER_FEEDBACK_PATH</code>). Use this to gauge how "
            "much labelled data has been collected for the stage-2 ML model."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888;")
        v.addWidget(intro)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        self.btn_data_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_data_open_folder = QtWidgets.QPushButton("Open central folder")
        self.btn_data_open_file = QtWidgets.QPushButton("Reveal feedback.jsonl")
        toolbar.addWidget(self.btn_data_refresh)
        toolbar.addWidget(self.btn_data_open_folder)
        toolbar.addWidget(self.btn_data_open_file)
        toolbar.addStretch(1)
        v.addLayout(toolbar)

        self.lbl_data_path = QtWidgets.QLabel("(no data yet)")
        self.lbl_data_path.setStyleSheet("color: #666; font-family: monospace;")
        self.lbl_data_path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_data_path)

        self.txt_data_summary = QtWidgets.QPlainTextEdit()
        self.txt_data_summary.setReadOnly(True)
        self.txt_data_summary.setMinimumHeight(220)
        self.txt_data_summary.setPlaceholderText(
            "Press Refresh to load central feedback stats."
        )
        v.addWidget(self.txt_data_summary, stretch=1)

        self.btn_data_refresh.clicked.connect(self._on_refresh_data_page)
        self.btn_data_open_folder.clicked.connect(self._on_open_central_folder)
        self.btn_data_open_file.clicked.connect(self._on_reveal_central_file)

        # Auto-populate on first open so the user does not need to click
        # Refresh to know whether the log even exists yet.
        QtCore.QTimer.singleShot(0, self._on_refresh_data_page)
        return page

    # ------------------------------------------------------------------
    # Data page handlers
    # ------------------------------------------------------------------

    def _on_refresh_data_page(self) -> None:
        try:
            from ..core import feedback_log
        except Exception as exc:
            self.txt_data_summary.setPlainText(f"Cannot import feedback_log: {exc!r}")
            return
        try:
            summary = feedback_log.stats_summary(central=True)
        except Exception as exc:
            self.txt_data_summary.setPlainText(f"stats_summary failed: {exc!r}")
            return

        path = summary.get("path") or "(unknown)"
        self.lbl_data_path.setText(path)
        if not summary.get("exists"):
            self.txt_data_summary.setPlainText(
                "No feedback log yet at the path above.\n\n"
                "It will be created the first time a take is plotted with "
                "write_feedback_jsonl enabled, or when 'Save feedback' is "
                "pressed with at least one Quality label set."
            )
            return

        lines: List[str] = []
        lines.append(f"Lines (raw JSONL):     {summary.get('lines', 0)}")
        lines.append(f"  - run records:       {summary.get('run_records', 0)}")
        lines.append(f"  - label records:     {summary.get('label_records', 0)}")
        lines.append(f"Unique takes seen:     {summary.get('unique_takes', 0)}")

        counts = summary.get("label_counts") or {}
        if counts:
            counts_str = ", ".join(
                f"{k}={v}" for k, v in sorted(counts.items())
            )
        else:
            counts_str = "(none)"
        lines.append(f"Label counts:          {counts_str}")
        good_ratio = summary.get("good_ratio")
        if good_ratio is not None:
            lines.append(f"good / (good+bad):     {good_ratio:.1%}")
        else:
            lines.append("good / (good+bad):     n/a")

        top = summary.get("advisor_change_top") or []
        lines.append("")
        if top:
            lines.append("Advisor most-changed fields (top 3):")
            for key, count in top:
                lines.append(f"  {count:>5}  {key}")
        else:
            lines.append(
                "No advisor change records yet. Press 'Auto recommend' "
                "and run a take to start accumulating this signal."
            )
        self.txt_data_summary.setPlainText("\n".join(lines))

    def _on_open_central_folder(self) -> None:
        try:
            from ..core import feedback_log
        except Exception:
            return
        try:
            path = feedback_log.central_feedback_path()
        except Exception:
            return
        folder = os.path.dirname(os.path.abspath(path))
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
        self._open_in_explorer(folder)

    def _on_reveal_central_file(self) -> None:
        try:
            from ..core import feedback_log
        except Exception:
            return
        try:
            path = feedback_log.central_feedback_path()
        except Exception:
            return
        if not os.path.isfile(path):
            # Nothing to reveal yet; fall back to the folder so the user can
            # at least confirm where the log will appear.
            self._open_in_explorer(os.path.dirname(os.path.abspath(path)))
            return
        self._open_in_explorer(path, reveal=True)

    def _open_in_explorer(self, path: str, *, reveal: bool = False) -> None:
        try:
            from ._qt_helpers import open_in_file_explorer
        except Exception:
            open_in_file_explorer = None
        if open_in_file_explorer is not None:
            try:
                open_in_file_explorer(path)
                return
            except Exception:
                pass
        # Generic fallback using QDesktopServices.
        url = QtCore.QUrl.fromLocalFile(path)
        QtGui.QDesktopServices.openUrl(url)

    # ------------------------------------------------------------------
    # Snapshot <-> widgets
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict:
        """Serialize current widget state to a dict matching the default JSON.

        If ``extras_provider`` is set, the dict it returns is merged into the
        top level (so e.g. the panel can inject ``engine_preset`` which lives
        on the toolbar rather than in this dialog).
        """
        snap: Dict = {
            "plot": {
                "plot_rate": int(self.spn_plot_rate.value()),
                "plot_translation": self.chk_plot_translation.isChecked(),
                "use_constant_key_reducer": self.chk_const_key_reducer.isChecked(),
                "constant_key_reducer_keep_one": self.chk_const_key_keep_one.isChecked(),
                "precise_time_discontinuities": self.chk_precise_time_disc.isChecked(),
                "plot_all_takes": self.chk_plot_all_takes.isChecked(),
                "rotation_filter": self.cmb_rotation_filter.currentText(),
            },
            "metrics": {
                "compute": self.chk_compute_metrics.isChecked(),
            },
            "import_options": {
                "clean_existing_takes": self.chk_clean_takes.isChecked(),
                "cleanup_duplicate_bones": self.chk_cleanup_dups.isChecked(),
            },
            "match_source": {
                "enabled": self.chk_match_source.isChecked(),
            },
            "hik_options": {
                key: self.chk_hik[key].isChecked() for key in HIK_OPTION_KEYS
            },
            "root_motion": {
                "default_mode": self.cmb_root_motion.currentText(),
            },
            "export": {
                "fbx_version": self.cmb_fbx_version.currentText(),
                "ascii": self.chk_ascii.isChecked(),
                "filename_template": self.txt_filename_template.text() or "{take}",
                "on_conflict": self.cmb_on_conflict.currentText(),
            },
            "metadata": {
                "inject": self.chk_inject_metadata.isChecked(),
            },
            "naming": {
                "take_prefix": self.txt_take_prefix.text(),
                "take_suffix": self.txt_take_suffix.text(),
            },
        }
        if self.extras_provider is not None:
            try:
                extras = self.extras_provider() or {}
                if isinstance(extras, dict):
                    snap.update(extras)
            except Exception:
                pass
        return snap

    def _populate_widgets_from(self, settings: Dict) -> None:
        """Inverse of :meth:`snapshot` - drive widgets from a settings dict."""
        plot = settings.get("plot") or {}
        self.spn_plot_rate.setValue(int(plot.get("plot_rate", 30)))
        self.chk_plot_translation.setChecked(bool(plot.get("plot_translation", True)))
        self.chk_const_key_reducer.setChecked(bool(plot.get("use_constant_key_reducer", False)))
        self.chk_const_key_keep_one.setChecked(bool(plot.get("constant_key_reducer_keep_one", True)))
        self.chk_precise_time_disc.setChecked(bool(plot.get("precise_time_discontinuities", True)))
        self.chk_plot_all_takes.setChecked(bool(plot.get("plot_all_takes", False)))
        self.cmb_rotation_filter.setCurrentText(str(plot.get("rotation_filter", "gimble_killer")))

        metrics_cfg = settings.get("metrics") or {}
        self.chk_compute_metrics.setChecked(bool(metrics_cfg.get("compute", False)))

        import_cfg = settings.get("import_options") or {}
        self.chk_clean_takes.setChecked(bool(import_cfg.get("clean_existing_takes", False)))
        self.chk_cleanup_dups.setChecked(bool(import_cfg.get("cleanup_duplicate_bones", True)))

        match = settings.get("match_source") or {}
        self.chk_match_source.setChecked(bool(match.get("enabled", True)))

        hik_cfg = settings.get("hik_options") or {}
        for key in HIK_OPTION_KEYS:
            self.chk_hik[key].setChecked(bool(hik_cfg.get(key, False)))

        rm = settings.get("root_motion") or {}
        self.cmb_root_motion.setCurrentText(str(rm.get("default_mode", MODE_KEEP)))

        export = settings.get("export") or {}
        self.cmb_fbx_version.setCurrentText(str(export.get("fbx_version", "FBX201800")))
        self.chk_ascii.setChecked(bool(export.get("ascii", False)))
        self.txt_filename_template.setText(str(export.get("filename_template", "{take}")))
        self.cmb_on_conflict.setCurrentText(str(export.get("on_conflict", "increment")))

        meta = settings.get("metadata") or {}
        self.chk_inject_metadata.setChecked(bool(meta.get("inject", True)))

        naming = settings.get("naming") or {}
        self.txt_take_prefix.setText(str(naming.get("take_prefix", "")))
        self.txt_take_suffix.setText(str(naming.get("take_suffix", "")))

    # ------------------------------------------------------------------
    # Bottom buttons
    # ------------------------------------------------------------------

    def _on_reset_defaults(self) -> None:
        confirm = QtWidgets.QMessageBox.question(
            self, "Reset options",
            "Reload all options from config/default_settings.json?\n"
            "Unsaved changes will be lost.",
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        self._settings = load_default_settings()
        self._populate_widgets_from(self._settings)

    def _on_save_as_default(self) -> None:
        snap = self._merge_with_existing_defaults(self.snapshot())
        try:
            with open(_DEFAULT_SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, indent=4)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Save as Default", f"Could not write defaults file:\n{exc!r}"
            )
            return
        QtWidgets.QMessageBox.information(
            self, "Save as Default",
            f"Saved current options as default:\n{_DEFAULT_SETTINGS_PATH}",
        )
        self.settingsSavedAsDefault.emit()

    @staticmethod
    def _merge_with_existing_defaults(snap: Dict) -> Dict:
        """Preserve unknown keys/sections from the existing JSON on disk."""
        try:
            with open(_DEFAULT_SETTINGS_PATH, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        for section, values in snap.items():
            if isinstance(values, dict) and isinstance(existing.get(section), dict):
                merged = dict(existing[section])
                merged.update(values)
                existing[section] = merged
            else:
                existing[section] = values
        return existing

    def _on_apply(self) -> None:
        self.settingsApplied.emit()
        self.btn_apply.setText("Applied")
        QtCore.QTimer.singleShot(1200, lambda: self.btn_apply.setText("Apply"))

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _preset_names(self) -> list:
        self._qsettings.beginGroup("Presets")
        names = sorted(self._qsettings.childKeys())
        self._qsettings.endGroup()
        return names

    def _refresh_preset_list(self) -> None:
        names = self._preset_names()
        self.cmb_presets.blockSignals(True)
        self.cmb_presets.clear()
        self.cmb_presets.addItems(names)
        self.cmb_presets.blockSignals(False)
        has_any = bool(names)
        self.btn_preset_load.setEnabled(has_any)
        self.btn_preset_delete.setEnabled(has_any)

    def _on_preset_load(self) -> None:
        name = self.cmb_presets.currentText()
        if not name:
            return
        raw = self._qsettings.value(f"Presets/{name}", "")
        if not raw:
            return
        try:
            data = json.loads(raw)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Load preset", f"Preset '{name}' is corrupt:\n{exc!r}"
            )
            return
        self._populate_widgets_from(data)

    def _on_preset_save_as(self) -> None:
        existing = self._preset_names()
        suggested = ""
        if self.cmb_presets.currentText():
            suggested = self.cmb_presets.currentText()
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save preset", "Preset name:", text=suggested
        )
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        if name in existing:
            confirm = QtWidgets.QMessageBox.question(
                self, "Overwrite preset",
                f"Preset '{name}' already exists. Overwrite?",
            )
            if confirm != QtWidgets.QMessageBox.Yes:
                return
        payload = json.dumps(self.snapshot())
        self._qsettings.setValue(f"Presets/{name}", payload)
        self._qsettings.sync()
        self._refresh_preset_list()
        index = self.cmb_presets.findText(name)
        if index >= 0:
            self.cmb_presets.setCurrentIndex(index)

    def _on_preset_delete(self) -> None:
        name = self.cmb_presets.currentText()
        if not name:
            return
        confirm = QtWidgets.QMessageBox.question(
            self, "Delete preset", f"Delete preset '{name}'?",
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        self._qsettings.remove(f"Presets/{name}")
        self._qsettings.sync()
        self._refresh_preset_list()

    def showEvent(self, event):  # noqa: N802 - Qt naming convention
        super().showEvent(event)
        try:
            self.refresh_hik_indicators()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # HIK option exposure indicators
    # ------------------------------------------------------------------

    @staticmethod
    def _paint_indicator(label: QtWidgets.QLabel, exposed: Optional[bool]) -> None:
        """Render the small dot beside each HIK checkbox.

        ``exposed`` semantics:
          * None  -> unknown (no characters checked yet) -> hollow grey
          * True  -> property/attribute resolved on the target -> green dot
          * False -> nothing resolved -> red dot + 'not exposed' tooltip
        """
        if exposed is None:
            label.setText("\u25cb")  # white circle
            label.setStyleSheet("color: #888;")
            label.setToolTip("HIK option exposure unknown (run Diagnose HIK or Auto recommend).")
        elif exposed:
            label.setText("\u25cf")  # black circle filled
            label.setStyleSheet("color: #4caf50;")
            label.setToolTip("Exposed on the current target character; value will be applied at plot time.")
        else:
            label.setText("\u25cf")
            label.setStyleSheet("color: #e53935;")
            label.setToolTip(
                "Not exposed on the current target character. The value is "
                "still recorded to feedback log but apply_hik_options will skip it."
            )

    def refresh_hik_indicators(self) -> None:
        """Probe the current target character for each HIK option and recolour
        the indicator dots accordingly. Safe to call anytime; no-op outside MoBu."""
        target_char = self._resolve_target_character()
        if target_char is None:
            for key in HIK_OPTION_KEYS:
                self._paint_indicator(self.lbl_hik_indicator[key], exposed=None)
            return
        try:
            from ..core.retarget_engine import hik_option_exposed
        except Exception:
            return
        for key in HIK_OPTION_KEYS:
            try:
                exposed = hik_option_exposed(target_char, key)
            except Exception:
                exposed = False
            self._paint_indicator(self.lbl_hik_indicator[key], exposed=exposed)

    def _resolve_target_character(self):
        if self.character_pair_provider is None:
            return None
        try:
            _src, tgt = self.character_pair_provider()
        except Exception:
            return None
        if not tgt:
            return None
        try:
            from ..core.scene_utils import find_character_by_name
        except Exception:
            return None
        try:
            return find_character_by_name(tgt)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Auto recommend (rule-based option advisor)
    # ------------------------------------------------------------------

    # Map advisor ``changed_fields`` strings -> the widget to highlight.
    # Centralising it here keeps Auto / Undo / unhighlight in lockstep
    # when a new option is added (one entry per option).
    def _changed_field_to_widget(self, key: str) -> Optional[QtWidgets.QWidget]:
        if key == "match_source":
            return self.chk_match_source
        if key == "plot.plot_rate":
            return self.spn_plot_rate
        if key == "plot.plot_translation":
            return self.chk_plot_translation
        if key == "plot.use_constant_key_reducer":
            return self.chk_const_key_reducer
        if key.startswith("hik."):
            return self.chk_hik.get(key.split(".", 1)[1])
        return None

    def _wire_auto_unhighlight(self) -> None:
        """When the user changes a widget by hand, drop its Auto highlight.

        We connect once at construction time; the per-widget signals are
        cheap because we ``setStyleSheet("")`` only when the widget is in
        the currently-highlighted set.
        """
        def _drop(widget: QtWidgets.QWidget) -> None:
            if widget in self._auto_highlighted:
                widget.setStyleSheet("")
                self._auto_highlighted.remove(widget)

        for chk in (
            self.chk_match_source,
            self.chk_plot_translation,
            self.chk_const_key_reducer,
            self.chk_compute_metrics,
        ):
            chk.toggled.connect(lambda _checked, w=chk: _drop(w))
        for chk in self.chk_hik.values():
            chk.toggled.connect(lambda _checked, w=chk: _drop(w))
        self.spn_plot_rate.valueChanged.connect(
            lambda _val, w=self.spn_plot_rate: _drop(w)
        )

    def _clear_auto_highlights(self) -> None:
        for w in list(self._auto_highlighted):
            try:
                w.setStyleSheet("")
            except Exception:
                pass
        self._auto_highlighted = []

    def _highlight_widget_for_field(self, field_key: str) -> None:
        w = self._changed_field_to_widget(field_key)
        if w is None:
            return
        try:
            w.setStyleSheet(_AUTO_HIGHLIGHT_STYLE)
        except Exception:
            return
        if w not in self._auto_highlighted:
            self._auto_highlighted.append(w)

    def _current_plot_config(self) -> PlotConfig:
        return PlotConfig(
            plot_rate=int(self.spn_plot_rate.value()),
            plot_translation=self.chk_plot_translation.isChecked(),
            use_constant_key_reducer=self.chk_const_key_reducer.isChecked(),
        )

    def _current_hik_dict(self) -> Dict[str, bool]:
        return {key: self.chk_hik[key].isChecked() for key in HIK_OPTION_KEYS}

    def _on_auto_recommend(self) -> None:
        # 1. Resolve which characters to inspect.
        if self.character_pair_provider is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Auto recommend",
                "No character provider is wired up; this dialog cannot reach "
                "the panel's Source / Target combo boxes.",
            )
            return
        try:
            src_name, tgt_name = self.character_pair_provider()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Auto recommend",
                f"Failed to read Source / Target characters:\n{exc!r}",
            )
            return
        if not src_name or not tgt_name:
            QtWidgets.QMessageBox.warning(
                self, "Auto recommend",
                "Select both Source and Target characters on the main panel first.",
            )
            return

        # 2. Lazy-import: skeleton_features / scene_utils need pyfbsdk and we
        #    do not want to require it just to open the dialog in a viewer.
        try:
            from ..core.scene_utils import find_character_by_name
            from ..core.skeleton_features import extract_pair_features
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Auto recommend",
                f"Cannot import scene helpers (pyfbsdk available?):\n{exc!r}",
            )
            return

        src_char = find_character_by_name(src_name)
        tgt_char = find_character_by_name(tgt_name)
        if src_char is None or tgt_char is None:
            QtWidgets.QMessageBox.warning(
                self, "Auto recommend",
                f"Characters not found in scene: source={src_name!r}, target={tgt_name!r}.",
            )
            return

        # 3. Capture undo snapshot, then extract features and recommend.
        self._snapshot_before_auto = self.snapshot()
        self._clear_auto_highlights()

        try:
            features = extract_pair_features(src_char, tgt_char)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self, "Auto recommend",
                f"extract_pair_features failed:\n{exc!r}",
            )
            self._snapshot_before_auto = None
            return

        rec = self._recommender.recommend(
            features,
            current_plot=self._current_plot_config(),
            current_match_source=self.chk_match_source.isChecked(),
            current_hik=self._current_hik_dict(),
        )

        # 4. Apply recommendation to widgets and highlight only what changed.
        #    Block signals so our unhighlight handler does not immediately
        #    fire on the values we are about to set.
        self._apply_recommendation_to_widgets(rec)
        for field_key in sorted(rec.changed_fields):
            self._highlight_widget_for_field(field_key)

        # 5. Print reasons. Always end with a summary line so even a no-op
        #    recommendation gives the operator a confirmation.
        self.txt_reasons.setPlainText("")
        if rec.reasons:
            self.txt_reasons.appendPlainText("\n".join(rec.reasons))
        if rec.changed_fields:
            self.txt_reasons.appendPlainText(
                f"\n--- {len(rec.changed_fields)}개 옵션 변경: "
                + ", ".join(sorted(rec.changed_fields))
            )
        else:
            self.txt_reasons.appendPlainText("\n--- 추천 결과: 현재 설정 유지 (변경 없음)")

        # Remember which fields the advisor moved this round so the panel
        # can echo it into RunConfig.advisor_changed_fields -> feedback log.
        self._last_advisor_changed = sorted(rec.changed_fields)

        # The advisor has already touched the target -- refresh exposure
        # indicators so users see green/red dots for the actual rig now.
        self.refresh_hik_indicators()
        self.btn_undo_auto.setEnabled(True)

    def _apply_recommendation_to_widgets(self, rec) -> None:
        """Write the recommendation back to widgets without re-triggering the
        ``toggled``/``valueChanged`` signals that would clear highlights.

        Locked fields are removed from ``rec.changed_fields`` so they neither
        get applied nor highlighted; instead a single note is appended to
        ``rec.reasons`` so the operator can see which knobs were respected."""
        locked = self._collect_locked_fields()
        skipped: List[str] = []

        def _is_locked(field_key: str) -> bool:
            if field_key not in locked:
                return False
            if field_key in rec.changed_fields:
                skipped.append(field_key)
                rec.changed_fields.discard(field_key)
            return True

        for w in (
            self.spn_plot_rate,
            self.chk_plot_translation,
            self.chk_const_key_reducer,
            self.chk_match_source,
            *self.chk_hik.values(),
        ):
            w.blockSignals(True)
        try:
            if not _is_locked("plot.plot_rate"):
                self.spn_plot_rate.setValue(int(rec.plot.plot_rate))
            if not _is_locked("plot.plot_translation"):
                self.chk_plot_translation.setChecked(bool(rec.plot.plot_translation))
            if not _is_locked("plot.use_constant_key_reducer"):
                self.chk_const_key_reducer.setChecked(bool(rec.plot.use_constant_key_reducer))
            if not _is_locked("match_source"):
                self.chk_match_source.setChecked(bool(rec.match_source))
            for key in HIK_OPTION_KEYS:
                if _is_locked(f"hik.{key}"):
                    continue
                self.chk_hik[key].setChecked(bool(rec.hik.get(key, False)))
        finally:
            for w in (
                self.spn_plot_rate,
                self.chk_plot_translation,
                self.chk_const_key_reducer,
                self.chk_match_source,
                *self.chk_hik.values(),
            ):
                w.blockSignals(False)

        if skipped:
            rec.reasons.append(
                "[lock] Skipped per user lock: " + ", ".join(sorted(skipped))
            )

    # ------------------------------------------------------------------
    # Lock persistence
    # ------------------------------------------------------------------

    def _collect_locked_fields(self) -> set:
        return {k for k, chk in self.chk_lock_field.items() if chk.isChecked()}

    def _persist_locks(self, *_args) -> None:
        """Write the current lock state into QSettings.

        Stored as a semicolon-joined string for maximum portability across
        QSettings backends (registry on Windows behaves badly with QStringList
        in some PySide/PyQt builds)."""
        try:
            qs = QtCore.QSettings("Retargeter", "OptionsDialog")
            value = ";".join(sorted(self._collect_locked_fields()))
            qs.setValue(_QSETTINGS_LOCK_KEY, value)
        except Exception:
            pass

    def _restore_locks(self) -> None:
        try:
            qs = QtCore.QSettings("Retargeter", "OptionsDialog")
            raw = qs.value(_QSETTINGS_LOCK_KEY, "")
            if raw is None:
                return
            if isinstance(raw, (list, tuple)):
                tokens = [str(x) for x in raw]
            else:
                tokens = [t for t in str(raw).split(";") if t]
        except Exception:
            return
        for field_key in tokens:
            chk = self.chk_lock_field.get(field_key)
            if chk is None:
                continue
            chk.blockSignals(True)
            chk.setChecked(True)
            chk.blockSignals(False)

    def _on_undo_auto(self) -> None:
        if self._snapshot_before_auto is None:
            return
        self._populate_widgets_from(self._snapshot_before_auto)
        self._clear_auto_highlights()
        self.txt_reasons.appendPlainText("\n--- Auto recommendation undone, restored prior values.")
        self.btn_undo_auto.setEnabled(False)
        self._snapshot_before_auto = None
        self._last_advisor_changed = []

    def last_advisor_changed_fields(self) -> List[str]:
        """Public accessor used by main_panel to populate RunConfig."""
        return list(self._last_advisor_changed)

    def _on_diagnose_hik(self) -> None:
        """Dump HIK option resolution info for the current target into reasons."""
        tgt_char = self._resolve_target_character()
        if tgt_char is None:
            self.txt_reasons.setPlainText(
                "Diagnose HIK: no target character resolved.\n"
                "Select source/target on the main panel, then try again."
            )
            return
        try:
            from ..core.retarget_engine import diagnose_hik_options
        except Exception as exc:
            self.txt_reasons.setPlainText(
                f"Diagnose HIK: cannot import retarget_engine ({exc!r})."
            )
            return

        try:
            report = diagnose_hik_options(tgt_char)
        except Exception as exc:
            self.txt_reasons.setPlainText(f"Diagnose HIK: failed ({exc!r}).")
            return

        target_name = getattr(tgt_char, "LongName", "") or "<unnamed>"
        lines = [f"Diagnose HIK on target: {target_name}", ""]
        for key in HIK_OPTION_KEYS:
            info = report.get(key, {})
            exposed = bool(info.get("exposed"))
            via = info.get("via") or "missing"
            name = info.get("name") or "-"
            value = info.get("value")
            marker = "OK" if exposed else "--"
            lines.append(f"[{marker}] {key}")
            lines.append(f"      via={via}  name={name}  value={value!r}")
            note = info.get("note")
            if note:
                lines.append(f"      note: {note}")
            cands = info.get("candidates") or []
            if not exposed and cands:
                shown = ", ".join(cands[:6])
                if len(cands) > 6:
                    shown += f", ... (+{len(cands) - 6} more)"
                lines.append(f"      tried: {shown}")
            lines.append("")

        self.txt_reasons.setPlainText("\n".join(lines))
        self.refresh_hik_indicators()
