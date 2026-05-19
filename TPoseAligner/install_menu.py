"""Register the TPoseAligner menu inside MotionBuilder.

Drop this file in your MotionBuilder ``config/Scripts/Startup`` folder
(typically ``Documents\\MB\\<version>\\config\\Scripts\\Startup``) - it runs
automatically on startup and adds a "TPose Align" submenu under a shared
top-level "Tools" menu.

You can also call :func:`install_menu` from your own startup script:

    from TPoseAligner.install_menu import install_menu
    install_menu()

The "Tools" parent menu is shared with sibling tools (e.g. Retargeter);
each tool's installer creates the parent menu only if it does not already
exist, so independent installs do not collide.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


_PARENT_MENU_NAME = "Tools"
_SUBMENU_NAME = "TPose Align"
_FULL_MENU_PATH = f"{_PARENT_MENU_NAME}/{_SUBMENU_NAME}"
_INSTALLED = False


def _ensure_package_on_path() -> None:
    """Make sure ``import TPoseAligner`` works from inside MotionBuilder."""
    here = Path(__file__).resolve().parent
    parent = str(here.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)


def _open_align_dialog(control, event) -> None:
    try:
        from TPoseAligner.ui import show_align_dialog
        show_align_dialog()
    except Exception:
        traceback.print_exc()


def _open_batch_dialog(control, event) -> None:
    try:
        from TPoseAligner.ui import show_batch_dialog
        show_batch_dialog()
    except Exception:
        traceback.print_exc()


def _reset_offsets(control, event) -> None:
    try:
        from pyfbsdk import FBApplication, FBMessageBox  # type: ignore
        from TPoseAligner.core.snapshot import reset_all_offsets

        char = FBApplication().CurrentCharacter
        if char is None:
            FBMessageBox(
                "TPoseAligner",
                "No current character set. Pick one in Character Controls first.",
                "OK",
            )
            return
        reset_all_offsets(char)
        FBMessageBox(
            "TPoseAligner",
            f"All offsets reset on {char.LongName}.",
            "OK",
        )
    except Exception:
        traceback.print_exc()


def _on_menu_activate(control, event) -> None:
    label = ""
    try:
        label = event.Name
    except Exception:
        pass
    if "Pair" in label:
        _open_align_dialog(control, event)
    elif "Batch" in label:
        _open_batch_dialog(control, event)
    elif "Reset" in label:
        _reset_offsets(control, event)


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
        print("TPoseAligner.install_menu: pyfbsdk not available, skipping.")
        return False

    try:
        manager = FBMenuManager()
        _ensure_parent_menu(manager)

        if manager.GetMenu(_FULL_MENU_PATH) is None:
            manager.InsertLast(_PARENT_MENU_NAME, _SUBMENU_NAME)

        manager.InsertLast(_FULL_MENU_PATH, "T-Pose Pair Align...")
        manager.InsertLast(_FULL_MENU_PATH, "Batch Retarget...")
        manager.InsertLast(_FULL_MENU_PATH, "")
        manager.InsertLast(_FULL_MENU_PATH, "Reset Offsets on Current Character")

        submenu = manager.GetMenu(_FULL_MENU_PATH)
        if submenu is not None:
            submenu.OnMenuActivate.Add(_on_menu_activate)
            _INSTALLED = True
            print(f"TPoseAligner: '{_FULL_MENU_PATH}' submenu installed.")
            return True
        print("TPoseAligner: failed to retrieve newly created submenu.")
        return False
    except Exception:
        traceback.print_exc()
        return False


if __name__ == "__main__" or os.environ.get("TPOSEALIGNER_AUTO_INSTALL", "1") == "1":
    install_menu()
