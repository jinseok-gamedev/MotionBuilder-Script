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

Adding a new tool requires only:

1. Defining its callback function(s) in this module.
2. Appending a :class:`ToolMenu` entry to :data:`_TOOLS`.

Environment variables:

* ``MOBU_TOOLS_AUTO_INSTALL=0`` skips the on-import auto-run, so callers
  can invoke :func:`install_all_menus` at a controlled point.
* ``MOBU_TOOLS_LOG_LEVEL`` (default ``INFO``) controls logging
  verbosity. Accepts any standard ``logging`` level name: ``DEBUG``,
  ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


_PARENT_MENU_NAME = "Tools"
_REQUIRED_PACKAGES = ("Retargeter", "TPoseAligner")

# Persistent install state.
#
# A plain module-level ``_INSTALLED`` flag is reset to ``False`` every time
# this file is re-parsed - and the most common deployment is to call it via
# ``exec(open(r"...\\install_menus.py").read())`` from MotionBuilder's Python
# editor, which DOES re-parse the file on every run. The flag therefore has
# to live somewhere the second exec can still see, so we stash a tiny state
# object on the ``sys`` module (which survives anything short of restarting
# MoBu). The same object also holds dispatcher closures so MoBu's menu event
# manager keeps a live reference after this module's globals are dropped.
_PERSIST_ATTR = "_mobu_install_menus_state"


class _InstallState:
    __slots__ = ("installed", "dispatchers")

    def __init__(self) -> None:
        self.installed: bool = False
        self.dispatchers: list = []


def _persistent_state() -> _InstallState:
    state = getattr(sys, _PERSIST_ATTR, None)
    if not isinstance(state, _InstallState):
        state = _InstallState()
        setattr(sys, _PERSIST_ATTR, state)
    return state


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_logger = logging.getLogger("install_menus")


def _setup_logging() -> None:
    """Configure the ``install_menus`` logger from ``MOBU_TOOLS_LOG_LEVEL``.

    Idempotent: handlers are only attached the first time. ``propagate``
    is disabled so we do not double-print through MotionBuilder's root
    logger when the host configures one of its own.
    """
    level_name = os.environ.get("MOBU_TOOLS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    _logger.setLevel(level)
    if not _logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[install_menus] %(levelname)s: %(message)s")
        )
        _logger.addHandler(handler)
        _logger.propagate = False


# ---------------------------------------------------------------------------
# Declarative menu schema
# ---------------------------------------------------------------------------

# MotionBuilder OnMenuActivate handlers receive (control, event).
MenuCallback = Callable[[object, object], None]


@dataclass(frozen=True)
class MenuItem:
    """One row inside a tool's submenu. ``label=""`` becomes a separator."""

    label: str
    callback: MenuCallback | None = None

    @property
    def is_separator(self) -> bool:
        return not self.label


@dataclass(frozen=True)
class ToolMenu:
    """A submenu registered under the shared "Tools" parent menu."""

    submenu_name: str
    items: tuple[MenuItem, ...]


# ---------------------------------------------------------------------------
# Path / module bookkeeping
# ---------------------------------------------------------------------------

def _looks_like_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return all(os.path.isdir(os.path.join(path, name)) for name in _REQUIRED_PACKAGES)


def _ensure_repo_on_path() -> str:
    """Locate the repo root and add it to ``sys.path``.

    Returns the resolved path. Raises :class:`RuntimeError` if it cannot
    be found.
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
        _logger.exception("open Retargeter panel failed")


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
        _logger.exception("open TPose Pair Align dialog failed")


def _open_tpose_batch_dialog(control, event) -> None:
    try:
        _ensure_repo_on_path()
        _purge_modules("TPoseAligner")
        from TPoseAligner.ui import show_batch_dialog
        show_batch_dialog()
    except Exception:
        _logger.exception("open TPose Batch dialog failed")


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
        _logger.exception("reset TPose offsets failed")


# ---------------------------------------------------------------------------
# Tool registry - add new tools by appending an entry here
# ---------------------------------------------------------------------------

_TOOLS: tuple[ToolMenu, ...] = (
    ToolMenu(
        submenu_name="Retargeter",
        items=(
            MenuItem("Open Retargeter Panel...", _open_retargeter_panel),
        ),
    ),
    ToolMenu(
        submenu_name="TPose Align",
        items=(
            MenuItem("T-Pose Pair Align...", _open_tpose_align_dialog),
            MenuItem("Batch Retarget...", _open_tpose_batch_dialog),
            MenuItem(""),
            MenuItem("Reset Offsets on Current Character", _reset_tpose_offsets),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Menu wiring
# ---------------------------------------------------------------------------

def _ensure_parent_menu(manager) -> None:
    """Create the shared "Tools" menu if it does not exist yet."""
    if manager.GetMenu(_PARENT_MENU_NAME) is None:
        manager.InsertBefore(None, "Help", _PARENT_MENU_NAME)


def _make_dispatcher(label_to_callback: dict[str, MenuCallback]) -> MenuCallback:
    """Build an OnMenuActivate handler that dispatches by clicked label.

    Falls back to substring matching if MotionBuilder ever decorates the
    label (e.g. with accelerator key prefixes) before passing it back.
    """

    def _dispatch(control, event):
        clicked = ""
        try:
            clicked = event.Name
        except Exception:
            return
        cb = label_to_callback.get(clicked)
        if cb is None:
            for label, candidate in label_to_callback.items():
                if label and (label in clicked or clicked in label):
                    cb = candidate
                    break
        if cb is not None:
            cb(control, event)

    return _dispatch


def _register_tool(manager, tool: ToolMenu) -> None:
    """Add one :class:`ToolMenu` under the shared parent and wire callbacks.

    Idempotent at the submenu level: if a submenu with the same path already
    exists we assume a previous install populated it, and skip item insertion
    entirely so re-running this file does not stack duplicate rows like
    "Open Retargeter Panel..., Open Retargeter Panel...".

    Note on call ordering: ``FBMenuManager.GetMenu("Tools/Retargeter")`` only
    starts returning a non-None ``FBGenericMenu`` once at least one item has
    been inserted into that path. So the lookup must happen AFTER the item
    insertion loop, not right after ``InsertLast(parent, submenu_name)``.
    """
    submenu_path = f"{_PARENT_MENU_NAME}/{tool.submenu_name}"

    if manager.GetMenu(submenu_path) is not None:
        _logger.debug(
            "submenu %r already exists; skipping item insertion.", submenu_path
        )
        return

    manager.InsertLast(_PARENT_MENU_NAME, tool.submenu_name)

    label_to_callback: dict[str, MenuCallback] = {}
    for item in tool.items:
        manager.InsertLast(submenu_path, item.label)
        if not item.is_separator and item.callback is not None:
            label_to_callback[item.label] = item.callback

    submenu = manager.GetMenu(submenu_path)
    if submenu is None:
        _logger.error("failed to retrieve submenu %r", submenu_path)
        return

    if label_to_callback:
        dispatcher = _make_dispatcher(label_to_callback)
        # Stash on the persistent state so MoBu's event manager keeps a live
        # reference even after this module's globals are GC'd by a reload.
        _persistent_state().dispatchers.append(dispatcher)
        submenu.OnMenuActivate.Add(dispatcher)

    _logger.info(
        "registered submenu %r (%d active item(s))",
        submenu_path,
        len(label_to_callback),
    )


def install_all_menus() -> bool:
    """Register every tool's submenu under the shared Tools menu.

    Idempotent across MoBu sessions AND across reloads of this file: the
    "already installed" flag lives on ``sys`` (see :func:`_persistent_state`),
    so a second ``exec(open(...).read())`` is a no-op instead of doubling the
    menu items.
    """
    _setup_logging()
    state = _persistent_state()

    if state.installed:
        _logger.debug("install_all_menus: already installed; skipping.")
        return True

    repo = _ensure_repo_on_path()
    _logger.info("project root = %s", repo)

    try:
        from pyfbsdk import FBMenuManager  # type: ignore
    except ImportError:
        _logger.warning("pyfbsdk not available; skipping menu install.")
        return False

    try:
        manager = FBMenuManager()
        _ensure_parent_menu(manager)
        for tool in _TOOLS:
            _register_tool(manager, tool)

        state.installed = True
        _logger.info(
            "%r menu installed (%s).",
            _PARENT_MENU_NAME,
            ", ".join(t.submenu_name for t in _TOOLS),
        )
        return True
    except Exception:
        _logger.exception("install_all_menus failed")
        return False


if __name__ == "__main__" or os.environ.get("MOBU_TOOLS_AUTO_INSTALL", "1") == "1":
    install_all_menus()
