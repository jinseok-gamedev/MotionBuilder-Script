"""Retargeter core package.

Contains the MotionBuilder API-bound logic: scene discovery, FBX import/export,
take management, HumanIK retargeting and root motion handling.

All modules in this package assume they run inside MotionBuilder (pyfbsdk
available). UI code lives in the sibling ``ui`` package.
"""

__all__ = [
    "scene_utils",
    "fbx_io",
    "take_manager",
    "retarget_engine",
    "root_motion",
]
