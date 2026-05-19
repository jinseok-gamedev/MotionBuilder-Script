"""Scene level helpers for HumanIK character discovery and validation.

The retargeter assumes the user has opened a "setting" FBX containing both a
Source and a Target HumanIK character that are already characterized. This
module surfaces the characters to the UI and runs cheap validation so the
operator gets a clear error before any heavy import / plot work starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from pyfbsdk import (  # type: ignore
    FBCharacter,
    FBSystem,
)


HIPS_SLOT = "HipsLink"
HEAD_SLOT = "HeadLink"
LEFT_HAND_SLOT = "LeftHandLink"
RIGHT_HAND_SLOT = "RightHandLink"
LEFT_FOOT_SLOT = "LeftFootLink"
RIGHT_FOOT_SLOT = "RightFootLink"

# Slots considered required for a usable HumanIK character. The full HIK
# definition has 50+ slots but if these are missing the rig is unusable for
# whole body retargeting.
REQUIRED_SLOTS = (
    HIPS_SLOT,
    HEAD_SLOT,
    LEFT_HAND_SLOT,
    RIGHT_HAND_SLOT,
    LEFT_FOOT_SLOT,
    RIGHT_FOOT_SLOT,
)


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class ValidationResult:
    ok: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.issues.append(ValidationIssue("error", msg))
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.issues.append(ValidationIssue("warning", msg))

    def format(self) -> str:
        if not self.issues:
            return "OK"
        return "\n".join(f"[{i.severity.upper()}] {i.message}" for i in self.issues)


def find_humanik_characters() -> List[FBCharacter]:
    """Return all FBCharacter objects currently in the scene."""
    scene = FBSystem().Scene
    return [c for c in scene.Characters]


def find_character_by_name(name: str) -> Optional[FBCharacter]:
    """Lookup a character by LongName or Name, returning None if missing."""
    if not name:
        return None
    for c in find_humanik_characters():
        if c.LongName == name or c.Name == name:
            return c
    return None


def get_slot_model(character: FBCharacter, slot: str):
    """Return the FBModel assigned to a HIK slot or None if empty.

    HIK slots are exposed as properties named e.g. ``HipsLink`` that contain a
    list of linked models (almost always a single entry).
    """
    prop = character.PropertyList.Find(slot)
    if prop is None:
        return None
    try:
        if len(prop) == 0:
            return None
        return prop[0]
    except Exception:
        return None


def is_characterized(character: FBCharacter) -> bool:
    """Robust check that a character has been Characterize()d.

    Some MotionBuilder versions return a bound method instead of a bool for
    ``GetCharacterize``; we normalise both code paths.
    """
    try:
        flag = character.GetCharacterize
        if callable(flag):
            flag = flag()
        return bool(flag)
    except Exception:
        return False


def validate_setup(source_name: str, target_name: str) -> ValidationResult:
    """Validate that the scene is ready for a retarget run.

    Checks performed:
        - Source / Target name are non-empty and resolve to scene characters
        - Source and Target are NOT the same character
        - Both are Characterize()d
        - Each has the minimum required HIK slots populated
    """
    result = ValidationResult(ok=True)

    if not source_name:
        result.add_error("Source character not selected.")
    if not target_name:
        result.add_error("Target character not selected.")
    if not result.ok:
        return result

    if source_name == target_name:
        result.add_error("Source and Target must be different characters.")
        return result

    source = find_character_by_name(source_name)
    target = find_character_by_name(target_name)

    if source is None:
        result.add_error(f"Source character '{source_name}' not found in scene.")
    if target is None:
        result.add_error(f"Target character '{target_name}' not found in scene.")
    if not result.ok:
        return result

    for label, char in (("Source", source), ("Target", target)):
        if not is_characterized(char):
            result.add_error(f"{label} character '{char.LongName}' is not Characterized.")
            continue
        for slot in REQUIRED_SLOTS:
            if get_slot_model(char, slot) is None:
                result.add_warning(
                    f"{label} '{char.LongName}': slot '{slot}' has no bone assigned."
                )

    return result


def list_character_names() -> List[str]:
    """Cheap helper for UI combo population."""
    return [c.LongName for c in find_humanik_characters()]


def get_character_namespace(character: FBCharacter) -> str:
    """Return the namespace prefix used by the character's bones, or ``""``.

    HumanIK characters drive skeletons by ``LongName``. If the rig was loaded
    inside a namespace (e.g. ``SrcRig:Hips``) MotionBuilder's merge-by-name
    will only match incoming bones that share the prefix. We sniff the Hips
    slot to discover that prefix so callers can ask the FBX merger to remap
    incoming bones into it.
    """
    if character is None:
        return ""
    hips = get_slot_model(character, HIPS_SLOT)
    if hips is None:
        return ""
    long_name = getattr(hips, "LongName", "") or ""
    short_name = getattr(hips, "Name", "") or ""
    if long_name.endswith(short_name) and long_name != short_name:
        prefix = long_name[: -len(short_name)]
        return prefix.rstrip(":")
    if ":" in long_name:
        return long_name.rsplit(":", 1)[0]
    return ""


def collect_scene_bone_names(character: FBCharacter) -> List[str]:
    """Names (without namespace) of every model linked into the character.

    Used as a quick "did the merge actually bind?" check after FileMerge.
    """
    out: List[str] = []
    for m in get_target_skeleton_models(character):
        n = getattr(m, "Name", "") or ""
        if n:
            out.append(n)
    return out


def get_target_skeleton_models(character: FBCharacter) -> List:
    """Collect every model linked into the character's HIK slots.

    Used when exporting: we want to select exactly the target rig's bones and
    not anything else that may live in the scene (lights, the source rig...).
    """
    seen = set()
    models = []
    for prop in character.PropertyList:
        name = prop.GetName()
        if not name.endswith("Link"):
            continue
        try:
            for i in range(len(prop)):
                m = prop[i]
                if m is None:
                    continue
                key = m.LongName
                if key in seen:
                    continue
                seen.add(key)
                models.append(m)
        except Exception:
            continue
    return models
