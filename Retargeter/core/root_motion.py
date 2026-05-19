"""Per-take root motion transformations.

Three modes are exposed to the operator:

* ``keep``
    No change. Hips translation FCurves are exported as-is. Suitable for game
    engines that drive movement from root motion or when the source data is
    already authored in-place.

* ``strip``
    Horizontal motion (X / Z) on the Hips bone is collapsed to the first-frame
    value, producing an in-place animation. Vertical motion (Y) is preserved
    so jumps and crouches still read. Used when the engine's animation
    blueprint moves the capsule and the animation must NOT drive movement.

* ``extract``
    Hips horizontal motion is transferred onto a dedicated root carrier (the
    HIK ``Reference`` bone if present, otherwise the parent of Hips). The
    Hips bone is then zeroed in XZ. This is the UE5 root-motion convention:
    a ``root`` bone moves through the world while the pelvis stays centred
    above it. If no carrier bone exists the function gracefully degrades to
    ``strip`` and reports it in the log.

All operations are scoped to a specific ``FBTake``. The caller sets and
restores the active take.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from pyfbsdk import (  # type: ignore
    FBSystem,
    FBTime,
)

from .scene_utils import HIPS_SLOT, get_slot_model


REFERENCE_SLOT = "ReferenceLink"

MODE_KEEP = "keep"
MODE_STRIP = "strip"
MODE_EXTRACT = "extract"
VALID_MODES = (MODE_KEEP, MODE_STRIP, MODE_EXTRACT)


@dataclass
class RootMotionResult:
    mode_applied: str
    notes: List[str]

    def add_note(self, msg: str) -> None:
        self.notes.append(msg)


def apply(target_character, mode: str, take) -> RootMotionResult:
    """Apply the given root motion ``mode`` to ``take`` on ``target_character``.

    The current take is temporarily switched to ``take`` and restored on exit.
    """
    mode = (mode or MODE_KEEP).lower()
    if mode not in VALID_MODES:
        return RootMotionResult(MODE_KEEP, [f"Unknown mode '{mode}', no-op."])
    result = RootMotionResult(mode, [])

    if mode == MODE_KEEP:
        return result

    hips = get_slot_model(target_character, HIPS_SLOT)
    if hips is None:
        result.add_note("Hips slot is empty; cannot apply root motion mode.")
        result.mode_applied = MODE_KEEP
        return result

    system = FBSystem()
    prev_take = system.CurrentTake
    system.CurrentTake = take
    try:
        if mode == MODE_STRIP:
            _strip_horizontal(hips, result)
        elif mode == MODE_EXTRACT:
            carrier = _resolve_carrier(target_character, hips)
            if carrier is None:
                result.add_note(
                    "No Reference / parent bone found for extract; falling back to strip."
                )
                _strip_horizontal(hips, result)
                result.mode_applied = MODE_STRIP
            else:
                _extract_to_carrier(hips, carrier, result)
    finally:
        system.CurrentTake = prev_take

    return result


def _resolve_carrier(target_character, hips):
    """Find a bone to receive extracted root motion."""
    ref = get_slot_model(target_character, REFERENCE_SLOT)
    if ref is not None and ref is not hips:
        return ref
    parent = hips.Parent
    if parent is not None:
        return parent
    return None


def _get_xyz_fcurves(model) -> Optional[Tuple[object, object, object]]:
    """Return (X, Y, Z) FCurves for a model's translation on the current take.

    Returns None if the model is not animated at all (no animation node).
    """
    model.Translation.SetAnimated(True)
    anim_node = model.Translation.GetAnimationNode()
    if anim_node is None or len(anim_node.Nodes) < 3:
        return None
    return (
        anim_node.Nodes[0].FCurve,
        anim_node.Nodes[1].FCurve,
        anim_node.Nodes[2].FCurve,
    )


def _first_key_value(fcurve, fallback: float) -> float:
    keys = list(fcurve.Keys)
    if not keys:
        return fallback
    return float(keys[0].Value)


def _flatten_to_first(fcurve) -> None:
    """Collapse an FCurve to a single key holding its first-frame value."""
    keys = list(fcurve.Keys)
    if not keys:
        return
    first_value = float(keys[0].Value)
    first_time = FBTime(keys[0].Time)
    fcurve.EditClear()
    fcurve.KeyAdd(first_time, first_value)


def _strip_horizontal(hips, result: RootMotionResult) -> None:
    curves = _get_xyz_fcurves(hips)
    if curves is None:
        result.add_note("Hips has no translation animation node; nothing to strip.")
        return
    x_curve, _y_curve, z_curve = curves
    _flatten_to_first(x_curve)
    _flatten_to_first(z_curve)
    result.add_note("Hips X/Z translation flattened (in-place).")


def _copy_curve(src_curve, dst_curve) -> None:
    """Replace ``dst_curve`` with a copy of ``src_curve``'s keys."""
    dst_curve.EditClear()
    for k in src_curve.Keys:
        idx = dst_curve.KeyAdd(FBTime(k.Time), float(k.Value))
        if idx >= 0:
            try:
                dst_curve.Keys[idx].Interpolation = k.Interpolation
            except Exception:
                pass


def _additive_copy(src_curve, dst_curve) -> None:
    """``dst += src`` over the union of their keyframes.

    Used when the carrier bone already has its own translation animation we
    must not destroy.
    """
    if not list(src_curve.Keys):
        return
    if not list(dst_curve.Keys):
        _copy_curve(src_curve, dst_curve)
        return
    for k in src_curve.Keys:
        t = FBTime(k.Time)
        existing = dst_curve.Evaluate(t)
        idx = dst_curve.KeyAdd(t, float(existing) + float(k.Value))
        if idx >= 0:
            try:
                dst_curve.Keys[idx].Interpolation = k.Interpolation
            except Exception:
                pass


def _extract_to_carrier(hips, carrier, result: RootMotionResult) -> None:
    hips_curves = _get_xyz_fcurves(hips)
    if hips_curves is None:
        result.add_note("Hips has no translation animation node; nothing to extract.")
        return
    carrier_curves = _get_xyz_fcurves(carrier)
    if carrier_curves is None:
        result.add_note(
            f"Carrier '{carrier.LongName}' has no translation node; cannot extract."
        )
        return

    hx, _hy, hz = hips_curves
    cx, _cy, cz = carrier_curves

    _additive_copy(hx, cx)
    _additive_copy(hz, cz)
    _flatten_to_first(hx)
    _flatten_to_first(hz)
    result.add_note(
        f"Hips XZ extracted to '{carrier.LongName}', Hips flattened in place."
    )
