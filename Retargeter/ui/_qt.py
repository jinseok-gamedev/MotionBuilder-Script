"""Qt binding compatibility shim.

MotionBuilder >= 2025 ships PySide6 (Qt 6 / Python 3.11). Earlier versions
shipped PySide2. The rest of the UI modules import ``QtCore``, ``QtGui`` and
``QtWidgets`` from this module so we have a single place to swap bindings.

Pyside6 differences we care about
---------------------------------

* ``QAction`` moved from ``QtWidgets`` to ``QtGui``. We re-export it under
  ``QtWidgets`` for compatibility, but our panel does not use it directly.
* Some Qt enums became strict (``Qt.CheckState.Checked`` rather than
  ``Qt.Checked``). PySide6 keeps the legacy spellings as aliases so the
  unqualified form keeps working for now.
"""

from __future__ import annotations

_BINDING = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    _BINDING = "PySide6"
except ImportError:  # pragma: no cover
    try:
        from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
        _BINDING = "PySide2"
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Neither PySide6 nor PySide2 is available. "
            "MotionBuilder 2025+ uses PySide6; older versions use PySide2."
        ) from exc


__all__ = ["QtCore", "QtGui", "QtWidgets", "BINDING"]

BINDING = _BINDING
