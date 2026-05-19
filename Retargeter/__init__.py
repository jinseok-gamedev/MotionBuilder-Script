"""Retargeter - MotionBuilder HumanIK based retargeting hub for Max/Maya/UE5.

Entry points:
    from Retargeter import show_panel
    show_panel()
"""

__version__ = "0.1.0"
__all__ = ["show_panel"]


def show_panel():
    """Lazy import the UI so importing the package does not require PySide2."""
    from .ui.main_panel import show_panel as _show

    return _show()
