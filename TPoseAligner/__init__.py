"""TPoseAligner - MotionBuilder T-Pose auto alignment for HumanIK retargeting.

A non-destructive tool that aligns the stance pose of two HumanIK characters
to a canonical T-Pose using FBCharacter rotation offsets, dramatically
improving retargeting quality.

Public API:
    from TPoseAligner.core import align_pair, AlignOptions, ChainId
    from TPoseAligner.ui import show_align_dialog, show_batch_dialog
    from TPoseAligner.batch import batch_retarget
"""

__version__ = "0.1.0"
__author__ = "TPoseAligner Contributors"
