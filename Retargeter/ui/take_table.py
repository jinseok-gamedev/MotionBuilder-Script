"""Take selection table widget.

After the import + plot phase runs, the panel inserts one row per take into
this table so the operator can:

* tick / untick which takes should actually be exported
* override the global root motion mode per-take
* see status updates streaming back from the pipeline

The widget is intentionally view-only of pipeline state -- it does NOT modify
the scene. The panel reads :class:`TakePlan` objects out via
:meth:`TakeTable.collect_plans` and feeds them into :class:`RunConfig`.
"""

from __future__ import annotations

from typing import List

from ._qt import QtCore, QtWidgets  # type: ignore

from ..core.pipeline import TakePlan
from ..core.root_motion import MODE_EXTRACT, MODE_KEEP, MODE_STRIP
from ._qt_helpers import status_color as _status_color


_COL_EXPORT = 0
_COL_NAME = 1
_COL_SOURCE = 2
_COL_ROOT_MOTION = 3
_COL_STATUS = 4

_HEADERS = ("Export", "Take", "Source", "Root Motion", "Status")
_ROOT_MOTION_CHOICES = (MODE_KEEP, MODE_STRIP, MODE_EXTRACT)


class TakeTable(QtWidgets.QTableWidget):
    """QTableWidget specialised for retarget take selection."""

    def __init__(self, parent=None):
        super().__init__(0, len(_HEADERS), parent)
        self.setHorizontalHeaderLabels(_HEADERS)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.horizontalHeader()
        header.setSectionResizeMode(_COL_EXPORT, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_NAME, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_SOURCE, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_ROOT_MOTION, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_STATUS, QtWidgets.QHeaderView.ResizeToContents)
        self.setMinimumHeight(160)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def clear_rows(self) -> None:
        self.setRowCount(0)

    def add_row(
        self,
        take_name: str,
        source_file: str = "",
        default_root_motion: str = MODE_KEEP,
        export_default: bool = True,
    ) -> int:
        row = self.rowCount()
        self.insertRow(row)

        check = QtWidgets.QTableWidgetItem()
        check.setFlags(check.flags() | QtCore.Qt.ItemIsUserCheckable)
        check.setCheckState(QtCore.Qt.Checked if export_default else QtCore.Qt.Unchecked)
        check.setTextAlignment(QtCore.Qt.AlignCenter)
        self.setItem(row, _COL_EXPORT, check)

        name_item = QtWidgets.QTableWidgetItem(take_name)
        name_item.setData(QtCore.Qt.UserRole, take_name)
        self.setItem(row, _COL_NAME, name_item)

        source_item = QtWidgets.QTableWidgetItem(source_file)
        source_item.setToolTip(source_file)
        self.setItem(row, _COL_SOURCE, source_item)

        combo = QtWidgets.QComboBox()
        combo.addItems(_ROOT_MOTION_CHOICES)
        if default_root_motion in _ROOT_MOTION_CHOICES:
            combo.setCurrentText(default_root_motion)
        self.setCellWidget(row, _COL_ROOT_MOTION, combo)

        status_item = QtWidgets.QTableWidgetItem("pending")
        status_item.setForeground(QtCore.Qt.gray)
        self.setItem(row, _COL_STATUS, status_item)
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
        for row in range(self.rowCount()):
            check = self.item(row, _COL_EXPORT)
            name_item = self.item(row, _COL_NAME)
            source_item = self.item(row, _COL_SOURCE)
            combo = self.cellWidget(row, _COL_ROOT_MOTION)
            if name_item is None:
                continue
            take_name = name_item.data(QtCore.Qt.UserRole) or name_item.text()
            plans.append(
                TakePlan(
                    take_name=take_name,
                    source_file=source_item.text() if source_item is not None else "",
                    export=(check.checkState() == QtCore.Qt.Checked) if check is not None else True,
                    root_motion_mode=combo.currentText() if combo is not None else MODE_KEEP,
                )
            )
        return plans

    def set_all_checked(self, checked: bool) -> None:
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for row in range(self.rowCount()):
            item = self.item(row, _COL_EXPORT)
            if item is not None:
                item.setCheckState(state)

    def set_all_root_motion(self, mode: str) -> None:
        if mode not in _ROOT_MOTION_CHOICES:
            return
        for row in range(self.rowCount()):
            combo = self.cellWidget(row, _COL_ROOT_MOTION)
            if combo is not None:
                combo.setCurrentText(mode)

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def set_status(self, take_name: str, status: str, tooltip: str = "") -> None:
        for row in range(self.rowCount()):
            name_item = self.item(row, _COL_NAME)
            if name_item is None:
                continue
            stored = name_item.data(QtCore.Qt.UserRole) or name_item.text()
            if stored != take_name:
                continue
            status_item = self.item(row, _COL_STATUS)
            if status_item is None:
                status_item = QtWidgets.QTableWidgetItem()
                self.setItem(row, _COL_STATUS, status_item)
            status_item.setText(status)
            status_item.setToolTip(tooltip or status)
            color = _status_color(status)
            if color is not None:
                status_item.setForeground(color)
            return
