"""Aggregate installer that registers menus for every tool in this repo.

Drop this single file (or a symlink to it) into MotionBuilder's
``config/Scripts/Startup`` folder - typically:

    Documents\\MB\\<version>\\config\\Scripts\\Startup\\

On the next launch a single shared **Tools** menu is added to the menubar
with one submenu per shipped tool::

    Tools
    +-- Retargeter
    |   +-- Open Retargeter Panel...
    +-- TPose Align
        +-- T-Pose Pair Align...
        +-- Batch Retarget...
        +-- ---
        +-- Reset Offsets on Current Character

Two deployment styles are supported:

* **Symlink** (recommended): ``__file__`` resolves to the real path
  inside the repo, so the project root is located trivially.
* **Copy**: ``__file__`` points at the startup folder which does not
  contain the packages. We fall back to scanning ``sys.path`` and a few
  conventional project locations for a folder that contains both
  ``Retargeter/`` and ``TPoseAligner/``.

The menu callbacks ``import`` each tool's public API lazily and purge
that package's cached modules first, so edits made in Cursor are picked
up on the next click without restarting MotionBuilder. Each click only
reloads the package owning the clicked submenu, never its siblings.

Set ``MOBU_TOOLS_AUTO_INSTALL=0`` to skip the on-import auto-run when
calling :func:`install_all_menus` from your own startup script.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


_PARENT_MENU_NAME = "Tools"
_RETARGETER_SUBMENU = "Retargeter"
_TPOSE_SUBMENU = "TPose Align"
_RETARGETER_PATH = f"{_PARENT_MENU_NAME}/{_RETARGETER_SUBMENU}"
_TPOSE_PATH = f"{_PARENT_MENU_NAME}/{_TPOSE_SUBMENU}"

_REQUIRED_PACKAGES = ("Retargeter", "TPoseAligner")
_INSTALLED = False


# ---------------------------------------------------------------------------
# Path / module bookkeeping
# ---------------------------------------------------------------------------

def _looks_like_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return all(os.path.isdir(os.path.join(path, name)) for name in _REQUIRED_PACKAGES)


def _ensure_repo_on_path() -> str:
    """Locate the repo root and add it to ``sys.path``.

    Returns the resolved path for logging. Raises if it cannot be found.
    """
    candidates: list[str] = []

    try:
        here = Path(__file__).resolve().parent
        candidates.append(str(here))
    except NameError:
        # exec(open(...).read()) - __file__ is undefined.
        pass

    for entry in list(sys.path):
        candidates.append(entry)

    home = os.path.expanduser("~")
    for base in ("Desktop", "Documents"):
        candidates.append(os.path.join(home, base, "MotionBuilder-Script"))

    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if _looks_like_repo(c):
            if c not in sys.path:
                sys.path.insert(0, c)
            return c

    raise RuntimeError(
        "install_menus: cannot locate the MotionBuilder-Script repo "
        f"(expected sibling folders: {', '.join(_REQUIRED_PACKAGES)}). "
        "Add the project's parent directory to sys.path manually, or "
        "deploy this file as a symlink rather than a copy."
    )


def _purge_modules(prefix: str) -> None:
    """Drop cached ``<prefix>`` and ``<prefix>.*`` modules from sys.modules."""
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            del sys.modules[name]


# ---------------------------------------------------------------------------
# Retargeter callbacks
# ---------------------------------------------------------------------------

def _open_retargeter_panel(control, event) -> None:
    try:
        _ensure_repo_on_path()
        _purge_modules("Retargeter")
        from Retargeter import show_panel
        show_panel()
    except Exception:
        traceback.print_exc()


def _on_retargeter_menu(control, event) -> None:
    label = ""
    try:
        label = event.Name
    except Exception:
        pass
    if "Open" in label or "Panel" in label:
        _open_retargeter_panel(control, event)


# ---------------------------------------------------------------------------
# TPoseAligner callbacks
# ---------------------------------------------------------------------------

def _open_tpose_align_dialog(control, event) -> None:
    try:
        _ensure_repo_on_path()
        _purge_modules("TPoseAligner")
        from TPoseAligner.ui import show_align_dialog
        show_align_dialog()
    except Exception:
        traceback.print_exc()


def _open_tpose_batch_dialog(control, event) -> None:
    try:
        _ensure_repo_on_path()
        _purge_modules("TPoseAligner")
        from TPoseAligner.ui import show_batch_dialog
        show_batch_dialog()
    except Exception:
        traceback.print_exc()


def _reset_tpose_offsets(control, event) -> None:
    try:
        _ensure_repo_on_path()
        _purge_modules("TPoseAligner")
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


def _on_tpose_menu(control, event) -> None:
    label = ""
    try:
        label = event.Name
    except Exception:
        pass
    if "Pair" in label:
        _open_tpose_align_dialog(control, event)
    elif "Batch" in label:
        _open_tpose_batch_dialog(control, event)
    elif "Reset" in label:
        _reset_tpose_offsets(control, event)


# ---------------------------------------------------------------------------
# Menu wiring
# ---------------------------------------------------------------------------

def _ensure_parent_menu(manager) -> None:
    """Create the shared "Tools" menu if it does not exist yet."""
    if manager.GetMenu(_PARENT_MENU_NAME) is None:
        manager.InsertBefore(None, "Help", _PARENT_MENU_NAME)


def install_all_menus() -> bool:
    """Register every shipped tool's submenu under the shared Tools menu.

    Idempotent: subsequent calls in the same MoBu session are no-ops.
    """
    global _INSTALLED
    if _INSTALLED:
        return True

    repo = _ensure_repo_on_path()
    print(f"install_menus: project root = {repo}")

    try:
        from pyfbsdk import FBMenuManager  # type: ignore
    except ImportError:
        print("install_menus: pyfbsdk not available, skipping.")
        return False

    try:
        manager = FBMenuManager()
        _ensure_parent_menu(manager)

        # Retargeter submenu
        if manager.GetMenu(_RETARGETER_PATH) is None:
            manager.InsertLast(_PARENT_MENU_NAME, _RETARGETER_SUBMENU)
        manager.InsertLast(_RETARGETER_PATH, "Open Retargeter Panel...")
        retargeter_menu = manager.GetMenu(_RETARGETER_PATH)
        if retargeter_menu is not None:
            retargeter_menu.OnMenuActivate.Add(_on_retargeter_menu)

        # TPoseAligner submenu
        if manager.GetMenu(_TPOSE_PATH) is None:
            manager.InsertLast(_PARENT_MENU_NAME, _TPOSE_SUBMENU)
        manager.InsertLast(_TPOSE_PATH, "T-Pose Pair Align...")
        manager.InsertLast(_TPOSE_PATH, "Batch Retarget...")
        manager.InsertLast(_TPOSE_PATH, "")
        manager.InsertLast(_TPOSE_PATH, "Reset Offsets on Current Character")
        tpose_menu = manager.GetMenu(_TPOSE_PATH)
        if tpose_menu is not None:
            tpose_menu.OnMenuActivate.Add(_on_tpose_menu)

        _INSTALLED = True
        print(
            f"install_menus: '{_PARENT_MENU_NAME}' menu installed "
            f"({_RETARGETER_SUBMENU}, {_TPOSE_SUBMENU})."
        )
        return True
    except Exception:
        traceback.print_exc()
        return False


if __name__ == "__main__" or os.environ.get("MOBU_TOOLS_AUTO_INSTALL", "1") == "1":
    install_all_menus()
