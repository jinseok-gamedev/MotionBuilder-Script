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


# Candidate names for the HumanIK options the advisor speaks about.
# We try these in order: direct attribute lookup on the character, then each
# PropertyList alias, then a normalised whole-PropertyList scan (auto-finder)
# as a final fallback. Whatever resolves first is cached per character +
# logical key in _HIK_OPTION_RESOLVE_CACHE so we only pay the discovery cost
# on first access per scene session.
#
# Note: on 3ds Max Biped characters the spine/finger options live on the
# HIK Solver itself and surface in the character's PropertyList as
# "HIK 2016 Solver 1 Top Spine Correction" (the trailing " 1" is a scene
# dedup index when more than one solver exists). The candidates below are
# the short forms; the solver-prefixed long forms are caught by the
# normalised whole-PropertyList scan in step 3 (see _normalise_hik_name).
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
        "Action Space Compensation",
        "ActionSpaceCompensation",
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
    "HIKLowerSpineCorrectionId": (
        "HIKLowerSpineCorrectionId",
        "Lower Spine Correction",
        "LowerSpineCorrection",
        "HIKLowerSpineCorrection",
        "Bottom Spine Correction",
        "BottomSpineCorrection",
    ),
    "HIKFingerPropagationId": (
        "HIKFingerPropagationId",
        "Finger Propagation",
        "FingerPropagation",
        "HIKFingerPropagation",
    ),
}

# Logical keys whose underlying MoBu property is a 0-100 percentage rather
# than a true boolean. ``_apply_one_hik_option`` consults this set so a
# bool ``True`` from the advisor maps to 100.0 (fully on) instead of 1.0
# (essentially off) when the prop is float-typed. See the comment above
# _coerce_value_for_prop for the full rationale.
_HIK_OPTION_PERCENTAGE_KEYS: Tuple[str, ...] = (
    "HIKScaleCompensationId",
)

# Per-(character, key) cache for the resolved access path. Values are tagged
# tuples: ("attr", <name>) or ("prop", <FBProperty>) or ("missing",).
_HIK_OPTION_RESOLVE_CACHE: "dict[tuple[int, str], tuple]" = {}


# Two-pass normalisation: first collapse separators (whitespace, underscores,
# dots) so the second pass can match tokens that originally contained spaces
# *and* the dot-joined solver-namespace path some MoBu builds use, e.g.
#   "HIK 2016 Solver 1.Top Spine Correction"
# (solver-owned properties surface in the character's PropertyList with the
# owning solver's name as a dotted prefix; the trailing " 1" is the scene
# dedup index when more than one solver instance exists).
_HIK_NORMALISE_WS_RE = re.compile(r"[\s_.]+")
# Strips "hik" anywhere, trailing "id", and the
# "<4-digit-year>solver<optional-index>" noise some MoBu builds prepend to
# solver-owned properties so that
#   "HIK 2016 Solver 1.Top Spine Correction" -> "topspinecorrection"
# matches
#   "HIKTopSpineCorrectionId"                 -> "topspinecorrection".
_HIK_NORMALISE_TOKENS_RE = re.compile(r"hik|id$|\d{4}solver\d*", re.IGNORECASE)


def _normalise_hik_name(name: str) -> str:
    """Collapse a property/key name to a comparable key.

    Steps:
    1. Lower-case.
    2. Remove all separator chars: whitespace, underscores, dots. The dot
       is important because MoBu writes solver-owned properties as
       ``"<SolverName>.<Property>"`` in the character's PropertyList.
    3. Strip the ``hik`` prefix (anywhere), trailing ``id``, and any
       ``<year>solver<index>`` segment that some MoBu builds prepend to
       HIK Solver-owned properties (the index appears when more than one
       solver instance lives in the scene, e.g. ``"HIK 2016 Solver 1"``).

    Result: ``"HIKForceActorSpaceId"``, ``"Force Actor Space"`` and
    ``"HIK 2016 Solver 1.Force Actor Space"`` all collapse to
    ``"forceactorspace"``.
    """
    s = _HIK_NORMALISE_WS_RE.sub("", (name or "").lower())
    return _HIK_NORMALISE_TOKENS_RE.sub("", s)


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
    """Apply a dict of HIK option values to ``target``.

    Values are normally bool (the advisor's native vocabulary) but numeric
    values are also accepted: percentage-typed HIK properties (see
    ``_HIK_OPTION_PERCENTAGE_KEYS``) accept a 0-100 float directly.

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
        ok = _apply_one_hik_option(target, key, value, logger)
        applied[key] = ok
    return applied


def _apply_one_hik_option(
    target: FBCharacter,
    key: str,
    value: Any,
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
            prop.Data = _coerce_value_for_prop(prop, key, value)
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


def _coerce_value_for_prop(prop: Any, key: str, value: Any) -> Any:
    """Coerce ``value`` to a type the live MoBu property will honour.

    HIK options surface as one of three property types depending on the
    build and the option:

    * a true bool (most cases),
    * an enum-like int (a few "mode" flags),
    * a 0-100 float percentage (notably ``HIKScaleCompensationId``,
      which appears as the "Action Space Compensation" slider on
      3ds Max Biped characters).

    Passing ``True`` directly to a float property usually collapses to
    ``1.0`` (a 1 % blend = essentially off), which silently neuters the
    advisor's intent. Passing ``False`` to a percentage prop is
    unambiguous (= 0 %). We detect "this is really a percentage" by:

    1. consulting :data:`_HIK_OPTION_PERCENTAGE_KEYS` (declared knowledge), or
    2. peeking at the property's current ``Data`` type (float / int).

    Numeric values pass through untouched (clamped to [0, 100] for
    percentage props). Bools are routed through the percentage / int /
    bool mapping as appropriate.
    """
    try:
        current = prop.Data
    except Exception:
        current = None

    is_percentage = key in _HIK_OPTION_PERCENTAGE_KEYS or isinstance(current, float)

    if is_percentage:
        if isinstance(value, bool):
            return 100.0 if value else 0.0
        try:
            num = float(value)
        except (TypeError, ValueError):
            return current if current is not None else 0.0
        return max(0.0, min(100.0, num))

    if isinstance(current, int) and not isinstance(current, bool):
        if isinstance(value, bool):
            return 1 if value else 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return current

    return bool(value)


def _resolve_hik_option(target: FBCharacter, key: str) -> tuple:
    """Find an access path for one HIK option.

    Returns one of::

        ("attr", attr_name)
        ("prop", prop, redirected_from)   # redirected_from: str | None
        ("missing",)

    The ``redirected_from`` slot is non-None when the character's
    PropertyList exposed only a dotted proxy name (e.g.
    ``"HIK 2016 Solver 1.Top Spine Correction"``) and we hopped to the
    actual writable property on the underlying solver object - the
    proxy on the character side is read-only / no-op for writes on
    some MoBu builds, so we always prefer the solver-side leaf. The
    original dotted name is preserved purely for diagnostic display.

    The cache (:data:`_HIK_OPTION_RESOLVE_CACHE`) memoises the result
    per (character_id, key) so the discovery cost is paid once per
    scene session.
    """
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
            return _wrap_prop_result(prop)

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
                return _wrap_prop_result(prop)
    except Exception:
        pass

    return ("missing",)


def _wrap_prop_result(char_prop: Any) -> tuple:
    """Wrap a matched char-side property into the resolve-cache tuple.

    If the prop's name is dotted (``"SolverName.Leaf"``) we try to hop to
    the solver's actual leaf property, which is the one writes actually
    land on. Falls back to the original prop when no redirect target is
    found (so behaviour stays correct for plain bool/float props that
    happen to live directly on the character).
    """
    try:
        orig_name = char_prop.GetName()
    except Exception:
        orig_name = ""
    redirected = _maybe_redirect_to_solver(orig_name)
    if redirected is not None:
        return ("prop", redirected, orig_name)
    return ("prop", char_prop, None)


def _maybe_redirect_to_solver(prop_name: str) -> Optional[Any]:
    """Resolve ``"<SolverName>.<Leaf>"`` to the solver's actual leaf prop.

    Returns the writable solver-side property when:

    * ``prop_name`` contains a dot (the MoBu naming convention for a
      solver-owned proxy that surfaces under the character),
    * a scene object whose name matches ``<SolverName>`` is found in
      ``FBSystem().Scene.Solvers`` (or, as a fallback for builds that
      don't list HIK solvers there, ``FBSystem().Scene.Components``),
    * that object exposes ``<Leaf>`` via ``PropertyList.Find``.

    Returns ``None`` (= no redirect) for any other case, including a
    name without a dot, missing solver, or a leaf that isn't there.

    Cached implicitly: this only runs once per (character, key) via
    :data:`_HIK_OPTION_RESOLVE_CACHE`.
    """
    if not prop_name or "." not in prop_name:
        return None
    solver_name, _, leaf_name = prop_name.partition(".")
    if not solver_name or not leaf_name:
        return None
    try:
        from pyfbsdk import FBSystem  # type: ignore
    except Exception:
        return None
    try:
        scene = FBSystem().Scene
    except Exception:
        return None

    # Scene.Solvers is the precise container; Scene.Components is the
    # exhaustive fallback for MoBu builds that don't list HIK solvers
    # under Solvers. Either iteration may raise on edge-case scenes, so
    # both are wrapped defensively.
    containers = []
    try:
        containers.append(scene.Solvers)
    except Exception:
        pass
    try:
        containers.append(scene.Components)
    except Exception:
        pass

    for container in containers:
        try:
            for obj in container:
                try:
                    name = obj.LongName
                except Exception:
                    try:
                        name = obj.Name
                    except Exception:
                        continue
                if name != solver_name:
                    continue
                try:
                    leaf = obj.PropertyList.Find(leaf_name)
                except Exception:
                    continue
                if leaf is not None:
                    return leaf
        except Exception:
            continue
    return None


def diagnose_hik_options(target: FBCharacter) -> Dict[str, Dict[str, Any]]:
    """Human-readable report of how each HIK option resolves on ``target``.

    Returned shape (per key)::

        {
            "exposed": True,
            "via": "attr" | "prop" | "missing" | "error",
            "name": "<actual property name>" | None,
            "value": <current value>,
            "candidates": ["HIKForceActorSpaceId", ...],
            "normalised_key": "forceactorspace",
            # if matched via attr/prop:
            "normalised_name": "forceactorspace",
            # if matched via prop, the typed-read method that produced
            # the reported value ("Data" | "AsDouble" | "AsInt" |
            # "AsString" | "GetData" | "all_failed"):
            "value_via": "Data",
            # if matched via prop AND the resolver hopped from a
            # solver-owned proxy to the solver's actual leaf property:
            "redirected_from": "HIK 2016 Solver 1.Top Spine Correction",
            # if missing:
            "near_misses": [("HIK 2016 Solver 1.Foo", "foo"), ...],
            "note": "<optional explanation>",
        }

    ``normalised_key`` / ``normalised_name`` make it trivial to debug
    "why didn't this match?" - if the normalised values differ, the
    normaliser regex needs to learn one more token. ``value_via`` flags
    when the plain ``prop.Data`` returned None and a typed-accessor
    fallback (``AsDouble`` / ``AsInt`` / ``AsString``) was needed - this
    is the signature of solver-owned animatable properties on some MoBu
    builds. ``redirected_from`` flags when the resolver hopped from a
    char-side proxy (which is read-only / no-op for writes) to the
    solver-side actual writable - this is necessary on 3ds Max Biped
    rigs where HIK options surface under the character as dotted
    proxies. ``near_misses`` is only filled when ``via=='missing'`` and
    lists property names whose normalised form contains (or is contained
    by) the needle, so the next "Diagnose HIK" press already surfaces
    the likely-correct name.

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
                "normalised_key": _normalise_hik_name(key),
                "near_misses": [],
                "note": "no target character",
            }
        return out

    for key in _HIK_OPTION_PROPERTY_CANDIDATES:
        needle = _normalise_hik_name(key)
        info: Dict[str, Any] = {
            "candidates": list(_HIK_OPTION_PROPERTY_CANDIDATES.get(key, ())),
            "normalised_key": needle,
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
            info.update(
                exposed=True,
                via="attr",
                name=attr_name,
                value=_coerce_diag_value(value),
                normalised_name=_normalise_hik_name(attr_name),
            )
        elif kind == "prop":
            prop = resolved[1]
            redirected_from = resolved[2] if len(resolved) > 2 else None
            try:
                pname = prop.GetName()
            except Exception:
                pname = "<unnamed>"
            value, via_method = _read_hik_prop_value(prop)
            info.update(
                exposed=True,
                via="prop",
                name=pname,
                value=_coerce_diag_value(value),
                value_via=via_method,
                normalised_name=_normalise_hik_name(pname),
            )
            if redirected_from:
                info["redirected_from"] = redirected_from
        else:
            info.update(
                exposed=False,
                via="missing",
                name=None,
                value=None,
                near_misses=_find_hik_near_misses(target, needle),
            )
        out[key] = info
    return out


def _read_hik_prop_value(prop: Any) -> "tuple[Any, str]":
    """Try multiple MoBu property read paths and return ``(value, via)``.

    The plain ``prop.Data`` accessor returns ``None`` for some solver-owned
    animatable properties (e.g. the dot-prefixed
    ``"HIK 2016 Solver 1.Top Spine Correction"`` on a 3ds Max Biped) even
    when the slider clearly holds a real value in the Character Settings
    panel. For diagnostic purposes we try each known typed-read fallback
    in order and return the first non-None hit, together with the method
    name that produced it so the operator can see which path actually
    worked.

    Returns ``(None, "all_failed")`` if every attempt raises or returns
    None.
    """
    # Ordered most-likely-to-work first. ``Data`` stays first so the
    # default report unchanged for the regular bool / float props.
    attempts = (
        ("Data", lambda p: p.Data),
        ("AsDouble", lambda p: p.AsDouble()),
        ("AsInt", lambda p: p.AsInt()),
        ("AsString", lambda p: p.AsString()),
        ("GetData", lambda p: p.GetData()),
    )
    for method_name, getter in attempts:
        try:
            val = getter(prop)
        except Exception:
            continue
        if val is not None:
            return val, method_name
    return None, "all_failed"


def _find_hik_near_misses(target: FBCharacter, needle: str) -> "list[tuple[str, str]]":
    """Return PropertyList entries whose normalised name overlaps ``needle``.

    "Overlap" = needle is a substring of the prop's normalised name, or
    vice-versa. This catches the dedup-indexed solver names like
    ``"HIK 2016 Solver 1 Top Spine Correction"`` even when the live
    normaliser has not yet learned to strip the solver prefix.

    Capped at 5 results to keep the diagnostic panel readable.
    """
    if not needle:
        return []
    results: list[tuple[str, str]] = []
    try:
        for prop in target.PropertyList:
            try:
                pname = prop.GetName()
            except Exception:
                continue
            n = _normalise_hik_name(pname)
            if not n:
                continue
            if needle in n or n in needle:
                results.append((pname, n))
                if len(results) >= 5:
                    break
    except Exception:
        return results
    return results


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
