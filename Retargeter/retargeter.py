"""Entry point you can run from MotionBuilder's Python Editor.

Recommended usage (works on any setup, including ``exec(open(...).read())``
which does NOT set ``__file__``)::

    exec(open(r"C:\\path\\to\\MotionBuilder-Script\\Retargeter\\retargeter.py").read())

Alternative for normal Python execution where ``__file__`` is defined::

    from Retargeter import show_panel
    show_panel()

The script self-locates the ``Retargeter`` package by trying, in order:
``__file__`` if available, then any existing ``Retargeter`` already on
``sys.path``, then a small list of common Windows / project locations.
"""

import os
import sys


_PROJECT_HINTS = (
    # User's Desktop folder is the most common location for personal tools.
    "Desktop",
    "Documents",
    "Documents/MotionBuilder",
    "Documents/Maya/scripts",
)
_PROJECT_FOLDER_NAMES = ("MotionBuilder-Script",)


def _ensure_on_path() -> None:
    """Make ``import Retargeter`` work regardless of how this file was invoked.

    Handles three modes:

    * Normal Python execution: ``__file__`` is defined; use its parent.
    * ``runpy.run_path`` / ``exec(compile(..., file, "exec"))``: ``__file__`` is
      set, same handling as above.
    * ``exec(open(file).read())``: ``__file__`` is NOT defined; we fall back to
      checking ``sys.path`` and a handful of common project directories.
    """
    try:
        import Retargeter  # noqa: F401

        return
    except ImportError:
        pass

    candidates = []

    try:
        this_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.dirname(this_dir))
    except NameError:
        # __file__ is absent when this script is executed via
        # ``exec(open(...).read())``; fall through to other strategies.
        pass

    for entry in list(sys.path):
        if entry and os.path.isdir(os.path.join(entry, "Retargeter")):
            candidates.append(entry)

    home = os.path.expanduser("~")
    for hint in _PROJECT_HINTS:
        base = os.path.join(home, *hint.split("/"))
        for folder in _PROJECT_FOLDER_NAMES:
            candidates.append(os.path.join(base, folder))

    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isdir(os.path.join(c, "Retargeter")):
            if c not in sys.path:
                sys.path.insert(0, c)
            return

    raise RuntimeError(
        "Cannot locate the Retargeter package.\n"
        "Add its parent directory to sys.path manually before running:\n"
        "    import sys\n"
        "    sys.path.insert(0, r'C:\\path\\to\\MotionBuilder-Script')"
    )


def _force_reload() -> None:
    """Drop cached Retargeter.* modules so re-running picks up edits.

    MotionBuilder keeps the Python interpreter alive across script executions,
    so without this any change to a Retargeter module would not be visible
    until MoBu is restarted.
    """
    for name in list(sys.modules):
        if name == "Retargeter" or name.startswith("Retargeter."):
            del sys.modules[name]


def main() -> None:
    _ensure_on_path()
    _force_reload()
    # Imported after path setup + reload so a loose ``exec`` of this file
    # always resolves the latest source on disk.
    from Retargeter.ui.main_panel import show_panel

    show_panel()


main()
