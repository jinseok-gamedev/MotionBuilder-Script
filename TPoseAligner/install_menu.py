"""Register the TPoseAligner menu inside MotionBuilder.

Drop this file in your MotionBuilder ``config/Scripts/Startup`` folder
(typically ``Documents\\MB\\<version>\\config\\Scripts\\Startup``) - it runs
automatically on startup and adds a top-level menu with the dialogs.

You can also call :func:`install_menu` from your own startup script:

    from TPoseAligner.install_menu import install_menu
    install_menu()
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


_MENU_NAME = "TPose Align"
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


def install_menu() -> bool:
    """Add the menu and wire up callbacks. Idempotent."""
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
        manager.InsertBefore(None, "Help", _MENU_NAME)
        manager.InsertLast(_MENU_NAME, "T-Pose Pair Align...")
        manager.InsertLast(_MENU_NAME, "Batch Retarget...")
        manager.InsertLast(_MENU_NAME, "")
        manager.InsertLast(_MENU_NAME, "Reset Offsets on Current Character")

        menu = manager.GetMenu(_MENU_NAME)
        if menu is not None:
            menu.OnMenuActivate.Add(_on_menu_activate)
            _INSTALLED = True
            print(f"TPoseAligner: '{_MENU_NAME}' menu installed.")
            return True
        print("TPoseAligner: failed to retrieve newly created menu.")
        return False
    except Exception:
        traceback.print_exc()
        return False


if __name__ == "__main__" or os.environ.get("TPOSEALIGNER_AUTO_INSTALL", "1") == "1":
    install_menu()
