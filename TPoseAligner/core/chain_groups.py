"""Chain definitions for per-chain T-Pose alignment.

The chains are processed parent-first so that the parent's offset is
already applied (and visible in the world matrix) when the child is
aligned. This avoids cascading errors that would happen if (e.g.) an
upper arm's offset shifted the elbow's world position before the elbow
was measured.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Dict, List, Tuple


class ChainId(Enum):
    SPINE = auto()        # Hips, Spine*, Neck*, Head
    LEFT_ARM = auto()     # LeftCollar -> LeftShoulder -> LeftElbow -> LeftWrist
    RIGHT_ARM = auto()
    LEFT_LEG = auto()     # LeftHip -> LeftKnee -> LeftAnkle -> LeftFoot
    RIGHT_LEG = auto()
    LEFT_HAND = auto()    # Left finger bones
    RIGHT_HAND = auto()
    TWIST = auto()        # *Roll bones (handled separately, parent-synced)


CHAIN_TO_NODE_NAMES: Dict[ChainId, List[str]] = {
    ChainId.SPINE: [
        "kFBHipsNodeId",
        "kFBChestNodeId",
        "kFBSpine2NodeId",
        "kFBSpine3NodeId",
        "kFBSpine4NodeId",
        "kFBSpine5NodeId",
        "kFBSpine6NodeId",
        "kFBSpine7NodeId",
        "kFBSpine8NodeId",
        "kFBSpine9NodeId",
        "kFBNeckNodeId",
        "kFBNeck1NodeId",
        "kFBNeck2NodeId",
        "kFBNeck3NodeId",
        "kFBNeck4NodeId",
        "kFBNeck5NodeId",
        "kFBNeck6NodeId",
        "kFBNeck7NodeId",
        "kFBNeck8NodeId",
        "kFBNeck9NodeId",
        "kFBHeadNodeId",
    ],
    ChainId.LEFT_ARM: [
        "kFBLeftCollarNodeId",
        "kFBLeftShoulderNodeId",
        "kFBLeftElbowNodeId",
        "kFBLeftWristNodeId",
    ],
    ChainId.RIGHT_ARM: [
        "kFBRightCollarNodeId",
        "kFBRightShoulderNodeId",
        "kFBRightElbowNodeId",
        "kFBRightWristNodeId",
    ],
    ChainId.LEFT_LEG: [
        "kFBLeftHipNodeId",
        "kFBLeftKneeNodeId",
        "kFBLeftAnkleNodeId",
        "kFBLeftFootNodeId",
    ],
    ChainId.RIGHT_LEG: [
        "kFBRightHipNodeId",
        "kFBRightKneeNodeId",
        "kFBRightAnkleNodeId",
        "kFBRightFootNodeId",
    ],
    ChainId.LEFT_HAND: [
        "kFBLeftThumbANodeId", "kFBLeftThumbBNodeId", "kFBLeftThumbCNodeId",
        "kFBLeftIndexANodeId", "kFBLeftIndexBNodeId", "kFBLeftIndexCNodeId",
        "kFBLeftMiddleANodeId", "kFBLeftMiddleBNodeId", "kFBLeftMiddleCNodeId",
        "kFBLeftRingANodeId", "kFBLeftRingBNodeId", "kFBLeftRingCNodeId",
        "kFBLeftPinkyANodeId", "kFBLeftPinkyBNodeId", "kFBLeftPinkyCNodeId",
    ],
    ChainId.RIGHT_HAND: [
        "kFBRightThumbANodeId", "kFBRightThumbBNodeId", "kFBRightThumbCNodeId",
        "kFBRightIndexANodeId", "kFBRightIndexBNodeId", "kFBRightIndexCNodeId",
        "kFBRightMiddleANodeId", "kFBRightMiddleBNodeId", "kFBRightMiddleCNodeId",
        "kFBRightRingANodeId", "kFBRightRingBNodeId", "kFBRightRingCNodeId",
        "kFBRightPinkyANodeId", "kFBRightPinkyBNodeId", "kFBRightPinkyCNodeId",
    ],
    ChainId.TWIST: [
        "kFBLeftShoulderRollNodeId", "kFBLeftShoulderRollNode1Id",
        "kFBLeftShoulderRollNode2Id", "kFBLeftShoulderRollNode3Id",
        "kFBLeftShoulderRollNode4Id", "kFBLeftShoulderRollNode5Id",
        "kFBLeftElbowRollNodeId", "kFBLeftElbowRollNode1Id",
        "kFBLeftElbowRollNode2Id", "kFBLeftElbowRollNode3Id",
        "kFBLeftElbowRollNode4Id", "kFBLeftElbowRollNode5Id",
        "kFBRightShoulderRollNodeId", "kFBRightShoulderRollNode1Id",
        "kFBRightShoulderRollNode2Id", "kFBRightShoulderRollNode3Id",
        "kFBRightShoulderRollNode4Id", "kFBRightShoulderRollNode5Id",
        "kFBRightElbowRollNodeId", "kFBRightElbowRollNode1Id",
        "kFBRightElbowRollNode2Id", "kFBRightElbowRollNode3Id",
        "kFBRightElbowRollNode4Id", "kFBRightElbowRollNode5Id",
        "kFBLeftHipRollNodeId", "kFBLeftHipRollNode1Id",
        "kFBLeftHipRollNode2Id", "kFBLeftHipRollNode3Id",
        "kFBLeftHipRollNode4Id", "kFBLeftHipRollNode5Id",
        "kFBLeftKneeRollNodeId", "kFBLeftKneeRollNode1Id",
        "kFBLeftKneeRollNode2Id", "kFBLeftKneeRollNode3Id",
        "kFBLeftKneeRollNode4Id", "kFBLeftKneeRollNode5Id",
        "kFBRightHipRollNodeId", "kFBRightHipRollNode1Id",
        "kFBRightHipRollNode2Id", "kFBRightHipRollNode3Id",
        "kFBRightHipRollNode4Id", "kFBRightHipRollNode5Id",
        "kFBRightKneeRollNodeId", "kFBRightKneeRollNode1Id",
        "kFBRightKneeRollNode2Id", "kFBRightKneeRollNode3Id",
        "kFBRightKneeRollNode4Id", "kFBRightKneeRollNode5Id",
    ],
}


CHAIN_PROCESS_ORDER: Tuple[ChainId, ...] = (
    ChainId.SPINE,
    ChainId.LEFT_ARM,
    ChainId.RIGHT_ARM,
    ChainId.LEFT_LEG,
    ChainId.RIGHT_LEG,
    ChainId.LEFT_HAND,
    ChainId.RIGHT_HAND,
    ChainId.TWIST,
)


CHAIN_DISPLAY_NAMES: Dict[ChainId, str] = {
    ChainId.SPINE: "Spine and Head",
    ChainId.LEFT_ARM: "Left Arm",
    ChainId.RIGHT_ARM: "Right Arm",
    ChainId.LEFT_LEG: "Left Leg",
    ChainId.RIGHT_LEG: "Right Leg",
    ChainId.LEFT_HAND: "Left Hand (fingers)",
    ChainId.RIGHT_HAND: "Right Hand (fingers)",
    ChainId.TWIST: "Twist / Roll bones",
}


def all_canonical_node_names() -> List[str]:
    """Flat list of every body-node name handled by chain alignment."""
    out: List[str] = []
    for chain in CHAIN_PROCESS_ORDER:
        out.extend(CHAIN_TO_NODE_NAMES[chain])
    return out


def chain_for_node(node_name: str) -> ChainId:
    """Reverse lookup: which chain does a given body-node belong to."""
    for chain, names in CHAIN_TO_NODE_NAMES.items():
        if node_name in names:
            return chain
    raise KeyError(f"No chain owns body node {node_name!r}")


_CHAIN_TO_NODES_LAZY: Dict[ChainId, List] = {}


def CHAIN_TO_NODES() -> Dict[ChainId, List]:
    """Resolve chain definitions to actual ``FBBodyNodeId`` enum values.

    Cached after first call. Returns an empty list for any name that is not
    available in the running MotionBuilder version.
    """
    global _CHAIN_TO_NODES_LAZY
    if _CHAIN_TO_NODES_LAZY:
        return _CHAIN_TO_NODES_LAZY
    from .canonical_pose import resolve_node_id

    out: Dict[ChainId, List] = {}
    for chain, names in CHAIN_TO_NODE_NAMES.items():
        resolved = []
        for name in names:
            node_id = resolve_node_id(name)
            if node_id is not None:
                resolved.append(node_id)
        out[chain] = resolved
    _CHAIN_TO_NODES_LAZY = out
    return out


def all_canonical_nodes() -> List:
    """Flat list of every resolvable ``FBBodyNodeId`` handled by alignment."""
    out: List = []
    for chain in CHAIN_PROCESS_ORDER:
        out.extend(CHAIN_TO_NODES().get(chain, []))
    return out
