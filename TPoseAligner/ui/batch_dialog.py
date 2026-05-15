"""Batch retargeting dialog (PySide2 / PySide6)."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from ..batch.batch_retarget import (
    BatchFileResult,
    BatchReport,
    NamingConvention,
    batch_retarget,
)
from ..core.tpose_align import AlignOptions


_dialog_instance: Optional["BatchDialog"] = None


class BatchDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TPoseAligner - Batch Retarget")
        self.resize(720, 540)
        self.setSizeGripEnabled(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        path_box = QtWidgets.QGroupBox("Paths")
        path_form = QtWidgets.QFormLayout(path_box)
        self.target_edit = self._make_path_field("Choose target FBX...", file_mode="open_file")
        self.source_edit = self._make_path_field("Choose source folder...", file_mode="dir")
        self.output_edit = self._make_path_field("Choose output folder...", file_mode="dir")
        path_form.addRow("Target FBX:", self.target_edit[0])
        path_form.addRow("Source folder:", self.source_edit[0])
        path_form.addRow("Output folder:", self.output_edit[0])
        layout.addWidget(path_box)

        opts_box = QtWidgets.QGroupBox("Alignment options")
        opts_grid = QtWidgets.QGridLayout(opts_box)
        self.opt_clear = QtWidgets.QCheckBox("Clear existing offsets")
        self.opt_clear.setChecked(True)
        self.opt_micro_bend = QtWidgets.QCheckBox("Preserve micro bend")
        self.opt_micro_bend.setChecked(True)
        self.opt_palms = QtWidgets.QCheckBox("Palms down")
        self.opt_palms.setChecked(True)
        self.opt_feet = QtWidgets.QCheckBox("Feet flat / forward")
        self.opt_feet.setChecked(True)
        self.opt_fingers = QtWidgets.QCheckBox("Include fingers")
        self.opt_wrist_flip = QtWidgets.QCheckBox("Wrist flip guard")
        self.opt_wrist_flip.setChecked(True)
        for col, w in enumerate((self.opt_clear, self.opt_micro_bend)):
            opts_grid.addWidget(w, 0, col)
        for col, w in enumerate((self.opt_palms, self.opt_feet)):
            opts_grid.addWidget(w, 1, col)
        for col, w in enumerate((self.opt_wrist_flip, self.opt_fingers)):
            opts_grid.addWidget(w, 2, col)

        naming_row = QtWidgets.QHBoxLayout()
        naming_row.addWidget(QtWidgets.QLabel("Source naming convention if not characterized:"))
        self.naming_combo = QtWidgets.QComboBox()
        self.naming_combo.addItem("MotionBuilder", NamingConvention.MOTIONBUILDER)
        self.naming_combo.addItem("3dsMax Biped", NamingConvention.BIPED_3DSMAX)
        naming_row.addWidget(self.naming_combo)
        naming_row.addStretch(1)
        opts_grid.addLayout(naming_row, 3, 0, 1, 2)
        layout.addWidget(opts_box)

        run_row = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("Run batch")
        self.run_btn.setDefault(True)
        run_row.addWidget(self.run_btn)
        run_row.addStretch(1)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1)
        run_row.addWidget(self.progress, stretch=1)
        layout.addLayout(run_row)

        log_box = QtWidgets.QGroupBox("Per-file results")
        log_layout = QtWidgets.QVBoxLayout(log_box)
        self.results_table = QtWidgets.QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(
            ["#", "File", "Result", "Time", "Notes"],
        )
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        log_layout.addWidget(self.results_table)
        layout.addWidget(log_box, stretch=1)

        self.summary = QtWidgets.QLabel("")
        layout.addWidget(self.summary)

        self.run_btn.clicked.connect(self._on_run)

    def _make_path_field(self, placeholder: str, file_mode: str):
        widget = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(widget)
        h.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText(placeholder)
        browse = QtWidgets.QPushButton("Browse...")
        h.addWidget(edit, stretch=1)
        h.addWidget(browse)

        def on_browse():
            if file_mode == "dir":
                path = QtWidgets.QFileDialog.getExistingDirectory(self, placeholder)
            else:
                path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, placeholder, "", "FBX Files (*.fbx)",
                )
            if path:
                edit.setText(path)
        browse.clicked.connect(on_browse)
        return widget, edit

    def _build_options(self) -> AlignOptions:
        return AlignOptions(
            clear_existing=self.opt_clear.isChecked(),
            preserve_micro_bend=self.opt_micro_bend.isChecked(),
            handle_wrist_flip=self.opt_wrist_flip.isChecked(),
            palms_down=self.opt_palms.isChecked(),
            feet_flat_forward=self.opt_feet.isChecked(),
            include_fingers=self.opt_fingers.isChecked(),
        )

    def _on_run(self) -> None:
        target = self.target_edit[1].text().strip()
        source = self.source_edit[1].text().strip()
        output = self.output_edit[1].text().strip()
        if not target or not source or not output:
            QtWidgets.QMessageBox.warning(self, "Missing paths",
                                          "All three paths are required.")
            return

        options = self._build_options()
        naming = self.naming_combo.currentData()

        self.results_table.setRowCount(0)
        QtWidgets.QApplication.processEvents()

        try:
            count = sum(1 for p in Path(source).iterdir()
                        if p.is_file() and p.suffix.lower() == ".fbx")
        except Exception:
            count = 1
        self.progress.setRange(0, max(1, count))
        self.progress.setValue(0)

        def progress_cb(idx: int, total: int, file_result: BatchFileResult) -> None:
            self.progress.setRange(0, total)
            self.progress.setValue(idx)
            self._append_result(idx, file_result)
            QtWidgets.QApplication.processEvents()

        try:
            self.run_btn.setEnabled(False)
            report = batch_retarget(
                Path(target),
                Path(source),
                Path(output),
                align_options=options,
                naming_convention=naming,
                progress_callback=progress_cb,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Batch failed",
                f"{exc}\n\n{traceback.format_exc()}",
            )
            return
        finally:
            self.run_btn.setEnabled(True)

        self.summary.setText(
            f"Done. {report.num_succeeded} ok, {report.num_failed} failed in {report.total_elapsed:.1f}s"
        )

    def _append_result(self, idx: int, result: BatchFileResult) -> None:
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)

        def cell(text: str, color: Optional[str] = None) -> QtWidgets.QTableWidgetItem:
            item = QtWidgets.QTableWidgetItem(text)
            if color:
                item.setForeground(QtGui.QColor(color))
            return item

        self.results_table.setItem(row, 0, cell(str(idx)))
        self.results_table.setItem(row, 1, cell(result.source_path.name))
        if result.success:
            note = f"{result.high_severity_count} high warning(s)" if result.high_severity_count else "OK"
            self.results_table.setItem(row, 2, cell("Success", "#3da35d"))
            self.results_table.setItem(row, 3, cell(f"{result.elapsed_seconds:.1f}s"))
            self.results_table.setItem(row, 4, cell(note))
        else:
            short_err = result.error.splitlines()[0] if result.error else "unknown error"
            self.results_table.setItem(row, 2, cell("Failed", "#c84630"))
            self.results_table.setItem(row, 3, cell(f"{result.elapsed_seconds:.1f}s"))
            self.results_table.setItem(row, 4, cell(short_err))


def show_batch_dialog():
    global _dialog_instance
    parent = None
    try:
        from pyfbsdk import FBSystem  # type: ignore  # noqa: F401
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        parent = app.activeWindow()
    except Exception:
        pass

    if _dialog_instance is None or not _dialog_instance.isVisible():
        _dialog_instance = BatchDialog(parent)
    _dialog_instance.show()
    _dialog_instance.raise_()
    _dialog_instance.activateWindow()
    return _dialog_instance


if __name__ == "__main__":
    show_batch_dialog()
