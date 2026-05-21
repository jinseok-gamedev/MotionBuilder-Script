"""Main Retargeter panel.

Top-to-bottom anatomy::

    +-------------------------------------------------------------+
    | Menu bar  (File / Option / Run / Help)                      |
    +-------------------------------------------------------------+
    | Top toolbar (Engine preset, Settings, Open output, Refresh, |
    |              ready-state dot)                               |
    +-------------------------+-----------------------------------+
    | LEFT SETUP COLUMN       | RIGHT RUN & REVIEW COLUMN         |
    |                         |                                   |
    | - HumanIK Characters    | - Takes (filter / table)          |
    |   src / arrow / tgt     | - Combined action row:            |
    | - Source FBX files      |     [Check all][Uncheck][Apply..] |
    |   (drag&drop list +     |     ... [Dry-run][Import & Plot]  |
    |    files count)         |     [Export][Run All*][Cancel]    |
    | - Output folder         | - Progress bar (full width)       |
    |                         | - Log panel                       |
    +-------------------------+-----------------------------------+

The panel does **no** scene mutation directly; everything goes through
:func:`Retargeter.core.pipeline.run`. Per-run options live in
:class:`OptionsDialog`; only the engine preset is surfaced to the toolbar
because it is the single most frequently changed control. The current
take name appears inside the progress bar text itself.
"""

from __future__ import annotations

import os
from typing import List

from ._qt import QtCore, QtGui, QtWidgets  # type: ignore

from ..core.fbx_io import ExportConfig
from ..core.logger import Logger
from ..core.pipeline import (
    RunConfig,
    RunReport,
    load_default_settings,
    run as pipeline_run,
)
from ..core.retarget_engine import PlotConfig
from ..core.scene_utils import list_character_names
from ..core.take_manager import all_take_names
from ._qt_helpers import (
    find_motionbuilder_main_window,
    human_readable_size,
    info_box,
    make_separator,
    make_tool_button,
    open_in_file_explorer,
    standard_icon,
    warning_box,
)
from .file_list import FbxDropList
from .log_view import ColoredLogView
from .options_dialog import OptionsDialog
from .take_table import TakeTable


WINDOW_OBJECT_NAME = "RetargeterMainPanel"

_QSETTINGS_ORG = "Retargeter"
_QSETTINGS_APP = "Retargeter"

_RUN_ALL_STYLE = (
    "QPushButton { background-color: #2e7d32; color: white;"
    " padding: 6px 14px; font-weight: bold; border-radius: 3px; }"
    "QPushButton:hover { background-color: #388e3c; }"
    "QPushButton:disabled { background-color: #555; color: #bbb; }"
)
_CANCEL_STYLE = (
    "QPushButton { background-color: #c62828; color: white;"
    " padding: 6px 14px; font-weight: bold; border-radius: 3px; }"
    "QPushButton:hover { background-color: #d32f2f; }"
    "QPushButton:disabled { background-color: #555; color: #bbb; }"
)


class RetargeterPanel(QtWidgets.QWidget):
    """Top-level dock-friendly widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Retargeter")
        # Sized so the screenshot-reference layout (3-column setup column
        # ~ 350 px, right column with takes table + log) never collapses
        # below a usable proportion.
        self.setMinimumSize(1124, 700)
        self.resize(1300, 800)

        self._settings = load_default_settings()
        self._qsettings = QtCore.QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        self._cancel_requested = False
        self._is_running = False
        self._last_run_out_dir = ""

        self._build_ui()
        self._wire_signals()
        self._restore_state()
        self.refresh_characters()
        self._update_run_buttons_enabled()
        self._update_swap_warning()
        self._update_file_count_label()

    # ==================================================================
    # UI CONSTRUCTION
    # ==================================================================

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The options dialog owns the option widgets; everything else on the
        # panel references them via ``self._options_dialog.<widget>``.
        self._options_dialog = OptionsDialog(
            self, settings=self._settings, qsettings=self._qsettings
        )
        # Engine preset lives on the toolbar (cmb_engine, created shortly).
        # ``extras_provider`` lets "Save as Default" persist it alongside the
        # dialog's own values.
        self._options_dialog.extras_provider = lambda: {
            "engine_preset": self.cmb_engine.currentText()
        }

        outer.addWidget(self._build_menu_bar())
        outer.addWidget(self._build_top_toolbar())
        outer.addWidget(make_separator())

        # Main split: left setup column / right run-and-review column.
        self.splitter_main = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter_main.setChildrenCollapsible(False)
        self.splitter_main.addWidget(self._build_left_setup_column())
        self.splitter_main.addWidget(self._build_right_review_column())
        self.splitter_main.setStretchFactor(0, 0)
        self.splitter_main.setStretchFactor(1, 1)
        self.splitter_main.setSizes([350, 670])
        outer.addWidget(self.splitter_main, stretch=1)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu_bar(self) -> QtWidgets.QMenuBar:
        bar = QtWidgets.QMenuBar(self)
        bar.setNativeMenuBar(False)

        file_menu = bar.addMenu("File")
        self.act_add_files = file_menu.addAction("Add FBX files...")
        self.act_add_files.setShortcut(QtGui.QKeySequence("Ctrl+O"))
        self.act_add_folder = file_menu.addAction("Add folder...")
        self.act_add_folder.setShortcut(QtGui.QKeySequence("Ctrl+Shift+O"))
        file_menu.addSeparator()
        self.act_remove_files = file_menu.addAction("Remove selected")
        self.act_remove_files.setShortcut(QtGui.QKeySequence("Delete"))
        self.act_clear_files = file_menu.addAction("Clear list")
        self.act_clear_files.setShortcut(QtGui.QKeySequence("Ctrl+L"))
        file_menu.addSeparator()
        self.act_browse_out = file_menu.addAction("Set output folder...")
        self.act_open_output = file_menu.addAction("Open output folder")
        file_menu.addSeparator()
        self.act_close_panel = file_menu.addAction("Close panel")
        self.act_close_panel.setShortcut(QtGui.QKeySequence("Ctrl+W"))

        option_menu = bar.addMenu("Option")
        self.act_open_settings = option_menu.addAction("Settings...")
        self.act_open_settings.setShortcut(QtGui.QKeySequence("Ctrl+,"))
        self.act_refresh_chars = option_menu.addAction("Refresh HumanIK characters")
        self.act_refresh_chars.setShortcut(QtGui.QKeySequence("F5"))
        self.act_swap_chars = option_menu.addAction("Swap Source / Target")

        run_menu = bar.addMenu("Run")
        self.act_run_all = run_menu.addAction("Run All")
        self.act_run_all.setShortcut(QtGui.QKeySequence("Ctrl+R"))
        self.act_import_plot = run_menu.addAction("Import && Plot only")
        self.act_import_plot.setShortcut(QtGui.QKeySequence("Ctrl+P"))
        self.act_export_selected = run_menu.addAction("Export selected takes")
        self.act_export_selected.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        self.act_dry_run = run_menu.addAction("Dry-run")
        self.act_dry_run.setShortcut(QtGui.QKeySequence("Ctrl+D"))
        run_menu.addSeparator()
        self.act_cancel = run_menu.addAction("Cancel running")
        self.act_cancel.setShortcut(QtGui.QKeySequence("Esc"))

        help_menu = bar.addMenu("Help")
        self.act_open_readme = help_menu.addAction("Open README")
        help_menu.addSeparator()
        self.act_about = help_menu.addAction("About Retargeter")
        return bar

    # ------------------------------------------------------------------
    # Top toolbar
    # ------------------------------------------------------------------

    def _build_top_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(6)

        # Engine preset combo: the single most frequently changed option.
        h.addWidget(QtWidgets.QLabel("Engine:"))
        self.cmb_engine = QtWidgets.QComboBox()
        self.cmb_engine.addItems(["ue5", "maya", "max", "generic"])
        self.cmb_engine.setCurrentText(
            str(self._settings.get("engine_preset", "ue5"))
        )
        self.cmb_engine.setMinimumWidth(110)
        h.addWidget(self.cmb_engine)

        h.addSpacing(8)
        self.btn_settings = make_tool_button(
            standard_icon(self, QtWidgets.QStyle.SP_FileDialogDetailedView),
            "Open Options dialog (Ctrl+,)",
            self,
            text="Settings",
        )
        h.addWidget(self.btn_settings)

        self.btn_open_output = make_tool_button(
            standard_icon(self, QtWidgets.QStyle.SP_DirOpenIcon),
            "Open the current output folder in the file explorer",
            self,
            text="Open Output",
        )
        h.addWidget(self.btn_open_output)

        self.btn_refresh_chars = make_tool_button(
            standard_icon(self, QtWidgets.QStyle.SP_BrowserReload),
            "Refresh the HumanIK character list (F5)",
            self,
            text="Refresh",
        )
        h.addWidget(self.btn_refresh_chars)

        h.addStretch(1)

        # Ready-state indicator (dot + tooltip with the missing prerequisite).
        self.lbl_ready = QtWidgets.QLabel("not ready")
        self.lbl_ready.setStyleSheet("color: #fb8c00; font-weight: bold;")
        h.addWidget(self.lbl_ready)
        return bar

    # ------------------------------------------------------------------
    # Left setup column
    # ------------------------------------------------------------------

    def _build_left_setup_column(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QScrollArea()
        wrap.setWidgetResizable(True)
        wrap.setFrameShape(QtWidgets.QFrame.NoFrame)
        wrap.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        content = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addWidget(self._build_character_group())
        v.addWidget(self._build_files_group(), stretch=1)
        v.addWidget(self._build_output_group())
        v.addStretch(0)
        wrap.setWidget(content)
        return wrap

    def _build_character_group(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("HumanIK Characters")
        v = QtWidgets.QVBoxLayout(g)
        v.setContentsMargins(8, 14, 8, 8)
        v.setSpacing(6)

        self.cmb_source = QtWidgets.QComboBox()
        self.cmb_target = QtWidgets.QComboBox()
        self.cmb_source.setMinimumWidth(160)
        self.cmb_target.setMinimumWidth(160)

        src_row = QtWidgets.QHBoxLayout()
        src_row.setContentsMargins(0, 0, 0, 0)
        src_row.setSpacing(6)
        src_lbl = QtWidgets.QLabel("Source:")
        src_lbl.setMinimumWidth(56)
        src_row.addWidget(src_lbl)
        src_row.addWidget(self.cmb_source, 1)
        v.addLayout(src_row)

        # Decorative arrow only (no click behaviour). Swap is reachable via
        # Option > Swap Source / Target in the menu bar.
        swap_row = QtWidgets.QHBoxLayout()
        swap_row.addStretch(1)
        self.lbl_swap_arrow = QtWidgets.QLabel()
        arrow_icon = standard_icon(self, QtWidgets.QStyle.SP_ArrowDown)
        self.lbl_swap_arrow.setPixmap(arrow_icon.pixmap(20, 20))
        self.lbl_swap_arrow.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_swap_arrow.setToolTip(
            "Source / Target order (use Option > Swap Source / Target to swap)"
        )
        swap_row.addWidget(self.lbl_swap_arrow)
        swap_row.addStretch(1)
        v.addLayout(swap_row)

        tgt_row = QtWidgets.QHBoxLayout()
        tgt_row.setContentsMargins(0, 0, 0, 0)
        tgt_row.setSpacing(6)
        tgt_lbl = QtWidgets.QLabel("Target:")
        tgt_lbl.setMinimumWidth(56)
        tgt_row.addWidget(tgt_lbl)
        tgt_row.addWidget(self.cmb_target, 1)
        v.addLayout(tgt_row)

        self.lbl_swap_warning = QtWidgets.QLabel("")
        self.lbl_swap_warning.setStyleSheet("color: #fb8c00;")
        self.lbl_swap_warning.setWordWrap(True)
        v.addWidget(self.lbl_swap_warning)
        return g

    def _build_files_group(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Source FBX files")
        v = QtWidgets.QVBoxLayout(g)
        v.setContentsMargins(8, 14, 8, 8)
        v.setSpacing(6)

        hint = QtWidgets.QLabel(
            "Drag FBX files or folders here, or use the buttons below."
        )
        hint.setStyleSheet("color: #888; font-style: italic;")
        v.addWidget(hint)

        self.list_files = FbxDropList()
        v.addWidget(self.list_files, stretch=1)

        self.lbl_file_count = QtWidgets.QLabel("0 files")
        self.lbl_file_count.setStyleSheet("color: #888;")
        v.addWidget(self.lbl_file_count)

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

    def _build_output_group(self) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox("Output folder")
        v = QtWidgets.QVBoxLayout(g)
        v.setContentsMargins(8, 14, 8, 8)
        v.setSpacing(6)

        row = QtWidgets.QHBoxLayout()
        self.txt_out_dir = QtWidgets.QLineEdit()
        self.txt_out_dir.setPlaceholderText("Folder where exported FBX files will be written...")
        row.addWidget(self.txt_out_dir, stretch=1)
        self.btn_browse_out = QtWidgets.QPushButton("Browse...")
        row.addWidget(self.btn_browse_out)
        self.btn_open_out_inline = make_tool_button(
            standard_icon(self, QtWidgets.QStyle.SP_DirOpenIcon),
            "Open this folder in the file explorer",
            self,
        )
        row.addWidget(self.btn_open_out_inline)
        v.addLayout(row)
        return g

    # ------------------------------------------------------------------
    # Right run-and-review column
    # ------------------------------------------------------------------

    def _build_right_review_column(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Vertical splitter: take table on top, log panel on bottom.
        self.splitter_right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.splitter_right.setChildrenCollapsible(False)

        takes_box = QtWidgets.QWidget()
        takes_layout = QtWidgets.QVBoxLayout(takes_box)
        takes_layout.setContentsMargins(0, 0, 0, 0)
        takes_layout.setSpacing(4)
        header = QtWidgets.QLabel("<b>Takes</b>")
        takes_layout.addWidget(header)
        self.take_table = TakeTable()
        takes_layout.addWidget(self.take_table, stretch=1)
        takes_layout.addWidget(self._build_take_actions_row())
        takes_layout.addWidget(self._build_progress_row())
        self.splitter_right.addWidget(takes_box)

        log_box = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)

        log_header = QtWidgets.QHBoxLayout()
        log_header.setContentsMargins(0, 0, 0, 0)
        log_header.addWidget(QtWidgets.QLabel("<b>Log</b>"))
        log_header.addStretch(1)
        self.btn_clear_log = QtWidgets.QToolButton()
        self.btn_clear_log.setText("Clear")
        self.btn_clear_log.setAutoRaise(True)
        log_header.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_header)

        self.log_view = ColoredLogView()
        log_layout.addWidget(self.log_view, stretch=1)
        self.splitter_right.addWidget(log_box)

        self.splitter_right.setStretchFactor(0, 3)
        self.splitter_right.setStretchFactor(1, 1)
        self.splitter_right.setSizes([420, 180])

        v.addWidget(self.splitter_right, stretch=1)
        return wrap

    def _build_take_actions_row(self) -> QtWidgets.QWidget:
        """Single combined row: selection actions on the left, run actions on the right."""
        w = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.btn_check_all = QtWidgets.QPushButton("Check all")
        self.btn_uncheck_all = QtWidgets.QPushButton("Uncheck all")
        self.btn_apply_root_motion = QtWidgets.QPushButton("Apply default root motion to all")
        for b in (self.btn_check_all, self.btn_uncheck_all, self.btn_apply_root_motion):
            row.addWidget(b)

        row.addStretch(1)

        self.btn_dry_run = QtWidgets.QPushButton("Dry-run")
        self.btn_dry_run.setToolTip(
            "Simulate the run: report which takes would be created and exported,"
            " without touching the scene."
        )
        self.btn_import_plot = QtWidgets.QPushButton("Import && Plot")
        self.btn_export = QtWidgets.QPushButton("Export Selected")
        self.btn_run_all = QtWidgets.QPushButton("Run All")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")

        self.btn_run_all.setStyleSheet(_RUN_ALL_STYLE)
        self.btn_cancel.setStyleSheet(_CANCEL_STYLE)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setToolTip(
            "Cancel after the current take finishes. Most responsive during"
            " the Export phase (Import/Plot phases process events less often)."
        )

        for b in (self.btn_dry_run, self.btn_import_plot, self.btn_export,
                  self.btn_run_all, self.btn_cancel):
            row.addWidget(b)
        return w

    def _build_progress_row(self) -> QtWidgets.QWidget:
        """Progress bar fills the entire row; current-take info is rendered
        inside the bar via :meth:`_on_progress`."""
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%  %v / %m")
        self.progress.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        h.addWidget(self.progress)
        return w

    # ==================================================================
    # SIGNAL WIRING
    # ==================================================================

    def _wire_signals(self) -> None:
        # Toolbar
        self.btn_settings.clicked.connect(self._show_options_dialog)
        self.btn_open_output.clicked.connect(self._on_open_output_folder)
        self.btn_refresh_chars.clicked.connect(self.refresh_characters)

        # Characters
        self.cmb_source.currentTextChanged.connect(self._on_chars_changed)
        self.cmb_target.currentTextChanged.connect(self._on_chars_changed)

        # File list
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_remove_files.clicked.connect(self.list_files.remove_selected)
        self.btn_clear_files.clicked.connect(self.list_files.clear_all)
        self.list_files.filesChanged.connect(self._on_files_changed)

        # Output folder
        self.btn_browse_out.clicked.connect(self._on_browse_out)
        self.btn_open_out_inline.clicked.connect(self._on_open_output_folder)
        self.txt_out_dir.textChanged.connect(self._update_run_buttons_enabled)

        # Bottom actions
        self.btn_import_plot.clicked.connect(lambda: self._run(do_import=True, do_export=False))
        self.btn_export.clicked.connect(lambda: self._run(do_import=False, do_export=True))
        self.btn_run_all.clicked.connect(lambda: self._run(do_import=True, do_export=True))
        self.btn_dry_run.clicked.connect(self._on_dry_run)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        # Take actions
        self.btn_check_all.clicked.connect(lambda: self.take_table.set_all_checked(True))
        self.btn_uncheck_all.clicked.connect(lambda: self.take_table.set_all_checked(False))
        self.btn_apply_root_motion.clicked.connect(self._on_apply_root_motion_all)

        # Log panel
        self.btn_clear_log.clicked.connect(self.log_view.clear_log)

        # Menu bar
        self.act_add_files.triggered.connect(self._on_add_files)
        self.act_add_folder.triggered.connect(self._on_add_folder)
        self.act_remove_files.triggered.connect(self.list_files.remove_selected)
        self.act_clear_files.triggered.connect(self.list_files.clear_all)
        self.act_browse_out.triggered.connect(self._on_browse_out)
        self.act_open_output.triggered.connect(self._on_open_output_folder)
        self.act_close_panel.triggered.connect(self.close)
        self.act_open_settings.triggered.connect(self._show_options_dialog)
        self.act_refresh_chars.triggered.connect(self.refresh_characters)
        self.act_swap_chars.triggered.connect(self._on_swap_chars)
        self.act_run_all.triggered.connect(lambda: self._run(do_import=True, do_export=True))
        self.act_import_plot.triggered.connect(lambda: self._run(do_import=True, do_export=False))
        self.act_export_selected.triggered.connect(lambda: self._run(do_import=False, do_export=True))
        self.act_dry_run.triggered.connect(self._on_dry_run)
        self.act_cancel.triggered.connect(self._on_cancel_clicked)
        self.act_open_readme.triggered.connect(self._on_open_readme)
        self.act_about.triggered.connect(self._on_about)

        # Options dialog
        self._options_dialog.settingsSavedAsDefault.connect(self._on_defaults_saved)

    # ==================================================================
    # Options dialog show
    # ==================================================================

    def _show_options_dialog(self) -> None:
        self._options_dialog.show()
        self._options_dialog.raise_()
        self._options_dialog.activateWindow()

    def _on_defaults_saved(self) -> None:
        self._log_line("[INFO] Default settings file updated.")

    # ==================================================================
    # File list handlers
    # ==================================================================

    def _on_add_files(self) -> None:
        start = self._qsettings.value("Paths/lastFbxDir", "", type=str)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select Source FBX files", start, "FBX (*.fbx)"
        )
        if not paths:
            return
        self.list_files.add_paths(paths)
        self._qsettings.setValue("Paths/lastFbxDir", os.path.dirname(paths[0]))

    def _on_add_folder(self) -> None:
        start = self._qsettings.value("Paths/lastFolderDir", "", type=str)
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select folder of FBX files", start
        )
        if not folder:
            return
        collected = []
        try:
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith(".fbx"):
                    collected.append(os.path.join(folder, name))
        except OSError as exc:
            warning_box(self, "Add folder", f"Could not read folder:\n{exc!r}")
            return
        if not collected:
            warning_box(self, "Add folder", "No .fbx files found in that folder.")
            return
        self.list_files.add_paths(collected)
        self._qsettings.setValue("Paths/lastFolderDir", folder)

    def _on_browse_out(self) -> None:
        start = self.txt_out_dir.text().strip() or self._qsettings.value(
            "Paths/lastOutDir", "", type=str
        )
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if d:
            self.txt_out_dir.setText(d)
            self._qsettings.setValue("Paths/lastOutDir", d)

    def _on_open_output_folder(self) -> None:
        path = self.txt_out_dir.text().strip() or self._last_run_out_dir
        if not path:
            warning_box(self, "Open output folder", "No output folder set yet.")
            return
        if not os.path.isdir(path):
            warning_box(self, "Open output folder", f"Folder does not exist:\n{path}")
            return
        if not open_in_file_explorer(path):
            warning_box(self, "Open output folder", f"Could not open:\n{path}")

    def _on_files_changed(self) -> None:
        self._update_file_count_label()
        self._update_run_buttons_enabled()

    def _update_file_count_label(self) -> None:
        count = self.list_files.count()
        if count == 0:
            self.lbl_file_count.setText("0 files")
            return
        size_text = human_readable_size(self.list_files.total_size_bytes())
        self.lbl_file_count.setText(f"{count} file(s) - {size_text} total")

    # ==================================================================
    # Help menu handlers
    # ==================================================================

    def _on_open_readme(self) -> None:
        readme_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), os.pardir, "README.md")
        )
        if not os.path.isfile(readme_path):
            warning_box(self, "Open README", f"README not found:\n{readme_path}")
            return
        url = QtCore.QUrl.fromLocalFile(readme_path)
        if not QtGui.QDesktopServices.openUrl(url):
            warning_box(self, "Open README", f"Could not open:\n{readme_path}")

    def _on_about(self) -> None:
        info_box(
            self,
            "About Retargeter",
            "Retargeter\n\n"
            "MotionBuilder HumanIK retargeting hub.\n"
            "See Help > Open README for usage details.",
        )

    # ==================================================================
    # Characters
    # ==================================================================

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
        self._on_chars_changed()

    def _on_chars_changed(self, *_args) -> None:
        self._update_swap_warning()
        self._update_run_buttons_enabled()

    def _on_swap_chars(self) -> None:
        src = self.cmb_source.currentText()
        tgt = self.cmb_target.currentText()
        if not src and not tgt:
            return
        self.cmb_source.blockSignals(True)
        self.cmb_target.blockSignals(True)
        if tgt:
            self.cmb_source.setCurrentText(tgt)
        if src:
            self.cmb_target.setCurrentText(src)
        self.cmb_source.blockSignals(False)
        self.cmb_target.blockSignals(False)
        self._on_chars_changed()

    def _update_swap_warning(self) -> None:
        src = self.cmb_source.currentText()
        tgt = self.cmb_target.currentText()
        if src and tgt and src == tgt:
            self.lbl_swap_warning.setText(
                "Source and Target are the same character; retarget will be a no-op."
            )
        else:
            self.lbl_swap_warning.clear()

    # ==================================================================
    # Run prerequisites + button gating
    # ==================================================================

    def _missing_prerequisites(self, *, do_import: bool, do_export: bool):
        """Return a list of human-readable missing prerequisites."""
        missing = []
        if not self.cmb_source.currentText():
            missing.append("a Source HumanIK character")
        if not self.cmb_target.currentText():
            missing.append("a Target HumanIK character")
        if do_import and self.list_files.count() == 0:
            missing.append("at least one source FBX file")
        if do_export and not self.txt_out_dir.text().strip():
            missing.append("an output folder")
        return missing

    def _update_run_buttons_enabled(self) -> None:
        if self._is_running:
            for b in (self.btn_run_all, self.btn_import_plot, self.btn_export,
                      self.btn_dry_run):
                b.setEnabled(False)
            self.lbl_ready.setText("running...")
            self.lbl_ready.setStyleSheet("color: #1e88e5; font-weight: bold;")
            return

        run_all_missing = self._missing_prerequisites(do_import=True, do_export=True)
        import_missing = self._missing_prerequisites(do_import=True, do_export=False)
        export_missing = self._missing_prerequisites(do_import=False, do_export=True)
        dry_missing = self._missing_prerequisites(do_import=True, do_export=False)

        self.btn_run_all.setEnabled(not run_all_missing)
        self.btn_import_plot.setEnabled(not import_missing)
        self.btn_export.setEnabled(not export_missing)
        self.btn_dry_run.setEnabled(not dry_missing)

        self.btn_run_all.setToolTip(
            "Run import, plot and export in one pass."
            if not run_all_missing
            else "Missing: " + ", ".join(run_all_missing)
        )
        self.btn_import_plot.setToolTip(
            "Import source FBX files and plot onto the target rig."
            if not import_missing
            else "Missing: " + ", ".join(import_missing)
        )
        self.btn_export.setToolTip(
            "Export the currently checked takes to FBX."
            if not export_missing
            else "Missing: " + ", ".join(export_missing)
        )
        self.btn_dry_run.setToolTip(
            "Simulate the run without touching the scene."
            if not dry_missing
            else "Missing: " + ", ".join(dry_missing)
        )

        if not run_all_missing:
            self.lbl_ready.setText("ready")
            self.lbl_ready.setStyleSheet("color: #4caf50; font-weight: bold;")
        else:
            self.lbl_ready.setText("not ready")
            self.lbl_ready.setStyleSheet("color: #fb8c00; font-weight: bold;")
            self.lbl_ready.setToolTip("Missing: " + ", ".join(run_all_missing))

    # ==================================================================
    # Run / dry-run
    # ==================================================================

    def _gather_fbx_paths(self) -> List[str]:
        return self.list_files.all_paths()

    def _opt(self):
        """Shorthand: the options dialog that owns per-run option widgets."""
        return self._options_dialog

    def _build_config(self, *, dry_run: bool = False) -> RunConfig:
        opt = self._opt()
        plot = PlotConfig(
            plot_rate=int(opt.spn_plot_rate.value()),
            plot_translation=opt.chk_plot_translation.isChecked(),
            use_constant_key_reducer=opt.chk_const_key_reducer.isChecked(),
        )
        export = ExportConfig(
            fbx_version=opt.cmb_fbx_version.currentText(),
            ascii=opt.chk_ascii.isChecked(),
        )
        cfg = RunConfig(
            source_character_name=self.cmb_source.currentText(),
            target_character_name=self.cmb_target.currentText(),
            fbx_files=self._gather_fbx_paths(),
            out_dir=self.txt_out_dir.text().strip(),
            plot=plot,
            export=export,
            take_prefix=opt.txt_take_prefix.text(),
            take_suffix=opt.txt_take_suffix.text(),
            take_filename_template=opt.txt_filename_template.text() or "{take}",
            on_conflict=opt.cmb_on_conflict.currentText(),
            default_root_motion=opt.cmb_root_motion.currentText(),
            match_source=opt.chk_match_source.isChecked(),
            clean_existing_takes=opt.chk_clean_takes.isChecked(),
            cleanup_duplicate_bones=opt.chk_cleanup_dups.isChecked(),
            inject_metadata=opt.chk_inject_metadata.isChecked(),
            dry_run=dry_run,
            engine_preset=self.cmb_engine.currentText(),
        )
        cfg.take_plans = self.take_table.collect_plans()
        return cfg

    def _on_apply_root_motion_all(self) -> None:
        mode = self._opt().cmb_root_motion.currentText()
        self.take_table.set_all_root_motion(mode)

    def _on_dry_run(self) -> None:
        cfg = self._build_config(dry_run=True)
        if not cfg.fbx_files:
            warning_box(self, "Dry-run", "Add at least one source FBX file.")
            return
        self.log_view.clear_log()
        logger = self._make_logger()
        report = pipeline_run(cfg, logger=logger)
        rows = [(r.take_name, r.source_file) for r in report.results]
        self.take_table.populate_from_takes(rows, default_root_motion=cfg.default_root_motion)
        for r in report.results:
            self.take_table.set_status(r.take_name, "dry-run", tooltip=r.error or "")
        info_box(self, "Dry-run", f"Would process {len(report.results)} take(s).")

    def _run(self, *, do_import: bool, do_export: bool) -> None:
        if self._is_running:
            return
        missing = self._missing_prerequisites(do_import=do_import, do_export=do_export)
        if missing:
            warning_box(self, "Run", "Missing prerequisites:\n - " + "\n - ".join(missing))
            return

        cfg = self._build_config(dry_run=False)
        if not do_import:
            cfg.fbx_files = []
        if not do_export:
            cfg.out_dir = ""

        self._enter_running_state()
        self.log_view.clear_log()
        self.progress.setValue(0)
        self.progress.setFormat("%p%  %v / %m")
        QtWidgets.QApplication.processEvents()

        before_take_names = set(all_take_names())
        logger = self._make_logger()
        try:
            report = pipeline_run(
                cfg,
                logger=logger,
                progress_cb=self._on_progress,
                cancel_check=self._is_cancel_requested,
            )
        finally:
            self._leave_running_state()

        if do_import:
            self._populate_take_table_from_run(report, before_take_names, cfg)
        for r in report.results:
            tooltip = r.error or " | ".join(r.notes)
            self.take_table.set_status(r.take_name, r.status, tooltip=tooltip)

        if cfg.out_dir:
            self._last_run_out_dir = cfg.out_dir

        QtWidgets.QApplication.processEvents()

        if self._cancel_requested:
            warning_box(self, "Run", "Cancelled by user. Partial results are shown.")
        elif report.ok:
            self._show_success_box(cfg.out_dir if do_export else "")
        else:
            warning_box(self, "Run", "Completed with errors. See log for details.")

    def _show_success_box(self, out_dir: str) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle("Run")
        box.setText("Completed successfully.")
        if out_dir and os.path.isdir(out_dir):
            box.setInformativeText(f"Output folder:\n{out_dir}")
            open_btn = box.addButton("Open output folder", QtWidgets.QMessageBox.AcceptRole)
            box.addButton(QtWidgets.QMessageBox.Ok)
            box.exec_()
            if box.clickedButton() is open_btn:
                open_in_file_explorer(out_dir)
        else:
            box.exec_()

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
    # Run state transitions
    # ------------------------------------------------------------------

    def _enter_running_state(self) -> None:
        self._is_running = True
        self._cancel_requested = False
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("Cancel")
        self._update_run_buttons_enabled()

    def _leave_running_state(self) -> None:
        self._is_running = False
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancel")
        self._update_run_buttons_enabled()

    def _on_cancel_clicked(self) -> None:
        if not self._is_running:
            return
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelling...")
        self._log_line("[WARN] Cancellation requested; stopping after current take.")

    def _is_cancel_requested(self) -> bool:
        return self._cancel_requested

    # ==================================================================
    # Logger / progress
    # ==================================================================

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
        self.log_view.append_line(line)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        total = max(1, total)
        self.progress.setMaximum(total)
        self.progress.setValue(min(done, total))
        if message:
            self.progress.setFormat(f"{message}  ({done}/{total})")
        QtWidgets.QApplication.processEvents()

    # ==================================================================
    # State persistence (QSettings)
    # ==================================================================

    def _restore_state(self) -> None:
        qs = self._qsettings
        geo = qs.value("Window/geometry", None)
        if geo:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        main_sizes = qs.value("Window/mainSplitter", None)
        if main_sizes:
            try:
                self.splitter_main.setSizes([int(s) for s in main_sizes])
            except Exception:
                pass
        right_sizes = qs.value("Window/rightSplitter", None)
        if right_sizes:
            try:
                self.splitter_right.setSizes([int(s) for s in right_sizes])
            except Exception:
                pass
        out_dir = qs.value("Paths/outDir", "", type=str)
        if out_dir:
            self.txt_out_dir.setText(out_dir)
        engine = qs.value("Run/enginePreset", "", type=str)
        if engine:
            idx = self.cmb_engine.findText(engine)
            if idx >= 0:
                self.cmb_engine.setCurrentIndex(idx)

    def _persist_state(self) -> None:
        qs = self._qsettings
        qs.setValue("Window/geometry", self.saveGeometry())
        qs.setValue("Window/mainSplitter", self.splitter_main.sizes())
        qs.setValue("Window/rightSplitter", self.splitter_right.sizes())
        qs.setValue("Paths/outDir", self.txt_out_dir.text().strip())
        qs.setValue("Run/enginePreset", self.cmb_engine.currentText())
        qs.setValue("Run/lastSourceChar", self.cmb_source.currentText())
        qs.setValue("Run/lastTargetChar", self.cmb_target.currentText())
        qs.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._persist_state()
        except Exception:
            pass
        # Close the modeless options dialog too so it doesn't dangle.
        try:
            self._options_dialog.close()
        except Exception:
            pass
        super().closeEvent(event)


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
            continue
        try:
            widget.close()
            widget.deleteLater()
        except Exception:
            pass


def show_panel() -> RetargeterPanel:
    """Create the panel and return it, enforcing a single live instance."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])

    _close_existing_instances()

    parent = find_motionbuilder_main_window()
    panel = RetargeterPanel(parent=parent)
    if parent is not None:
        panel.setWindowFlags(QtCore.Qt.Tool)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    return panel
