"""Small Qt helpers shared by the UI modules.

Keeping these out of ``main_panel`` so the panel reads top-to-bottom as a
single composition of named widgets.
"""

from __future__ import annotations

from typing import List, Optional

from ._qt import QtGui, QtWidgets  # type: ignore


def status_color(status: str) -> Optional[QtGui.QColor]:
    s = (status or "").lower()
    if s == "ok":
        return QtGui.QColor("#4caf50")
    if s in ("failed", "error"):
        return QtGui.QColor("#e53935")
    if s == "skipped":
        return QtGui.QColor("#fb8c00")
    if s in ("pending", ""):
        return QtGui.QColor("#9e9e9e")
    if s == "running":
        return QtGui.QColor("#1e88e5")
    return None


def make_separator() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setFrameShadow(QtWidgets.QFrame.Sunken)
    return line


def find_motionbuilder_main_window() -> Optional[QtWidgets.QWidget]:
    """Best-effort lookup of MotionBuilder's main window for dialog parenting."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    for w in app.topLevelWidgets():
        if not w:
            continue
        title = w.windowTitle() or ""
        if "MotionBuilder" in title:
            return w
    for w in app.topLevelWidgets():
        if isinstance(w, QtWidgets.QMainWindow):
            return w
    return None


def labeled_row(label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
    row = QtWidgets.QWidget()
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    lab = QtWidgets.QLabel(label)
    lab.setMinimumWidth(110)
    h.addWidget(lab)
    h.addWidget(widget, stretch=1)
    return row


def warning_box(parent, title: str, text: str) -> None:
    QtWidgets.QMessageBox.warning(parent, title, text)


def info_box(parent, title: str, text: str) -> None:
    QtWidgets.QMessageBox.information(parent, title, text)
