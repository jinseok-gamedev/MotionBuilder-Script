"""Canonical T-Pose definitions for MotionBuilder HumanIK body nodes.

For each ``FBBodyNodeId`` we describe the canonical T-Pose by two world-space
direction vectors:

- ``primary``: the direction the bone's "tip" (the child end of the bone)
  should point in world space when the character is in canonical T-Pose.
- ``up``: a secondary direction used to disambiguate twist around the
  primary axis. Conceptually this is "the direction the top of the bone
  faces". For example a leg's primary direction is ``-Y`` and its up
  is ``+Z`` so that the knee faces forward (and not sideways).

MotionBuilder uses the standard Y-up, +Z-forward convention for HIK
characters. Character LEFT is the character's anatomical left, which is
``+X`` in world space when the character faces ``+Z``.

This module is intentionally pure-data (no pyfbsdk import at module load) so
it can be inspected from documentation / preset tooling without a running
MotionBuilder. The ``FBBodyNodeId`` symbols are looked up lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .math_utils import Vec3

WORLD_UP: Vec3 = (0.0, 1.0, 0.0)
WORLD_DOWN: Vec3 = (0.0, -1.0, 0.0)
WORLD_FORWARD: Vec3 = (0.0, 0.0, 1.0)
WORLD_BACK: Vec3 = (0.0, 0.0, -1.0)
WORLD_LEFT: Vec3 = (1.0, 0.0, 0.0)
WORLD_RIGHT: Vec3 = (-1.0, 0.0, 0.0)


@dataclass(frozen=True)
class CanonicalDir:
    """Canonical orientation for a single bone in world space."""

    primary: Vec3   # direction from bone origin towards its child
    up: Vec3        # secondary direction used to lock twist


_NAME_TO_DIR: Dict[str, CanonicalDir] = {
    "kFBHipsNodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBChestNodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine2NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine3NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine4NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine5NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine6NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine7NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine8NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBSpine9NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeckNodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck1NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck2NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck3NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck4NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck5NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck6NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck7NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck8NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBNeck9NodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),
    "kFBHeadNodeId": CanonicalDir(WORLD_UP, WORLD_FORWARD),

    "kFBLeftCollarNodeId": CanonicalDir(WORLD_LEFT, WORLD_UP),
    "kFBLeftShoulderNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftElbowNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftWristNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),

    "kFBRightCollarNodeId": CanonicalDir(WORLD_RIGHT, WORLD_UP),
    "kFBRightShoulderNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightElbowNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightWristNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),

    "kFBLeftHipNodeId": CanonicalDir(WORLD_DOWN, WORLD_FORWARD),
    "kFBLeftKneeNodeId": CanonicalDir(WORLD_DOWN, WORLD_FORWARD),
    "kFBLeftAnkleNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBLeftFootNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),

    "kFBRightHipNodeId": CanonicalDir(WORLD_DOWN, WORLD_FORWARD),
    "kFBRightKneeNodeId": CanonicalDir(WORLD_DOWN, WORLD_FORWARD),
    "kFBRightAnkleNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBRightFootNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),

    "kFBLeftThumbANodeId": CanonicalDir((0.7, 0.0, 0.7), WORLD_UP),
    "kFBLeftThumbBNodeId": CanonicalDir((0.7, 0.0, 0.7), WORLD_UP),
    "kFBLeftThumbCNodeId": CanonicalDir((0.7, 0.0, 0.7), WORLD_UP),
    "kFBLeftIndexANodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftIndexBNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftIndexCNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftMiddleANodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftMiddleBNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftMiddleCNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftRingANodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftRingBNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftRingCNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftPinkyANodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftPinkyBNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),
    "kFBLeftPinkyCNodeId": CanonicalDir(WORLD_LEFT, WORLD_BACK),

    "kFBRightThumbANodeId": CanonicalDir((-0.7, 0.0, 0.7), WORLD_UP),
    "kFBRightThumbBNodeId": CanonicalDir((-0.7, 0.0, 0.7), WORLD_UP),
    "kFBRightThumbCNodeId": CanonicalDir((-0.7, 0.0, 0.7), WORLD_UP),
    "kFBRightIndexANodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightIndexBNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightIndexCNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightMiddleANodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightMiddleBNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightMiddleCNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightRingANodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightRingBNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightRingCNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightPinkyANodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightPinkyBNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),
    "kFBRightPinkyCNodeId": CanonicalDir(WORLD_RIGHT, WORLD_BACK),

    "kFBLeftFootIndexANodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBLeftFootIndexBNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBLeftFootMiddleANodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBLeftFootMiddleBNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBRightFootIndexANodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBRightFootIndexBNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBRightFootMiddleANodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
    "kFBRightFootMiddleBNodeId": CanonicalDir(WORLD_FORWARD, WORLD_UP),
}

ROLL_BONE_NAMES = frozenset({
    "kFBLeftShoulderRollNodeId",
    "kFBLeftShoulderRollNode1Id",
    "kFBLeftShoulderRollNode2Id",
    "kFBLeftShoulderRollNode3Id",
    "kFBLeftShoulderRollNode4Id",
    "kFBLeftShoulderRollNode5Id",
    "kFBLeftElbowRollNodeId",
    "kFBLeftElbowRollNode1Id",
    "kFBLeftElbowRollNode2Id",
    "kFBLeftElbowRollNode3Id",
    "kFBLeftElbowRollNode4Id",
    "kFBLeftElbowRollNode5Id",
    "kFBRightShoulderRollNodeId",
    "kFBRightShoulderRollNode1Id",
    "kFBRightShoulderRollNode2Id",
    "kFBRightShoulderRollNode3Id",
    "kFBRightShoulderRollNode4Id",
    "kFBRightShoulderRollNode5Id",
    "kFBRightElbowRollNodeId",
    "kFBRightElbowRollNode1Id",
    "kFBRightElbowRollNode2Id",
    "kFBRightElbowRollNode3Id",
    "kFBRightElbowRollNode4Id",
    "kFBRightElbowRollNode5Id",
    "kFBLeftHipRollNodeId",
    "kFBLeftHipRollNode1Id",
    "kFBLeftHipRollNode2Id",
    "kFBLeftHipRollNode3Id",
    "kFBLeftHipRollNode4Id",
    "kFBLeftHipRollNode5Id",
    "kFBLeftKneeRollNodeId",
    "kFBLeftKneeRollNode1Id",
    "kFBLeftKneeRollNode2Id",
    "kFBLeftKneeRollNode3Id",
    "kFBLeftKneeRollNode4Id",
    "kFBLeftKneeRollNode5Id",
    "kFBRightHipRollNodeId",
    "kFBRightHipRollNode1Id",
    "kFBRightHipRollNode2Id",
    "kFBRightHipRollNode3Id",
    "kFBRightHipRollNode4Id",
    "kFBRightHipRollNode5Id",
    "kFBRightKneeRollNodeId",
    "kFBRightKneeRollNode1Id",
    "kFBRightKneeRollNode2Id",
    "kFBRightKneeRollNode3Id",
    "kFBRightKneeRollNode4Id",
    "kFBRightKneeRollNode5Id",
})


_PARENT_OF_ROLL: Dict[str, str] = {
    name: name.replace("Roll", "").rstrip("0123456789").rstrip("Node")
    for name in ROLL_BONE_NAMES
}
_PARENT_OF_ROLL.update({
    "kFBLeftShoulderRollNodeId": "kFBLeftShoulderNodeId",
    "kFBLeftElbowRollNodeId": "kFBLeftElbowNodeId",
    "kFBRightShoulderRollNodeId": "kFBRightShoulderNodeId",
    "kFBRightElbowRollNodeId": "kFBRightElbowNodeId",
    "kFBLeftHipRollNodeId": "kFBLeftHipNodeId",
    "kFBLeftKneeRollNodeId": "kFBLeftKneeNodeId",
    "kFBRightHipRollNodeId": "kFBRightHipNodeId",
    "kFBRightKneeRollNodeId": "kFBRightKneeNodeId",
})
for prefix, parent in [
    ("kFBLeftShoulderRollNode", "kFBLeftShoulderNodeId"),
    ("kFBLeftElbowRollNode", "kFBLeftElbowNodeId"),
    ("kFBRightShoulderRollNode", "kFBRightShoulderNodeId"),
    ("kFBRightElbowRollNode", "kFBRightElbowNodeId"),
    ("kFBLeftHipRollNode", "kFBLeftHipNodeId"),
    ("kFBLeftKneeRollNode", "kFBLeftKneeNodeId"),
    ("kFBRightHipRollNode", "kFBRightHipNodeId"),
    ("kFBRightKneeRollNode", "kFBRightKneeNodeId"),
]:
    for i in range(1, 6):
        _PARENT_OF_ROLL[f"{prefix}{i}Id"] = parent


def canonical_dir_for(node_name: str) -> Optional[CanonicalDir]:
    """Return the canonical direction for the given ``FBBodyNodeId`` name."""
    return _NAME_TO_DIR.get(node_name)


def is_roll_bone(node_name: str) -> bool:
    return node_name in ROLL_BONE_NAMES


def parent_of_roll(node_name: str) -> Optional[str]:
    """For a roll/twist bone, return the name of its parent body node."""
    return _PARENT_OF_ROLL.get(node_name)


def all_canonical_node_names():
    """Iterable of every node name that has a canonical direction defined."""
    return _NAME_TO_DIR.keys()


def resolve_node_id(node_name: str):
    """Resolve a node name string to the actual ``FBBodyNodeId`` enum value.

    Looked up lazily to avoid importing ``pyfbsdk`` outside MotionBuilder.
    Returns ``None`` if the symbol is not defined in the running version.
    """
    from pyfbsdk import FBBodyNodeId  # type: ignore
    return getattr(FBBodyNodeId, node_name, None)


def name_for_node_id(node_id) -> Optional[str]:
    """Inverse of :func:`resolve_node_id` - find the string name of an enum.

    Slow (linear scan) but only used at logging time for a handful of bones.
    """
    from pyfbsdk import FBBodyNodeId  # type: ignore
    for attr in dir(FBBodyNodeId):
        if not attr.startswith("kFB"):
            continue
        try:
            value = getattr(FBBodyNodeId, attr)
        except Exception:
            continue
        if value == node_id:
            return attr
    return None
