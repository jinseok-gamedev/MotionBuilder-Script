"""Colored log viewer.

The pipeline emits plain text lines through :class:`Retargeter.core.logger.Logger`
sinks. ``ColoredLogView`` wraps those lines in coloured HTML based on the leading
severity token (``[ERROR]`` / ``[WARN]`` / ``[INFO]`` / ``[DRY-RUN]``) so the
operator can spot the few important lines inside hundreds of bone-cleanup logs.

The widget keeps the bounded ``maximumBlockCount`` behaviour of the previous
``QPlainTextEdit`` (5000 lines) and the same monospaced look so existing screen
captures still match.
"""

from __future__ import annotations

import html
import re

from ._qt import QtGui, QtWidgets  # type: ignore


_SEVERITY_RE = re.compile(r"^\s*\[(ERROR|WARN|WARNING|INFO|DRY-RUN|DEBUG)\]\s*", re.IGNORECASE)

_SEVERITY_COLORS = {
    "ERROR": "#e53935",
    "WARN": "#fb8c00",
    "WARNING": "#fb8c00",
    "INFO": "#9aa0a6",
    "DRY-RUN": "#1e88e5",
    "DEBUG": "#7e57c2",
}


class ColoredLogView(QtWidgets.QTextEdit):
    """Read-only log view that colours lines by their severity prefix.

    ``QTextEdit`` (not ``QPlainTextEdit``) is used because we want inline HTML
    colouring per token. ``setMaximumBlockCount`` is not available on
    ``QTextEdit``, so we trim manually after each append.
    """

    def __init__(self, parent=None, max_lines: int = 5000):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self.setStyleSheet(
            "QTextEdit { font-family: Consolas, 'Courier New', monospace;"
            " font-size: 10pt; background-color: #1e1e1e; color: #d4d4d4; }"
        )
        self.setMinimumHeight(120)
        self._max_lines = max_lines

    def append_line(self, line: str) -> None:
        """Append one log line, colouring it by detected severity prefix."""
        line = line or ""
        match = _SEVERITY_RE.match(line)
        if match:
            severity = match.group(1).upper()
            color = _SEVERITY_COLORS.get(severity, "#d4d4d4")
            head = html.escape(line[: match.end()])
            body = html.escape(line[match.end():])
            weight = "bold" if severity in ("ERROR", "WARN", "WARNING") else "normal"
            html_line = (
                f'<span style="color:{color}; font-weight:{weight};">{head}</span>'
                f'<span style="color:#d4d4d4;">{body}</span>'
            )
        else:
            html_line = f'<span style="color:#d4d4d4;">{html.escape(line)}</span>'

        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertHtml(html_line + "<br>")
        self._trim_to_max_lines()

        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_log(self) -> None:
        self.clear()

    def _trim_to_max_lines(self) -> None:
        """Drop oldest blocks once we exceed the limit."""
        doc = self.document()
        overflow = doc.blockCount() - self._max_lines
        if overflow <= 0:
            return
        cursor = QtGui.QTextCursor(doc)
        cursor.movePosition(QtGui.QTextCursor.Start)
        for _ in range(overflow):
            cursor.select(QtGui.QTextCursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # also drop the trailing newline / block break
