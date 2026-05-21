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
from typing import Callable, Dict, Optional

from ._qt import QtCore, QtGui, QtWidgets  # type: ignore

from ..core.pipeline import load_default_settings
from ..core.root_motion import MODE_EXTRACT, MODE_KEEP, MODE_STRIP


_PAGE_TITLES = ("Plot", "Retargeting", "Naming", "Export", "Presets")
_DEFAULT_SETTINGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "config", "default_settings.json")
)


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

        self._build_ui()
        self._populate_widgets_from(self._settings)
        self._refresh_preset_list()

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

        self.chk_plot_translation = QtWidgets.QCheckBox("Plot translation (Hips XYZ)")
        v.addWidget(self.chk_plot_translation)

        self.chk_const_key_reducer = QtWidgets.QCheckBox("Constant key reduction")
        v.addWidget(self.chk_const_key_reducer)

        v.addStretch(1)
        return page

    def _build_retarget_page(self) -> QtWidgets.QWidget:
        page = self._new_page("Retargeting")
        v = page.layout()

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

        self.cmb_root_motion = QtWidgets.QComboBox()
        self.cmb_root_motion.addItems([MODE_KEEP, MODE_STRIP, MODE_EXTRACT])
        self.cmb_root_motion.setMinimumWidth(140)
        v.addWidget(_h_row("Default root motion:", self.cmb_root_motion))

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
            },
            "import_options": {
                "clean_existing_takes": self.chk_clean_takes.isChecked(),
                "cleanup_duplicate_bones": self.chk_cleanup_dups.isChecked(),
            },
            "match_source": {
                "enabled": self.chk_match_source.isChecked(),
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

        import_cfg = settings.get("import_options") or {}
        self.chk_clean_takes.setChecked(bool(import_cfg.get("clean_existing_takes", False)))
        self.chk_cleanup_dups.setChecked(bool(import_cfg.get("cleanup_duplicate_bones", True)))

        match = settings.get("match_source") or {}
        self.chk_match_source.setChecked(bool(match.get("enabled", True)))

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
