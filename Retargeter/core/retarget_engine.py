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

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

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


# Candidate names for the four HumanIK options the advisor speaks about.
# We try these in order: direct attribute lookup on the character, then each
# PropertyList alias, then a normalised whole-PropertyList scan (auto-finder)
# as a final fallback. Whatever resolves first is cached per character +
# logical key in _HIK_OPTION_RESOLVE_CACHE so we only pay the discovery cost
# on first access per scene session.
#
# If the operator confirms the exact spelling MoBu 2026 uses on this rig,
# put it as the first entry of the relevant tuple to make access immediate.
_HIK_OPTION_PROPERTY_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "HIKForceActorSpaceId": (
        "HIKForceActorSpaceId",
        "Force Actor Space",
        "ForceActorSpace",
        "HIKForceActorSpace",
    ),
    "HIKScaleCompensationId": (
        "HIKScaleCompensationId",
        "Scale Compensation",
        "ScaleCompensation",
        "HIKScaleCompensation",
    ),
    "HIKTopSpineCorrectionId": (
        "HIKTopSpineCorrectionId",
        "Top Spine Correction",
        "TopSpineCorrection",
        "HIKTopSpineCorrection",
    ),
    "HIKFingerPropagationId": (
        "HIKFingerPropagationId",
        "Finger Propagation",
        "FingerPropagation",
        "HIKFingerPropagation",
    ),
}

# Per-(character, key) cache for the resolved access path. Values are tagged
# tuples: ("attr", <name>) or ("prop", <FBProperty>) or ("missing",).
_HIK_OPTION_RESOLVE_CACHE: "dict[tuple[int, str], tuple]" = {}

_HIK_NORMALISE_RE = re.compile(r"[\s_]+|hik|id$", re.IGNORECASE)


def _normalise_hik_name(name: str) -> str:
    """Lower-case, strip whitespace/underscores, drop ``HIK`` prefix and
    ``Id`` suffix so ``"HIKForceActorSpaceId"`` and ``"Force Actor Space"``
    collapse to the same key."""
    return _HIK_NORMALISE_RE.sub("", name or "").lower()


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


def apply_hik_options(
    target: FBCharacter,
    options: Optional[Dict[str, Any]],
    logger: Optional[Any] = None,
) -> Dict[str, bool]:
    """Apply a dict of HIK option booleans to ``target``.

    Returns a dict ``{key: applied?}`` so the caller can detect options the
    rig did not expose. When ``logger`` is provided (anything with ``.warn``)
    misses are surfaced once per option per character via that channel rather
    than swallowed silently.

    Resolution order, mirroring :func:`apply_match_source` philosophy:

    1. ``getattr(target, "<key>", ...)`` direct attribute on the character
    2. each name in ``_HIK_OPTION_PROPERTY_CANDIDATES[key]`` via
       ``target.PropertyList.Find(...)`` (fast, exact)
    3. one whole-PropertyList scan using normalised names (auto-finder)
    4. give up and log a warning once
    """
    applied: Dict[str, bool] = {}
    if not options:
        return applied
    if target is None:
        return applied

    for key, value in options.items():
        if key not in _HIK_OPTION_PROPERTY_CANDIDATES:
            applied[key] = False
            continue
        ok = _apply_one_hik_option(target, key, bool(value), logger)
        applied[key] = ok
    return applied


def _apply_one_hik_option(
    target: FBCharacter,
    key: str,
    value: bool,
    logger: Optional[Any],
) -> bool:
    cache_id = (id(target), key)
    resolved = _HIK_OPTION_RESOLVE_CACHE.get(cache_id)
    if resolved is None:
        resolved = _resolve_hik_option(target, key)
        _HIK_OPTION_RESOLVE_CACHE[cache_id] = resolved

    kind = resolved[0]
    if kind == "attr":
        attr_name = resolved[1]
        try:
            setattr(target, attr_name, value)
            return True
        except Exception:
            # Attribute existed at probe time but reject this value type;
            # fall back to a fresh property search next call.
            _HIK_OPTION_RESOLVE_CACHE.pop(cache_id, None)
            if logger is not None:
                try:
                    logger.warn(
                        f"HIK option '{key}': setattr on '{attr_name}' failed; will re-probe next call."
                    )
                except Exception:
                    pass
            return False
    if kind == "prop":
        prop = resolved[1]
        try:
            prop.Data = value
            return True
        except Exception:
            _HIK_OPTION_RESOLVE_CACHE.pop(cache_id, None)
            return False
    if logger is not None:
        try:
            logger.warn(
                f"HIK option '{key}' not exposed on character '{getattr(target, 'LongName', '?')}'; skipping."
            )
        except Exception:
            pass
    return False


def _resolve_hik_option(target: FBCharacter, key: str) -> tuple:
    """Find an access path for one HIK option. Returns ``("attr"|"prop", x)``
    or ``("missing",)``. Tried only once per (character, key)."""
    # 1. Direct attribute (some MoBu builds expose HIK options as Id-typed
    #    enums on the character object itself).
    if hasattr(target, key):
        return ("attr", key)
    # Direct attribute under a few normalised aliases too.
    base = key[:-2] if key.endswith("Id") else key  # drop trailing "Id"
    for cand in (base, base + "ID", base.replace("HIK", "")):
        if cand and cand != key and hasattr(target, cand):
            return ("attr", cand)

    # 2. PropertyList exact-name candidates.
    for name in _HIK_OPTION_PROPERTY_CANDIDATES.get(key, ()):
        prop = target.PropertyList.Find(name)
        if prop is not None:
            return ("prop", prop)

    # 3. PropertyList scan: normalise every property's name and look for any
    #    that matches the normalised key.
    needle = _normalise_hik_name(key)
    try:
        for prop in target.PropertyList:
            try:
                pname = prop.GetName()
            except Exception:
                continue
            if _normalise_hik_name(pname) == needle:
                return ("prop", prop)
    except Exception:
        pass

    return ("missing",)


def diagnose_hik_options(target: FBCharacter) -> Dict[str, Dict[str, Any]]:
    """Human-readable report of how each HIK option resolves on ``target``.

    Returned shape::

        {
            "HIKForceActorSpaceId": {
                "exposed": True,
                "via": "attr",
                "name": "HIKForceActorSpaceId",
                "value": 1,
                "candidates": ["HIKForceActorSpaceId", "HIK_ForceActorSpace", ...],
            },
            ...
        }

    The dialog dumps this verbatim into the recommendation reasons panel
    so the operator can quickly see which knobs the current rig actually
    exposes and which were silently skipped at apply time."""
    out: Dict[str, Dict[str, Any]] = {}
    if target is None:
        for key in _HIK_OPTION_PROPERTY_CANDIDATES:
            out[key] = {
                "exposed": False,
                "via": "missing",
                "name": None,
                "value": None,
                "candidates": list(_HIK_OPTION_PROPERTY_CANDIDATES.get(key, ())),
                "note": "no target character",
            }
        return out

    for key in _HIK_OPTION_PROPERTY_CANDIDATES:
        info: Dict[str, Any] = {
            "candidates": list(_HIK_OPTION_PROPERTY_CANDIDATES.get(key, ())),
        }
        try:
            resolved = _resolve_hik_option(target, key)
        except Exception as exc:
            info.update(exposed=False, via="error", name=None, value=None,
                        note=f"resolve raised {exc!r}")
            out[key] = info
            continue

        kind = resolved[0]
        if kind == "attr":
            attr_name = resolved[1]
            try:
                value = getattr(target, attr_name)
            except Exception:
                value = None
            info.update(exposed=True, via="attr", name=attr_name, value=_coerce_diag_value(value))
        elif kind == "prop":
            prop = resolved[1]
            try:
                pname = prop.GetName()
            except Exception:
                pname = "<unnamed>"
            try:
                value = prop.Data
            except Exception:
                value = None
            info.update(exposed=True, via="prop", name=pname, value=_coerce_diag_value(value))
        else:
            info.update(exposed=False, via="missing", name=None, value=None)
        out[key] = info
    return out


def _coerce_diag_value(value: Any) -> Any:
    """Make a HIK property value JSON-safe for the diagnostic dump."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    try:
        return str(value)
    except Exception:
        return repr(value)


def hik_option_exposed(target: FBCharacter, key: str) -> bool:
    """Cheap query for the UI: is this HIK option available on this rig?

    Touches the same resolver cache, so calling this from the dialog (to
    grey out a checkbox the rig does not expose) is essentially free after
    the first lookup.
    """
    if target is None or key not in _HIK_OPTION_PROPERTY_CANDIDATES:
        return False
    cache_id = (id(target), key)
    resolved = _HIK_OPTION_RESOLVE_CACHE.get(cache_id)
    if resolved is None:
        resolved = _resolve_hik_option(target, key)
        _HIK_OPTION_RESOLVE_CACHE[cache_id] = resolved
    return resolved[0] in ("attr", "prop")


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
