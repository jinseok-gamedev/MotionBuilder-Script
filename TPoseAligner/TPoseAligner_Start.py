"""Convenience launcher for the TPoseAligner pair-align dialog.

Recommended invocation from MotionBuilder Python Editor::

    exec(open(r"<repo>/TPoseAligner/TPoseAligner_Start.py").read())

For permanent use, place :mod:`TPoseAligner.install_menu` in MotionBuilder's
``config/Scripts/Startup`` folder so the "TPose Align" menu appears
automatically on every launch.

Two execution modes are supported:

* Normal Python execution: ``__file__`` is defined; we use it to locate
  the project root (the folder containing ``TPoseAligner/``).
* ``exec(open(...).read())``: ``__file__`` is NOT defined; we scan
  ``sys.path`` and a few common project locations as a fallback.
"""

import os
import sys


def _resolve_root() -> str:
    try:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass

    for entry in list(sys.path):
        if entry and os.path.isdir(os.path.join(entry, "TPoseAligner")):
            return entry

    home = os.path.expanduser("~")
    for base in ("Desktop", "Documents"):
        candidate = os.path.join(home, base, "MotionBuilder-Script")
        if os.path.isdir(os.path.join(candidate, "TPoseAligner")):
            return candidate

    raise RuntimeError(
        "TPoseAligner_Start: cannot locate the project root.\n"
        "Add it to sys.path manually before running."
    )


_root = _resolve_root()
if _root not in sys.path:
    sys.path.insert(0, _root)

for _name in list(sys.modules):
    if _name == "TPoseAligner" or _name.startswith("TPoseAligner."):
        del sys.modules[_name]

from TPoseAligner.ui import show_align_dialog  # noqa: E402

show_align_dialog()
