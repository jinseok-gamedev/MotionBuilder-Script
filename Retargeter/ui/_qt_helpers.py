"""Small Qt helpers shared by the UI modules.

Keeping these out of ``main_panel`` so the panel reads top-to-bottom as a
single composition of named widgets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from ._qt import QtCore, QtGui, QtWidgets  # type: ignore


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


def standard_icon(widget: QtWidgets.QWidget, sp_pixmap) -> QtGui.QIcon:
    """Wrap ``QStyle.standardIcon`` with a sane fallback for headless tests."""
    style = widget.style() if widget is not None else QtWidgets.QApplication.style()
    return style.standardIcon(sp_pixmap)


def make_tool_button(
    icon: QtGui.QIcon,
    tooltip: str,
    parent: Optional[QtWidgets.QWidget] = None,
    *,
    text: str = "",
    auto_raise: bool = True,
) -> QtWidgets.QToolButton:
    """Create a uniform small icon-style ``QToolButton`` for toolbars/inline rows."""
    btn = QtWidgets.QToolButton(parent)
    btn.setIcon(icon)
    btn.setToolTip(tooltip)
    btn.setAutoRaise(auto_raise)
    if text:
        btn.setText(text)
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    else:
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
    return btn


def open_in_file_explorer(path: str) -> bool:
    """Reveal ``path`` in the OS file manager.

    On Windows we use ``explorer /select,`` so the file itself is highlighted
    when ``path`` is a file. On other OSes we open the containing folder via
    ``QDesktopServices`` (per-file selection is OS-specific and rarely worth
    the maintenance burden inside MotionBuilder).
    """
    if not path:
        return False
    path = os.path.normpath(path)
    if sys.platform.startswith("win"):
        if not os.path.exists(path):
            return False
        try:
            if os.path.isdir(path):
                subprocess.Popen(["explorer", path])
            else:
                subprocess.Popen(["explorer", f"/select,{path}"])
            return True
        except Exception:
            return False
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not folder or not os.path.isdir(folder):
        return False
    return QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))


def human_readable_size(num_bytes: int) -> str:
    """Format a byte count like ``"3.4 MB"``. Returns ``"-"`` on bad input."""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"
