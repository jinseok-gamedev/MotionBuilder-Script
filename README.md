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
to scanning `sys.path` and conventional project paths, and runs each
tool's `install_menu.install_menu()` independently. A failure in one
tool never blocks the others.

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

## Manual install (one tool at a time)

If you only want one of the tools, use that package's own
`install_menu.py` - see the per-tool README for details. Each tool's
installer is fully independent and can be triggered from the Python
Editor without `install_menus.py`.

## Usage

After install, every tool launches from the **Tools** menu in
MotionBuilder. For one-shot launches without installing the menu, see
the per-tool README's "Running" section.
