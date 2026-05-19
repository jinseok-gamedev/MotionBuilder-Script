"""Convenience launcher you can drop into a MotionBuilder shelf or call from
the Python Editor.

Recommended invocation::

    exec(open(r"<repo>/Retargeter/Retargeter_Start.py").read())

The actual entry point is ``retargeter.py`` next to this file; it already
handles ``sys.path`` setup, cached-module purge, and showing the panel, so
this launcher just locates and forwards to it.

Two execution modes are supported:

* Normal Python execution (``runpy``, ``import``): ``__file__`` is defined.
* ``exec(open(...).read())`` from MotionBuilder: ``__file__`` is NOT defined;
  we scan ``sys.path`` and a few common project locations as a fallback.
"""

import os
import sys


def _resolve_target() -> str:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(here, "retargeter.py")
        if os.path.isfile(candidate):
            return candidate
    except NameError:
        pass

    for entry in list(sys.path):
        candidate = os.path.join(entry, "Retargeter", "retargeter.py")
        if os.path.isfile(candidate):
            return candidate

    home = os.path.expanduser("~")
    for base in ("Desktop", "Documents"):
        candidate = os.path.join(
            home, base, "MotionBuilder-Script", "Retargeter", "retargeter.py"
        )
        if os.path.isfile(candidate):
            return candidate

    raise RuntimeError(
        "Retargeter_Start: cannot locate Retargeter/retargeter.py.\n"
        "Add the project's parent directory to sys.path manually."
    )


_target = _resolve_target()
exec(compile(open(_target).read(), _target, "exec"))
