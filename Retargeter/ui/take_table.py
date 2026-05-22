"""Take selection table widget.

After the import + plot phase runs, the panel inserts one row per take into
this widget so the operator can:

* tick / untick which takes should actually be exported
* override the global root motion mode per-take
* see status updates streaming back from the pipeline
* right-click for source-file actions / per-row removal
* filter rows by status (only failed, etc.)
* read an "empty state" hint before the first run

The widget is intentionally view-only of pipeline state -- it does NOT modify
the scene. The panel reads :class:`TakePlan` objects out via
:meth:`TakeTable.collect_plans` and feeds them into :class:`RunConfig`.

Externally the widget is still a single ``QWidget`` named ``take_table`` on
the panel; the old method names (``add_row``, ``collect_plans``,
``set_status``, ``set_all_checked``, ``set_all_root_motion``,
``populate_from_takes``, ``clear_rows``) are preserved so the rest of the
codebase keeps working unchanged.
"""

from __future__ import annotations

import os
from typing import List, Optional

from ._qt import QtCore, QtGui, QtWidgets  # type: ignore

from ..core.pipeline import TakePlan
from ..core.root_motion import MODE_EXTRACT, MODE_KEEP, MODE_STRIP
from ._qt_helpers import open_in_file_explorer, status_color as _status_color


_COL_EXPORT = 0
_COL_NAME = 1
_COL_SOURCE = 2
_COL_ROOT_MOTION = 3
_COL_QUALITY = 4
_COL_STATUS = 5

_HEADERS = ("Export", "Take", "Source", "Root Motion", "Quality", "Status")
_ROOT_MOTION_CHOICES = (MODE_KEEP, MODE_STRIP, MODE_EXTRACT)

# Quality combo entries. The empty string is the "unset" value and is what
# collect_quality_labels uses to skip a take when writing the feedback log.
_QUALITY_CHOICES = ("-", "good", "bad")
_QUALITY_UNSET = "-"

_PATH_ROLE = QtCore.Qt.UserRole + 1

_FILTER_ALL = "All"
_FILTER_CHOICES = (_FILTER_ALL, "pending", "running", "ok", "failed", "skipped", "dry-run")


def _row_background(status: str) -> Optional[QtGui.QColor]:
    """Tint a row faintly to flag failures / successes without yelling."""
    s = (status or "").lower()
    if s in ("failed", "error"):
        return QtGui.QColor(229, 57, 53, 40)
    if s == "ok":
        return QtGui.QColor(76, 175, 80, 28)
    if s == "skipped":
        return QtGui.QColor(251, 140, 0, 32)
    return None


class TakeTable(QtWidgets.QWidget):
    """Composite widget: status filter on top, sortable table below.

    The public method surface (``add_row`` etc.) matches the original
    ``QTableWidget`` subclass so existing call sites in ``main_panel`` do
    not need to change.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        header_row.addWidget(QtWidgets.QLabel("Filter:"))
        self.cmb_status_filter = QtWidgets.QComboBox()
        self.cmb_status_filter.addItems(_FILTER_CHOICES)
        self.cmb_status_filter.setMaximumWidth(120)
        self.cmb_status_filter.currentTextChanged.connect(self._apply_filter)
        header_row.addWidget(self.cmb_status_filter)
        self.lbl_count = QtWidgets.QLabel("0 takes")
        self.lbl_count.setStyleSheet("color: #888;")
        header_row.addWidget(self.lbl_count)
        header_row.addStretch(1)
        outer.addLayout(header_row)

        self.table = QtWidgets.QTableWidget(0, len(_HEADERS), self)
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_COL_EXPORT, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_NAME, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_SOURCE, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_ROOT_MOTION, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_QUALITY, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_STATUS, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(160)
        outer.addWidget(self.table, stretch=1)

        # Empty-state hint sits on top of the table viewport when there are
        # no rows. Parenting to the viewport (not the QTableWidget itself)
        # keeps it from drawing over the column header or scrollbars.
        self.lbl_empty = QtWidgets.QLabel(
            "No takes loaded yet.\n"
            "Add source FBX files, then press 'Import & Plot' or 'Run All'.",
            self.table.viewport(),
        )
        self.lbl_empty.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_empty.setStyleSheet(
            "QLabel { color: #9aa0a6; font-style: italic; background: transparent; }"
        )
        self.lbl_empty.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.lbl_empty.hide()
        self.table.viewport().installEventFilter(self)
        self._update_empty_state()

    # ------------------------------------------------------------------
    # Empty state overlay
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QtCore.QEvent.Resize:
            self._reposition_empty_label()
        return super().eventFilter(obj, event)

    def _reposition_empty_label(self) -> None:
        viewport = self.table.viewport()
        if viewport is None:
            return
        size = viewport.size()
        self.lbl_empty.setGeometry(0, 0, size.width(), size.height())
        self.lbl_empty.raise_()

    def _update_empty_state(self) -> None:
        is_empty = self.table.rowCount() == 0
        self.lbl_empty.setVisible(is_empty)
        if is_empty:
            self._reposition_empty_label()
        self.lbl_count.setText(f"{self.table.rowCount()} takes")

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def clear_rows(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setSortingEnabled(True)
        self._update_empty_state()

    def add_row(
        self,
        take_name: str,
        source_file: str = "",
        default_root_motion: str = MODE_KEEP,
        export_default: bool = True,
    ) -> int:
        self.table.setSortingEnabled(False)
        row = self.table.rowCount()
        self.table.insertRow(row)

        check = QtWidgets.QTableWidgetItem()
        check.setFlags(check.flags() | QtCore.Qt.ItemIsUserCheckable)
        check.setCheckState(QtCore.Qt.Checked if export_default else QtCore.Qt.Unchecked)
        check.setTextAlignment(QtCore.Qt.AlignCenter)
        self.table.setItem(row, _COL_EXPORT, check)

        name_item = QtWidgets.QTableWidgetItem(take_name)
        name_item.setData(QtCore.Qt.UserRole, take_name)
        self.table.setItem(row, _COL_NAME, name_item)

        basename = os.path.basename(source_file) if source_file else ""
        source_item = QtWidgets.QTableWidgetItem(basename)
        source_item.setData(_PATH_ROLE, source_file)
        source_item.setToolTip(source_file)
        self.table.setItem(row, _COL_SOURCE, source_item)

        combo = QtWidgets.QComboBox()
        combo.addItems(_ROOT_MOTION_CHOICES)
        if default_root_motion in _ROOT_MOTION_CHOICES:
            combo.setCurrentText(default_root_motion)
        self.table.setCellWidget(row, _COL_ROOT_MOTION, combo)

        quality_combo = QtWidgets.QComboBox()
        quality_combo.addItems(_QUALITY_CHOICES)
        quality_combo.setCurrentText(_QUALITY_UNSET)
        quality_combo.setToolTip(
            "Operator's quality label (good / bad / unset). Save with the "
            "'Save feedback' action on the panel to append to _retarget_feedback.jsonl."
        )
        self.table.setCellWidget(row, _COL_QUALITY, quality_combo)

        status_item = QtWidgets.QTableWidgetItem("pending")
        status_item.setForeground(QtCore.Qt.gray)
        self.table.setItem(row, _COL_STATUS, status_item)

        self.table.setSortingEnabled(True)
        self._apply_filter(self.cmb_status_filter.currentText())
        self._update_empty_state()
        return row

    def populate_from_takes(
        self,
        rows,
        default_root_motion: str = MODE_KEEP,
    ) -> None:
        """Replace contents with ``rows = [(take_name, source_file), ...]``."""
        self.clear_rows()
        for take_name, source_file in rows:
            self.add_row(take_name, source_file, default_root_motion)

    # ------------------------------------------------------------------
    # Plan extraction
    # ------------------------------------------------------------------

    def collect_plans(self) -> List[TakePlan]:
        plans = []
        for row in range(self.table.rowCount()):
            check = self.table.item(row, _COL_EXPORT)
            name_item = self.table.item(row, _COL_NAME)
            source_item = self.table.item(row, _COL_SOURCE)
            combo = self.table.cellWidget(row, _COL_ROOT_MOTION)
            if name_item is None:
                continue
            take_name = name_item.data(QtCore.Qt.UserRole) or name_item.text()
            source_file = ""
            if source_item is not None:
                source_file = source_item.data(_PATH_ROLE) or source_item.text()
            plans.append(
                TakePlan(
                    take_name=take_name,
                    source_file=source_file,
                    export=(check.checkState() == QtCore.Qt.Checked) if check is not None else True,
                    root_motion_mode=combo.currentText() if combo is not None else MODE_KEEP,
                )
            )
        return plans

    def set_all_checked(self, checked: bool) -> None:
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_EXPORT)
            if item is not None:
                item.setCheckState(state)

    def set_all_root_motion(self, mode: str) -> None:
        if mode not in _ROOT_MOTION_CHOICES:
            return
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, _COL_ROOT_MOTION)
            if combo is not None:
                combo.setCurrentText(mode)

    def set_quality_hint(self, take_name: str, label: Optional[str]) -> None:
        """Pre-fill the Quality combo with a metric-derived suggestion.

        Only writes when the operator has not yet picked anything (still
        at ``"-"``); we never overwrite an explicit choice. Suggested rows
        get a faint yellow tint on the Quality cell so it is visually
        distinct from manually-set labels, and the combo carries a tooltip
        explaining the source of the hint."""
        if not label or label not in _QUALITY_CHOICES:
            return
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, _COL_NAME)
            if name_item is None:
                continue
            stored = name_item.data(QtCore.Qt.UserRole) or name_item.text()
            if stored != take_name:
                continue
            combo = self.table.cellWidget(row, _COL_QUALITY)
            if combo is None:
                return
            if combo.currentText() != _QUALITY_UNSET:
                return
            combo.blockSignals(True)
            combo.setCurrentText(label)
            combo.blockSignals(False)
            combo.setStyleSheet("QComboBox { background-color: #fff59d; }")
            combo.setToolTip(
                f"Suggested by quality metrics ({label}). Confirm or change "
                "before clicking 'Save feedback'."
            )
            return

    def collect_quality_labels(self) -> "list[tuple[str, str]]":
        """Return ``[(take_name, label), ...]`` for every labelled take.

        Skips rows where Quality is still ``"-"`` (unset) so the feedback
        log only grows on explicit operator opinions, never on noise.
        """
        out = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, _COL_NAME)
            quality_combo = self.table.cellWidget(row, _COL_QUALITY)
            if name_item is None or quality_combo is None:
                continue
            take_name = name_item.data(QtCore.Qt.UserRole) or name_item.text()
            label = quality_combo.currentText()
            if not label or label == _QUALITY_UNSET:
                continue
            out.append((take_name, label))
        return out

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def set_status(self, take_name: str, status: str, tooltip: str = "") -> None:
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, _COL_NAME)
            if name_item is None:
                continue
            stored = name_item.data(QtCore.Qt.UserRole) or name_item.text()
            if stored != take_name:
                continue
            status_item = self.table.item(row, _COL_STATUS)
            if status_item is None:
                status_item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, _COL_STATUS, status_item)
            status_item.setText(status)
            status_item.setToolTip(tooltip or status)
            color = _status_color(status)
            if color is not None:
                status_item.setForeground(color)
            self._tint_row(row, status)
            return
        self._apply_filter(self.cmb_status_filter.currentText())

    def _tint_row(self, row: int, status: str) -> None:
        bg = _row_background(status)
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is None:
                continue
            if bg is None:
                item.setData(QtCore.Qt.BackgroundRole, None)
            else:
                item.setBackground(bg)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_filter(self, choice: str) -> None:
        choice = (choice or _FILTER_ALL).lower()
        visible = 0
        for row in range(self.table.rowCount()):
            if choice == _FILTER_ALL.lower():
                hide = False
            else:
                status_item = self.table.item(row, _COL_STATUS)
                status_text = (status_item.text() if status_item is not None else "").lower()
                hide = status_text != choice
            self.table.setRowHidden(row, hide)
            if not hide:
                visible += 1
        if choice == _FILTER_ALL.lower():
            self.lbl_count.setText(f"{self.table.rowCount()} takes")
        else:
            self.lbl_count.setText(
                f"{visible} / {self.table.rowCount()} takes ({choice})"
            )

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        row = index.row() if index.isValid() else -1
        source_path = ""
        take_name = ""
        if row >= 0:
            source_item = self.table.item(row, _COL_SOURCE)
            name_item = self.table.item(row, _COL_NAME)
            if source_item is not None:
                source_path = source_item.data(_PATH_ROLE) or source_item.text()
            if name_item is not None:
                take_name = name_item.data(QtCore.Qt.UserRole) or name_item.text()

        menu = QtWidgets.QMenu(self.table)
        act_open = menu.addAction("Reveal source in file explorer")
        act_copy = menu.addAction("Copy source path")
        act_copy_take = menu.addAction("Copy take name")
        menu.addSeparator()
        act_remove = menu.addAction("Remove row")

        act_open.setEnabled(bool(source_path))
        act_copy.setEnabled(bool(source_path))
        act_copy_take.setEnabled(bool(take_name))
        act_remove.setEnabled(row >= 0)

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_open:
            open_in_file_explorer(source_path)
        elif chosen is act_copy:
            QtWidgets.QApplication.clipboard().setText(source_path)
        elif chosen is act_copy_take:
            QtWidgets.QApplication.clipboard().setText(take_name)
        elif chosen is act_remove and row >= 0:
            self.table.removeRow(row)
            self._update_empty_state()
            self._apply_filter(self.cmb_status_filter.currentText())
