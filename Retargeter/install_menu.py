"""Register the Retargeter menu inside MotionBuilder.

Drop this file in your MotionBuilder ``config/Scripts/Startup`` folder
(typically ``Documents\\MB\\<version>\\config\\Scripts\\Startup``) - it runs
automatically on startup and adds a "Retargeter" submenu under a shared
top-level "Tools" menu.

You can also call :func:`install_menu` from your own startup script:

    from Retargeter.install_menu import install_menu
    install_menu()

The "Tools" parent menu is shared with sibling tools (e.g. TPoseAligner);
each tool's installer creates the parent menu only if it does not already
exist, so independent installs do not collide.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


_PARENT_MENU_NAME = "Tools"
_SUBMENU_NAME = "Retargeter"
_FULL_MENU_PATH = f"{_PARENT_MENU_NAME}/{_SUBMENU_NAME}"
_INSTALLED = False


def _ensure_package_on_path() -> None:
    """Make sure ``import Retargeter`` works from inside MotionBuilder."""
    here = Path(__file__).resolve().parent
    parent = str(here.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)


def _force_reload() -> None:
    """Drop cached Retargeter.* modules so menu clicks pick up edits."""
    for name in list(sys.modules):
        if name == "Retargeter" or name.startswith("Retargeter."):
            del sys.modules[name]


def _open_panel(control, event) -> None:
    try:
        _ensure_package_on_path()
        _force_reload()
        from Retargeter import show_panel
        show_panel()
    except Exception:
        traceback.print_exc()


def _on_menu_activate(control, event) -> None:
    label = ""
    try:
        label = event.Name
    except Exception:
        pass
    if "Open" in label or "Panel" in label:
        _open_panel(control, event)


def _ensure_parent_menu(manager) -> None:
    """Create the shared "Tools" menu if it does not exist yet.

    Idempotent across multiple tools that share the same parent.
    """
    if manager.GetMenu(_PARENT_MENU_NAME) is None:
        manager.InsertBefore(None, "Help", _PARENT_MENU_NAME)


def install_menu() -> bool:
    """Add the submenu and wire up callbacks. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return True

    _ensure_package_on_path()

    try:
        from pyfbsdk import FBMenuManager  # type: ignore
    except ImportError:
        print("Retargeter.install_menu: pyfbsdk not available, skipping.")
        return False

    try:
        manager = FBMenuManager()
        _ensure_parent_menu(manager)

        if manager.GetMenu(_FULL_MENU_PATH) is None:
            manager.InsertLast(_PARENT_MENU_NAME, _SUBMENU_NAME)

        manager.InsertLast(_FULL_MENU_PATH, "Open Retargeter Panel...")

        submenu = manager.GetMenu(_FULL_MENU_PATH)
        if submenu is not None:
            submenu.OnMenuActivate.Add(_on_menu_activate)
            _INSTALLED = True
            print(f"Retargeter: '{_FULL_MENU_PATH}' submenu installed.")
            return True
        print("Retargeter: failed to retrieve newly created submenu.")
        return False
    except Exception:
        traceback.print_exc()
        return False


if __name__ == "__main__" or os.environ.get("RETARGETER_AUTO_INSTALL", "1") == "1":
    install_menu()
