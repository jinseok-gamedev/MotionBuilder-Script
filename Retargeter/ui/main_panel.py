"""PySide2 main panel for the retargeter.

Layout (top-to-bottom):

* Source / Target HumanIK character combos + refresh button
* Source FBX list (Add files, Add folder, Remove, Clear)
* Options group (plot rate, root motion, match source, naming, FBX version)
* Action row (Import & Plot, Export Selected, Run All, Dry-run)
* Take table (populated after Import & Plot)
* Output folder picker
* Progress bar
* Log area

The panel does **no** scene mutation directly; everything goes through
:func:`Retargeter.core.pipeline.run`. It listens to the pipeline's progress
callback and the logger sink so it can stream feedback into the UI.
"""

from __future__ import annotations

import os
from typing import List

from ._qt import QtCore, QtWidgets  # type: ignore

from ..core.fbx_io import ExportConfig
from ..core.logger import Logger
from ..core.pipeline import (
    RunConfig,
    RunReport,
    TakePlan,
    load_default_settings,
    run as pipeline_run,
)
from ..core.retarget_engine import PlotConfig
from ..core.root_motion import MODE_EXTRACT, MODE_KEEP, MODE_STRIP
from ..core.scene_utils import list_character_names
from ..core.take_manager import all_take_names
from ._qt_helpers import (
    find_motionbuilder_main_window,
    info_box,
    labeled_row,
    make_separator,
    warning_box,
)
from .take_table import TakeTable


WINDOW_OBJECT_NAME = "RetargeterMainPanel"


class RetargeterPanel(QtWidgets.QWidget):
    """Top-level dock-friendly widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Retargeter")
        # Hard floor: small enough to dock comfortably; the QScrollArea inside
        # _build_ui keeps every widget reachable even when the user resizes
        # below the "ideal" opening size.
        self.setMinimumSize(640, 500)
        self.resize(900, 880)
        self._settings = load_default_settings()

        self._build_ui()
        self._wire_signals()
        self.refresh_characters()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Wrap content in a QScrollArea so shrinking the window scrolls
        instead of clipping / collapsing sub-widgets."""
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_character_group())
        root.addWidget(self._build_files_group())
        root.addWidget(self._build_options_group())
        root.addLayout(self._build_action_row())
        root.addWidget(QtWidgets.QLabel("Takes (populated after Import & Plot):"))
        self.take_table = TakeTable()
        root.addWidget(self.take_table)
        root.addWidget(self._build_take_actions_row())

        root.addWidget(make_separator())
        root.addWidget(self._build_output_row())
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%  %v / %m")
        root.addWidget(self.progress)

        root.addWidget(QtWidgets.QLabel("Log:"))
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(120)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 10pt;")
        root.addWidget(self.log_view, stretch=1)

    def _build_character_group(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("HumanIK Characters")
        v = QtWidgets.QVBoxLayout(g)

        self.cmb_source = QtWidgets.QComboBox()
        self.cmb_target = QtWidgets.QComboBox()
        self.btn_refresh_chars = QtWidgets.QPushButton("Refresh")
        self.btn_refresh_chars.setMaximumWidth(80)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.btn_refresh_chars)
        row.addStretch(1)

        v.addWidget(labeled_row("Source character:", self.cmb_source))
        v.addWidget(labeled_row("Target character:", self.cmb_target))
        v.addLayout(row)
        return g

    def _build_files_group(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Source FBX files")
        v = QtWidgets.QVBoxLayout(g)

        self.list_files = QtWidgets.QListWidget()
        self.list_files.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list_files.setMinimumHeight(110)
        v.addWidget(self.list_files)

        row = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton("Add files...")
        self.btn_add_folder = QtWidgets.QPushButton("Add folder...")
        self.btn_remove_files = QtWidgets.QPushButton("Remove")
        self.btn_clear_files = QtWidgets.QPushButton("Clear")
        for b in (self.btn_add_files, self.btn_add_folder, self.btn_remove_files, self.btn_clear_files):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        return g

    def _build_options_group(self) -> QtWidgets.QGroupBox:
        """Options laid out as four sub-groups in a 2x2 grid for clarity."""
        g = QtWidgets.QGroupBox("Options")
        grid = QtWidgets.QGridLayout(g)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(8, 14, 8, 8)

        plot_cfg = (self._settings.get("plot") or {})
        export_cfg = (self._settings.get("export") or {})
        rm_cfg = (self._settings.get("root_motion") or {})
        naming_cfg = (self._settings.get("naming") or {})
        import_cfg = (self._settings.get("import_options") or {})
        match_cfg = (self._settings.get("match_source") or {})
        meta_cfg = (self._settings.get("metadata") or {})

        grid.addWidget(self._build_plot_subgroup(plot_cfg), 0, 0)
        grid.addWidget(self._build_retarget_subgroup(rm_cfg, match_cfg, import_cfg), 0, 1)
        grid.addWidget(self._build_naming_subgroup(naming_cfg, export_cfg), 1, 0)
        grid.addWidget(self._build_export_subgroup(export_cfg, meta_cfg), 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        return g

    @staticmethod
    def _new_subgroup(title: str) -> QtWidgets.QGroupBox:
        sub = QtWidgets.QGroupBox(title)
        sub.setSizePolicy(
            QtWidgets.QSizePolicy.MinimumExpanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        return sub

    @staticmethod
    def _h_row(label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """One labeled row that lays out as ``[label][widget][stretch]``."""
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        lab = QtWidgets.QLabel(label)
        lab.setMinimumWidth(120)
        lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        h.addWidget(lab)
        h.addWidget(widget, 1)
        return row

    def _build_plot_subgroup(self, plot_cfg: dict) -> QtWidgets.QGroupBox:
        sub = self._new_subgroup("Plot")
        v = QtWidgets.QVBoxLayout(sub)
        v.setContentsMargins(10, 14, 10, 10)
        v.setSpacing(6)

        self.spn_plot_rate = QtWidgets.QSpinBox()
        self.spn_plot_rate.setRange(1, 240)
        self.spn_plot_rate.setValue(int(plot_cfg.get("plot_rate", 30)))
        self.spn_plot_rate.setSuffix(" fps")
        self.spn_plot_rate.setMinimumWidth(100)
        v.addWidget(self._h_row("Plot rate:", self.spn_plot_rate))

        self.chk_plot_translation = QtWidgets.QCheckBox("Plot translation (Hips XYZ)")
        self.chk_plot_translation.setChecked(bool(plot_cfg.get("plot_translation", True)))
        v.addWidget(self.chk_plot_translation)

        self.chk_const_key_reducer = QtWidgets.QCheckBox("Constant key reduction")
        self.chk_const_key_reducer.setChecked(bool(plot_cfg.get("use_constant_key_reducer", False)))
        v.addWidget(self.chk_const_key_reducer)

        v.addStretch(1)
        return sub

    def _build_retarget_subgroup(
        self, rm_cfg: dict, match_cfg: dict, import_cfg: dict
    ) -> QtWidgets.QGroupBox:
        sub = self._new_subgroup("Retargeting")
        v = QtWidgets.QVBoxLayout(sub)
        v.setContentsMargins(10, 14, 10, 10)
        v.setSpacing(6)

        self.chk_match_source = QtWidgets.QCheckBox("HumanIK Match Source")
        self.chk_match_source.setChecked(bool(match_cfg.get("enabled", True)))
        v.addWidget(self.chk_match_source)

        self.chk_clean_takes = QtWidgets.QCheckBox("Remove existing takes before import")
        self.chk_clean_takes.setChecked(bool(import_cfg.get("clean_existing_takes", False)))
        v.addWidget(self.chk_clean_takes)

        self.chk_cleanup_dups = QtWidgets.QCheckBox("Cleanup duplicate bones after merge")
        self.chk_cleanup_dups.setToolTip(
            "When FBX hierarchy differs from the source rig, FileMerge appends ' <N>' "
            "to clashing bone names and the animation lands on those duplicates. "
            "Enable this to transfer the keys back onto the source character bones "
            "and delete the leftover duplicates so plot has data to read."
        )
        self.chk_cleanup_dups.setChecked(
            bool(import_cfg.get("cleanup_duplicate_bones", True))
        )
        v.addWidget(self.chk_cleanup_dups)

        self.cmb_root_motion = QtWidgets.QComboBox()
        self.cmb_root_motion.addItems([MODE_KEEP, MODE_STRIP, MODE_EXTRACT])
        self.cmb_root_motion.setCurrentText(str(rm_cfg.get("default_mode", MODE_KEEP)))
        self.cmb_root_motion.setMinimumWidth(140)
        v.addWidget(self._h_row("Default root motion:", self.cmb_root_motion))

        v.addStretch(1)
        return sub

    def _build_naming_subgroup(self, naming_cfg: dict, export_cfg: dict) -> QtWidgets.QGroupBox:
        sub = self._new_subgroup("Naming")
        v = QtWidgets.QVBoxLayout(sub)
        v.setContentsMargins(10, 14, 10, 10)
        v.setSpacing(6)

        self.txt_take_prefix = QtWidgets.QLineEdit(str(naming_cfg.get("take_prefix", "")))
        self.txt_take_suffix = QtWidgets.QLineEdit(str(naming_cfg.get("take_suffix", "")))
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
        v.addWidget(self._h_row("Take name:", prefix_widget))

        self.txt_filename_template = QtWidgets.QLineEdit(str(export_cfg.get("filename_template", "{take}")))
        self.txt_filename_template.setToolTip("Available placeholders: {take}")
        self.txt_filename_template.setMinimumWidth(160)
        v.addWidget(self._h_row("Filename template:", self.txt_filename_template))

        self.cmb_on_conflict = QtWidgets.QComboBox()
        self.cmb_on_conflict.addItems(["increment", "overwrite", "skip"])
        self.cmb_on_conflict.setCurrentText(str(export_cfg.get("on_conflict", "increment")))
        self.cmb_on_conflict.setMinimumWidth(140)
        v.addWidget(self._h_row("On file conflict:", self.cmb_on_conflict))

        v.addStretch(1)
        return sub

    def _build_export_subgroup(self, export_cfg: dict, meta_cfg: dict) -> QtWidgets.QGroupBox:
        sub = self._new_subgroup("Export")
        v = QtWidgets.QVBoxLayout(sub)
        v.setContentsMargins(10, 14, 10, 10)
        v.setSpacing(6)

        self.cmb_fbx_version = QtWidgets.QComboBox()
        self.cmb_fbx_version.addItems(["FBX202000", "FBX201800", "FBX201600", "FBX201400"])
        self.cmb_fbx_version.setCurrentText(str(export_cfg.get("fbx_version", "FBX201800")))
        self.cmb_fbx_version.setMinimumWidth(160)
        v.addWidget(self._h_row("FBX version:", self.cmb_fbx_version))

        self.cmb_engine = QtWidgets.QComboBox()
        self.cmb_engine.addItems(["ue5", "maya", "max", "generic"])
        self.cmb_engine.setCurrentText(str(self._settings.get("engine_preset", "ue5")))
        self.cmb_engine.setMinimumWidth(160)
        v.addWidget(self._h_row("Engine preset:", self.cmb_engine))

        self.chk_ascii = QtWidgets.QCheckBox("ASCII FBX")
        self.chk_ascii.setChecked(bool(export_cfg.get("ascii", False)))
        v.addWidget(self.chk_ascii)

        self.chk_inject_metadata = QtWidgets.QCheckBox("Inject metadata into FBX")
        self.chk_inject_metadata.setChecked(bool(meta_cfg.get("inject", True)))
        v.addWidget(self.chk_inject_metadata)

        v.addStretch(1)
        return sub

    def _build_action_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        self.btn_import_plot = QtWidgets.QPushButton("Import && Plot")
        self.btn_export = QtWidgets.QPushButton("Export Selected Takes")
        self.btn_run_all = QtWidgets.QPushButton("Run All (Import + Plot + Export)")
        self.btn_dry_run = QtWidgets.QPushButton("Dry-run")
        for b in (self.btn_import_plot, self.btn_export, self.btn_run_all, self.btn_dry_run):
            row.addWidget(b)
        row.addStretch(1)
        self.btn_run_all.setStyleSheet("font-weight: bold;")
        return row

    def _build_take_actions_row(self) -> QtWidgets.QWidget:
        row = QtWidgets.QHBoxLayout()
        self.btn_check_all = QtWidgets.QPushButton("Check all")
        self.btn_uncheck_all = QtWidgets.QPushButton("Uncheck all")
        self.btn_apply_root_motion = QtWidgets.QPushButton("Apply default root motion to all")
        for b in (self.btn_check_all, self.btn_uncheck_all, self.btn_apply_root_motion):
            row.addWidget(b)
        row.addStretch(1)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        return w

    def _build_output_row(self) -> QtWidgets.QWidget:
        row = QtWidgets.QHBoxLayout()
        self.txt_out_dir = QtWidgets.QLineEdit()
        self.txt_out_dir.setPlaceholderText("Output folder for exported FBX files...")
        self.btn_browse_out = QtWidgets.QPushButton("Browse...")
        row.addWidget(QtWidgets.QLabel("Output:"))
        row.addWidget(self.txt_out_dir, stretch=1)
        row.addWidget(self.btn_browse_out)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        return w

    def _wire_signals(self) -> None:
        self.btn_refresh_chars.clicked.connect(self.refresh_characters)
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_remove_files.clicked.connect(self._on_remove_files)
        self.btn_clear_files.clicked.connect(self.list_files.clear)

        self.btn_browse_out.clicked.connect(self._on_browse_out)

        self.btn_import_plot.clicked.connect(lambda: self._run(do_import=True, do_export=False))
        self.btn_export.clicked.connect(lambda: self._run(do_import=False, do_export=True))
        self.btn_run_all.clicked.connect(lambda: self._run(do_import=True, do_export=True))
        self.btn_dry_run.clicked.connect(self._on_dry_run)

        self.btn_check_all.clicked.connect(lambda: self.take_table.set_all_checked(True))
        self.btn_uncheck_all.clicked.connect(lambda: self.take_table.set_all_checked(False))
        self.btn_apply_root_motion.clicked.connect(
            lambda: self.take_table.set_all_root_motion(self.cmb_root_motion.currentText())
        )

    # ------------------------------------------------------------------
    # File list
    # ------------------------------------------------------------------

    def _on_add_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select Source FBX files", "", "FBX (*.fbx)"
        )
        for p in paths:
            self._add_file_to_list(p)

    def _on_add_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder of FBX files")
        if not folder:
            return
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".fbx"):
                self._add_file_to_list(os.path.join(folder, name))

    def _add_file_to_list(self, path: str) -> None:
        existing = {self.list_files.item(i).data(QtCore.Qt.UserRole) for i in range(self.list_files.count())}
        if path in existing:
            return
        item = QtWidgets.QListWidgetItem(os.path.basename(path))
        item.setData(QtCore.Qt.UserRole, path)
        item.setToolTip(path)
        self.list_files.addItem(item)

    def _on_remove_files(self) -> None:
        for item in self.list_files.selectedItems():
            self.list_files.takeItem(self.list_files.row(item))

    def _on_browse_out(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder")
        if d:
            self.txt_out_dir.setText(d)

    # ------------------------------------------------------------------
    # Character refresh
    # ------------------------------------------------------------------

    def refresh_characters(self) -> None:
        try:
            names = list_character_names()
        except Exception as exc:
            self._log_line(f"[ERROR] Could not list HumanIK characters: {exc!r}")
            names = []
        prev_src = self.cmb_source.currentText()
        prev_tgt = self.cmb_target.currentText()
        self.cmb_source.blockSignals(True)
        self.cmb_target.blockSignals(True)
        self.cmb_source.clear()
        self.cmb_target.clear()
        self.cmb_source.addItems(names)
        self.cmb_target.addItems(names)
        if prev_src in names:
            self.cmb_source.setCurrentText(prev_src)
        if prev_tgt in names:
            self.cmb_target.setCurrentText(prev_tgt)
        elif len(names) >= 2:
            self.cmb_target.setCurrentIndex(1)
        self.cmb_source.blockSignals(False)
        self.cmb_target.blockSignals(False)
        self._log_line(f"[INFO] Found {len(names)} HumanIK character(s).")

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _gather_fbx_paths(self) -> List[str]:
        return [
            self.list_files.item(i).data(QtCore.Qt.UserRole)
            for i in range(self.list_files.count())
        ]

    def _build_config(self, *, dry_run: bool = False) -> RunConfig:
        plot = PlotConfig(
            plot_rate=int(self.spn_plot_rate.value()),
            plot_translation=self.chk_plot_translation.isChecked(),
            use_constant_key_reducer=self.chk_const_key_reducer.isChecked(),
        )
        export = ExportConfig(
            fbx_version=self.cmb_fbx_version.currentText(),
            ascii=self.chk_ascii.isChecked(),
        )
        cfg = RunConfig(
            source_character_name=self.cmb_source.currentText(),
            target_character_name=self.cmb_target.currentText(),
            fbx_files=self._gather_fbx_paths(),
            out_dir=self.txt_out_dir.text().strip(),
            plot=plot,
            export=export,
            take_prefix=self.txt_take_prefix.text(),
            take_suffix=self.txt_take_suffix.text(),
            take_filename_template=self.txt_filename_template.text() or "{take}",
            on_conflict=self.cmb_on_conflict.currentText(),
            default_root_motion=self.cmb_root_motion.currentText(),
            match_source=self.chk_match_source.isChecked(),
            clean_existing_takes=self.chk_clean_takes.isChecked(),
            cleanup_duplicate_bones=self.chk_cleanup_dups.isChecked(),
            inject_metadata=self.chk_inject_metadata.isChecked(),
            dry_run=dry_run,
            engine_preset=self.cmb_engine.currentText(),
        )
        cfg.take_plans = self.take_table.collect_plans()
        return cfg

    def _on_dry_run(self) -> None:
        cfg = self._build_config(dry_run=True)
        if not cfg.fbx_files:
            warning_box(self, "Dry-run", "Add at least one source FBX file.")
            return
        self.log_view.clear()
        logger = self._make_logger()
        report = pipeline_run(cfg, logger=logger)
        rows = [(r.take_name, r.source_file) for r in report.results]
        self.take_table.populate_from_takes(rows, default_root_motion=cfg.default_root_motion)
        for r in report.results:
            self.take_table.set_status(r.take_name, "dry-run", tooltip=r.error or "")
        info_box(self, "Dry-run", f"Would process {len(report.results)} take(s).")

    def _run(self, *, do_import: bool, do_export: bool) -> None:
        cfg = self._build_config(dry_run=False)

        if not cfg.source_character_name or not cfg.target_character_name:
            warning_box(self, "Run", "Select both a Source and a Target HumanIK character.")
            return

        if do_import and not cfg.fbx_files:
            warning_box(self, "Run", "Add at least one source FBX file.")
            return

        if do_export and not cfg.out_dir:
            warning_box(self, "Run", "Pick an output folder for exported FBX files.")
            return

        if not do_import:
            cfg.fbx_files = []

        if not do_export:
            cfg.out_dir = ""

        self.log_view.clear()
        self.progress.setValue(0)
        QtWidgets.QApplication.processEvents()

        before_take_names = set(all_take_names())
        logger = self._make_logger()
        report = pipeline_run(cfg, logger=logger, progress_cb=self._on_progress)

        if do_import:
            self._populate_take_table_from_run(report, before_take_names, cfg)
        for r in report.results:
            tooltip = r.error or " | ".join(r.notes)
            self.take_table.set_status(r.take_name, r.status, tooltip=tooltip)

        QtWidgets.QApplication.processEvents()
        if report.ok:
            info_box(self, "Run", "Completed successfully.")
        else:
            warning_box(self, "Run", "Completed with errors. See log for details.")

    def _populate_take_table_from_run(
        self, report: RunReport, before_take_names: set, cfg: RunConfig
    ) -> None:
        rows = []
        existing = {r.take_name for r in self.take_table.collect_plans()}
        for r in report.results:
            if r.take_name in existing:
                continue
            rows.append((r.take_name, r.source_file))
        if rows:
            current_plans = self.take_table.collect_plans()
            self.take_table.clear_rows()
            for plan in current_plans:
                self.take_table.add_row(
                    plan.take_name,
                    plan.source_file,
                    plan.root_motion_mode,
                    plan.export,
                )
            for take_name, source_file in rows:
                self.take_table.add_row(
                    take_name, source_file, cfg.default_root_motion
                )

    # ------------------------------------------------------------------
    # Logger / progress
    # ------------------------------------------------------------------

    def _make_logger(self) -> Logger:
        logger = Logger(also_print=True)
        logger.add_sink(self._log_line)
        return logger

    def _log_line(self, line: str) -> None:
        # NOTE: Do NOT call ``QApplication.processEvents()`` here. Logger lines
        # are emitted very frequently (one per pipeline phase, plus dozens per
        # take during cleanup_duplicate_bones). Draining the Qt queue on every
        # line lets MotionBuilder run its post-FileMerge scene-evaluation
        # events re-entrantly inside our Python code path, which crashes MoBu
        # non-deterministically on heavier rigs (UE Mannequin etc.). Progress
        # feedback is handled separately in ``_on_progress``.
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_progress(self, done: int, total: int, message: str) -> None:
        total = max(1, total)
        self.progress.setMaximum(total)
        self.progress.setValue(min(done, total))
        if message:
            self.progress.setFormat(f"{message}  ({done}/{total})")
        QtWidgets.QApplication.processEvents()


# ----------------------------------------------------------------------------
# Single-instance show helper
# ----------------------------------------------------------------------------


def _close_existing_instances() -> None:
    """Close any prior copy of this panel still alive in the Qt app.

    A module-level singleton variable is NOT reliable here: the menu callback
    in ``install_menus.py`` purges the whole ``Retargeter`` package out of
    ``sys.modules`` before re-importing, which throws away any cached
    reference. The QWidget itself, however, is owned by Qt and survives the
    Python reload, so we find it by enumerating top-level widgets and
    matching on ``objectName``.
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        try:
            if widget.objectName() != WINDOW_OBJECT_NAME:
                continue
        except RuntimeError:
            # Underlying C++ object already deleted - safe to ignore.
            continue
        try:
            widget.close()
            widget.deleteLater()
        except Exception:
            pass


def show_panel() -> RetargeterPanel:
    """Create the panel and return it, enforcing a single live instance.

    Importing this module triggers the Qt binding (PySide6 in MoBu 2025+,
    PySide2 in older versions) which is only valid inside MotionBuilder. If
    the panel is already open from a previous click, that instance is
    closed first so the user always sees exactly one panel.
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])  # MoBu provides one, but guard anyway.

    _close_existing_instances()

    parent = find_motionbuilder_main_window()
    panel = RetargeterPanel(parent=parent)
    if parent is not None:
        panel.setWindowFlags(QtCore.Qt.Tool)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    return panel
