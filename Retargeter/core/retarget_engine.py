"""HumanIK retargeting primitives.

The retarget loop for one take is:

    link_input(target, source)
    plot_to_skeleton(target, opts)
    unbind_input(target)

``link_input`` wires the source character's evaluated pose into the target as
a live HumanIK input. ``plot_to_skeleton`` bakes the result onto the target's
skeleton FCurves so the rig can be exported as plain bone animation that
Max / Maya / UE5 can consume.

All operations are scoped to the currently active take (set by the caller),
unless ``plot_all_takes`` is True in :class:`PlotConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pyfbsdk import (  # type: ignore
    FBCharacter,
    FBCharacterInputType,
    FBCharacterPlotWhere,
    FBPlotOptions,
    FBRotationFilter,
    FBTime,
)


# Property names that toggle HumanIK's "Match Source" retargeting behaviour.
# These live on the character's PropertyList and the precise spelling has
# varied between MotionBuilder versions, so we try a small alias set.
_MATCH_SOURCE_PROPERTY_CANDIDATES = (
    "Match Source",
    "MatchSource",
)


@dataclass
class PlotConfig:
    """User-facing plot options serialised from the UI / settings JSON."""

    plot_rate: int = 30
    plot_translation: bool = True
    use_constant_key_reducer: bool = False
    constant_key_reducer_keep_one: bool = True
    precise_time_discontinuities: bool = True
    plot_all_takes: bool = False
    rotation_filter: str = "gimble_killer"  # "none" | "gimble_killer" | "unroll"

    def to_fb_plot_options(self) -> FBPlotOptions:
        opts = FBPlotOptions()
        opts.PlotAllTakes = bool(self.plot_all_takes)
        opts.PlotOnFrame = True
        opts.PlotPeriod = _fb_time_for_rate(self.plot_rate)
        opts.UseConstantKeyReducer = bool(self.use_constant_key_reducer)
        opts.ConstantKeyReducerKeepOneKey = bool(self.constant_key_reducer_keep_one)
        opts.PreciseTimeDiscontinuities = bool(self.precise_time_discontinuities)
        opts.RotationFilterToApply = _rotation_filter_enum(self.rotation_filter)
        # Plot Hips translation so foot-on-floor information survives the bake.
        # If the user only wants joint rotations they can untick the option.
        try:
            opts.PlotTranslationOnRootOnly = not bool(self.plot_translation)
        except AttributeError:
            pass
        return opts


def _fb_time_for_rate(rate: int) -> FBTime:
    """30 fps -> 1 frame at 30 fps, 60 fps -> 1 frame at 60 fps, etc.

    MotionBuilder 2026 tightened the ``FBTime(int, int, int, int, int, int)``
    constructor: the 6th positional argument is now strictly an ``FBTimeMode``
    enum, not a frame-rate int, so the old ``FBTime(0, 0, 0, 1, 0, rate)``
    form raises ``ArgumentError``. Building the period from seconds avoids
    needing a rate->FBTimeMode lookup table and works on every MoBu version.
    """
    rate = max(1, int(rate))
    t = FBTime()
    t.SetSecondDouble(1.0 / float(rate))
    return t


def _rotation_filter_enum(name: str):
    name = (name or "").lower()
    if name in ("none", "off", ""):
        return FBRotationFilter.kFBRotationFilterNone
    if name in ("gimble_killer", "gimble", "gimbalkiller", "gimbal_killer"):
        return FBRotationFilter.kFBRotationFilterGimbleKiller
    if name in ("unroll", "unrolled"):
        return FBRotationFilter.kFBRotationFilterUnroll
    return FBRotationFilter.kFBRotationFilterGimbleKiller


def link_input(target: FBCharacter, source: FBCharacter) -> None:
    """Set ``source`` as the live HIK input of ``target`` and activate it."""
    target.InputCharacter = source
    target.InputType = FBCharacterInputType.kFBCharacterInputCharacter
    target.ActiveInput = True


def unbind_input(target: FBCharacter) -> None:
    """Disconnect any live input so future operations are not driven by it.

    Deactivating ``ActiveInput`` is what actually stops the HumanIK input from
    driving the character. Clearing ``InputCharacter`` is best-effort: some
    MotionBuilder Python builds (Boost.Python) reject ``None`` for that slot
    with::

        Boost.Python.ArgumentError: None.None(FBCharacter, NoneType)

    so we try a couple of equivalents and silently fall back to "deactivated
    but still pointing at the old source", which is safe because the input is
    no longer active.
    """
    try:
        target.ActiveInput = False
    except Exception:
        pass
    try:
        target.InputCharacter = None
        return
    except Exception:
        pass
    try:
        target.InputType = FBCharacterInputType.kFBCharacterInputStance
    except Exception:
        pass


def apply_match_source(target: FBCharacter, enabled: bool) -> bool:
    """Toggle HumanIK ``Match Source`` if exposed on this MoBu version.

    Returns True if the property was found and set, False otherwise so the UI
    can surface a warning instead of silently doing nothing.
    """
    for name in _MATCH_SOURCE_PROPERTY_CANDIDATES:
        prop = target.PropertyList.Find(name)
        if prop is not None:
            try:
                prop.Data = bool(enabled)
                return True
            except Exception:
                continue
    return False


def plot_to_skeleton(target: FBCharacter, config: Optional[PlotConfig] = None) -> bool:
    """Bake the live input onto the target character's skeleton.

    Returns True on success. The caller is responsible for calling
    ``unbind_input`` afterwards.
    """
    if config is None:
        config = PlotConfig()
    if not _is_characterized(target):
        return False
    opts = config.to_fb_plot_options()
    return bool(
        target.PlotAnimation(FBCharacterPlotWhere.kFBCharacterPlotOnSkeleton, opts)
    )


def _is_characterized(character: FBCharacter) -> bool:
    try:
        flag = character.GetCharacterize
        if callable(flag):
            flag = flag()
        return bool(flag)
    except Exception:
        return False
