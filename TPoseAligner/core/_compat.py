"""Cross-version compatibility shims for ``pyfbsdk``.

MotionBuilder 2026 removed the ``FBRVector`` / ``FBTVector`` symbols that
older docs and scripts use; the underlying classes are now exposed as
``FBVector3d`` and ``FBVector4d``. The pyfbsdk method bindings accept
either name interchangeably (they were typedefs at the C++ level), so
this shim simply re-exports whichever set of symbols is present in the
running MotionBuilder.

Use ``from ._compat import FBRVector, FBTVector`` everywhere instead of
importing them directly from ``pyfbsdk``.
"""

from __future__ import annotations

try:
    from pyfbsdk import FBRVector  # type: ignore
except ImportError:
    try:
        from pyfbsdk import FBVector3d as FBRVector  # type: ignore
    except ImportError:
        from pyfbsdk import FBVector3 as FBRVector  # type: ignore # noqa: F401

try:
    from pyfbsdk import FBTVector  # type: ignore
except ImportError:
    try:
        from pyfbsdk import FBVector4d as FBTVector  # type: ignore
    except ImportError:
        from pyfbsdk import FBVector4 as FBTVector  # type: ignore # noqa: F401


__all__ = ["FBRVector", "FBTVector"]
