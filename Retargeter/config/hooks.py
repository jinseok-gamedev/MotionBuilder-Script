"""User extension hooks.

The pipeline imports this module and calls any hook functions that exist. The
default implementations are intentionally no-ops; copy this file into a
project location, edit, and the pipeline will pick up your overrides.

All hook callables are optional. If a hook raises an exception the pipeline
logs it and continues with the next take (failure is isolated to one item).

Available hooks
---------------

``pre_import(fbx_path: str) -> None``
    Called before each source FBX is merged. Use to e.g. verify the file
    matches a naming convention or to skip-flag certain files.

``post_import(fbx_path: str, take) -> None``
    Called after a take has been created from a source FBX. Use to remap
    audio takes, fix unit scale on imported skeletons, etc.

``pre_plot(target_character, source_character, take) -> None``
    Called immediately before ``PlotAnimation``. The HumanIK input is already
    wired. Use to tweak HIK properties (Reach values, Floor Contact) per take.

``post_plot(target_character, take) -> None``
    Called after plotting and before root motion processing. Use for custom
    cleanup (smoothing filters, key reduction, IK adjustments...).

``pre_export(take, target_character, out_path: str, metadata) -> None``
    Called before each take is saved to FBX. Mutate ``metadata.extras`` to
    inject custom provenance, or rename ``out_path`` for special-casing.

``post_export(take, target_character, out_path: str, metadata) -> None``
    Called after a successful export. Use to copy the FBX to a network share,
    register it in an asset DB, kick off a Maya / Max / UE5 import, etc.
"""

from __future__ import annotations


def pre_import(fbx_path):  # pragma: no cover
    pass


def post_import(fbx_path, take):  # pragma: no cover
    pass


def pre_plot(target_character, source_character, take):  # pragma: no cover
    pass


def post_plot(target_character, take):  # pragma: no cover
    pass


def pre_export(take, target_character, out_path, metadata):  # pragma: no cover
    pass


def post_export(take, target_character, out_path, metadata):  # pragma: no cover
    pass
