"""Aggregate installer that registers menus for every tool in this repo.

Drop this single file (or a symlink to it) into MotionBuilder's
``config/Scripts/Startup`` folder - typically:

    Documents\\MB\\<version>\\config\\Scripts\\Startup\\

On the next launch every shipped tool's submenu appears under a shared
top-level "Tools" menu. The per-package ``install_menu.py`` files remain
available for developers who want to install just one tool.

Equivalent to running, from MotionBuilder's Python Editor::

    from Retargeter.install_menu import install_menu as install_retargeter
    from TPoseAligner.install_menu import install_menu as install_tpose_aligner
    install_retargeter()
    install_tpose_aligner()

The script is robust to two common deployment styles:

* **Symlink** (recommended): ``__file__`` resolves to the real path
  inside the repo, so the project root is located trivially.
* **Copy**: ``__file__`` points at the startup folder, which does not
  contain the packages. We fall back to scanning ``sys.path`` and a few
  conventional project locations to find a folder that contains both
  ``Retargeter/`` and ``TPoseAligner/``.
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path


_TOOLS = ("Retargeter", "TPoseAligner")


def _looks_like_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return all(os.path.isdir(os.path.join(path, name)) for name in _TOOLS)


def _ensure_repo_on_path() -> str:
    """Locate the repo root and add it to ``sys.path``.

    Returns the located root for logging. Raises if it cannot be found.
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
        f"(expected sibling folders: {', '.join(_TOOLS)}). "
        "Add the project's parent directory to sys.path manually, or "
        "deploy this file as a symlink rather than a copy."
    )


def install_all_menus() -> dict[str, bool]:
    """Run every per-package ``install_menu`` we know about.

    A failure in one tool never prevents the others from being installed.
    Returns a mapping of package name -> success boolean for logging.
    """
    repo = _ensure_repo_on_path()
    print(f"install_menus: project root = {repo}")

    results: dict[str, bool] = {}
    for package in _TOOLS:
        try:
            module = importlib.import_module(f"{package}.install_menu")
            install_menu = getattr(module, "install_menu")
            results[package] = bool(install_menu())
        except Exception:
            results[package] = False
            traceback.print_exc()

    return results


if __name__ == "__main__" or os.environ.get("MOBU_TOOLS_AUTO_INSTALL", "1") == "1":
    install_all_menus()
