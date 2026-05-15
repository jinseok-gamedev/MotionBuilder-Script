"""TPoseAligner core package."""

from .tpose_align import (
    AlignOptions,
    AlignResult,
    align_character_to_canonical_tpose,
    align_pair,
    connect_for_retarget,
)
from .snapshot import OffsetSnapshot, capture, restore, reset_all_offsets
from .validation import (
    Severity,
    OffsetGrade,
    assert_y_up_scene,
    is_y_up_scene,
    categorize_offset,
    compare_proportions,
    ProportionReport,
)
from .preset_io import save_preset, load_preset, list_presets, default_presets_dir
from .chain_groups import ChainId, CHAIN_TO_NODES, all_canonical_nodes

__all__ = [
    "AlignOptions",
    "AlignResult",
    "align_character_to_canonical_tpose",
    "align_pair",
    "connect_for_retarget",
    "OffsetSnapshot",
    "capture",
    "restore",
    "reset_all_offsets",
    "Severity",
    "OffsetGrade",
    "assert_y_up_scene",
    "is_y_up_scene",
    "categorize_offset",
    "compare_proportions",
    "ProportionReport",
    "save_preset",
    "load_preset",
    "list_presets",
    "default_presets_dir",
    "ChainId",
    "CHAIN_TO_NODES",
    "all_canonical_nodes",
]
