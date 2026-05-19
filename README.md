# MotionBuilder-Script

Autodesk MotionBuilder Python scripts and utilities. Each tool ships as
a self-contained Python package; a top-level installer wires them all up
into a single shared menu inside MotionBuilder.

## Structure

- `Retargeter/` — HumanIK-based retargeting hub for moving animation
  between 3ds Max, Maya, and Unreal Engine 5 through FBX. See
  [`Retargeter/README.md`](Retargeter/README.md).
- `TPoseAligner/` — Non-destructive HumanIK T-Pose alignment between
  source / target characters using `FBCharacter.SetROffset`. See
  [`TPoseAligner/README.md`](TPoseAligner/README.md).
- `install_menus.py` — aggregate installer that registers every tool's
  menu under a single shared "Tools" menu (see "Quick install" below).

## Requirements

- Autodesk MotionBuilder 2020 or newer (with Python scripting enabled)
- Python interpreter shipped with MotionBuilder
  - 2025/2026: PySide6 / Python 3.11
  - 2020-2024: PySide2 / Python 3.7+

## Quick install (recommended)

Drop `install_menus.py` (or a symlink to it) into MotionBuilder's
startup folder so every tool's menu installs automatically on launch:

- Windows: `Documents\MB\<version>\config\Scripts\Startup\`

The aggregate installer self-locates the repo via `__file__`, falls back
to scanning `sys.path` and conventional project paths, and registers each
tool's submenu independently. A failure in one tool's submenu never
blocks the others.

After restart, MotionBuilder's menubar gets a single **Tools** menu:

```
Tools
├── Retargeter
│   └── Open Retargeter Panel...
└── TPose Align
    ├── T-Pose Pair Align...
    ├── Batch Retarget...
    ├── ─────────────
    └── Reset Offsets on Current Character
```

### PowerShell symlink (preferred over copy)

A symlink keeps the startup file in sync with the repo - edits in
Cursor land in MotionBuilder on the next launch with no extra steps.
Run from an elevated PowerShell or with Developer Mode enabled:

```powershell
$repo   = "C:\Users\jinseok.park\Desktop\MotionBuilder-Script"
$mbVer  = "2026"
$target = "$HOME\Documents\MB\$mbVer\config\Scripts\Startup\install_menus.py"

New-Item -ItemType SymbolicLink -Path $target -Target "$repo\install_menus.py"
```

If you cannot create symlinks, copy `install_menus.py` to the same
location instead - the script's path-resolution fallback locates the
repo via common project folders (`Desktop\MotionBuilder-Script`,
`Documents\MotionBuilder-Script`).

## Manual install from the Python Editor

If you cannot copy files into the Startup folder (e.g. locked-down
production setup), call the installer directly from MotionBuilder's
Python Editor on every launch:

```python
import sys
sys.path.insert(0, r"C:\path\to\MotionBuilder-Script")
import install_menus  # auto-registers menus on import
```

Or, if you want explicit control over when the menu appears:

```python
import os
os.environ["MOBU_TOOLS_AUTO_INSTALL"] = "0"  # disable on-import auto-run
import install_menus
install_menus.install_all_menus()
```

## Environment variables

Both variables are optional and read by `install_menus.py`.

| Variable | Default | Effect |
|---|---|---|
| `MOBU_TOOLS_AUTO_INSTALL` | `1` | Set to `0` to skip the on-import auto-run, so a caller can invoke `install_menus.install_all_menus()` at a controlled point (e.g. from a custom startup script). |
| `MOBU_TOOLS_LOG_LEVEL` | `INFO` | Python `logging` level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. `DEBUG` adds idempotent-skip messages and is useful when diagnosing why a menu did not appear. |

Set them in MotionBuilder's launch environment, in your shell before
launching `motionbuilder.exe`, or programmatically before importing the
installer:

```python
import os
os.environ["MOBU_TOOLS_LOG_LEVEL"] = "DEBUG"
import install_menus
```

## Adding a new tool

Each tool is a Python package at the repo root with its own
`__init__.py`, plus an entry in `install_menus.py`'s `_TOOLS` registry.
To add a tool:

1. Drop the package folder next to `Retargeter/` and `TPoseAligner/`
   (and update `_REQUIRED_PACKAGES` in `install_menus.py` if the path
   resolver should consider it mandatory).
2. In `install_menus.py`, add menu callback function(s) that take
   `(control, event)`, do their work, and log via `_logger`.
3. Append a `ToolMenu(submenu_name=..., items=(MenuItem(...), ...))`
   entry to `_TOOLS`. Empty-label items become separators.

The next MotionBuilder launch picks the new submenu up automatically -
no other code changes are required.

## Usage

After install, every tool launches from the **Tools** menu in
MotionBuilder. For one-shot launches without installing the menu, see
the per-tool README's "Running" section.
