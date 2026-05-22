"""Quantitative quality metrics for a single retargeted take.

These metrics are computed *after* :func:`Retargeter.core.retarget_engine.plot_to_skeleton`
succeeds, comparing the source character and the target character on the
same take. They serve two purposes:

1. Surface objective regressions to the operator (foot sliding, wrist flips,
   etc.) without needing to scrub the timeline.
2. Provide a numeric label / weak supervision signal for the stage-2
   regression model that will eventually replace
   :class:`Retargeter.core.option_advisor.RuleBasedRecommender`.

The pipeline calls this module only when ``RunConfig.compute_metrics`` is
True - the per-frame evaluation is several times more expensive than the
plot itself on a long take, so it stays opt-in.

All thresholds (foot-contact height, wrist-flip jump radians, max frames
sampled) are constants at the top of the module; tune them in one place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from pyfbsdk import (  # type: ignore
    FBCharacter,
    FBMatrix,
    FBPlayerControl,
    FBSystem,
    FBTime,
    FBVector3d,
)

from .scene_utils import (
    HIPS_SLOT,
    LEFT_FOOT_SLOT,
    LEFT_HAND_SLOT,
    LEFT_LEG_SLOT,
    LEFT_SHOULDER_SLOT,
    LEFT_UP_LEG_SLOT,
    RIGHT_FOOT_SLOT,
    RIGHT_HAND_SLOT,
    RIGHT_LEG_SLOT,
    RIGHT_SHOULDER_SLOT,
    RIGHT_UP_LEG_SLOT,
    get_slot_model,
)


# Cap on the number of frames we sample for metrics. A 60 fps 30 s take is
# 1800 frames; that's manageable. A 4 minute take at 120 fps is 28800 frames
# and we down-sample to MAX_SAMPLES below by striding.
MAX_SAMPLES = 1200
FOOT_CONTACT_HEIGHT_M = 0.05          # 5 cm above lowest foot Y over the take
WRIST_FLIP_JUMP_RAD = 2.6             # ~150 degrees frame-to-frame
KNEE_POP_OUTLIER_SIGMA = 4.0          # |2nd derivative| > N * std

# Thresholds used by ``suggest_label`` to convert a MetricResult into a
# weak good/bad hint. Tuned conservatively: only label a take "good" when
# every signal is clean; only label "bad" when at least one signal is
# clearly wrong. Tweak in one place if your studio has different tolerances.
LABEL_GOOD_FOOT_IOU_MIN = 0.85
LABEL_GOOD_WRIST_FLIPS_MAX = 0          # zero flips combined
LABEL_GOOD_KNEE_POPS_MAX = 1            # at most 1 combined pop
LABEL_GOOD_HIPS_DELTA_P95_MAX_M = 0.20  # 20 cm hips slide vs source
LABEL_GOOD_SHOULDER_SHRUG_MAX_M = 0.08  # 8 cm shoulder-to-source delta

LABEL_BAD_FOOT_IOU_MAX = 0.55           # less than 55% overlap -> bad
LABEL_BAD_WRIST_FLIPS_MIN = 3           # 3+ combined flips -> bad
LABEL_BAD_KNEE_POPS_MIN = 6             # 6+ combined pops -> bad
LABEL_BAD_HIPS_DELTA_P95_MIN_M = 0.40   # 40 cm hips drift -> bad


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


@dataclass
class MetricResult:
    foot_contact_iou: Optional[float] = None
    wrist_flip_count_l: int = 0
    wrist_flip_count_r: int = 0
    hips_translation_delta_p95: Optional[float] = None
    knee_pop_count_l: int = 0
    knee_pop_count_r: int = 0
    shoulder_shrug_max_m: Optional[float] = None
    frames_sampled: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "foot_contact_iou": self.foot_contact_iou,
            "wrist_flip_count": {
                "L": self.wrist_flip_count_l,
                "R": self.wrist_flip_count_r,
            },
            "hips_translation_delta_p95": self.hips_translation_delta_p95,
            "knee_pop_count": {
                "L": self.knee_pop_count_l,
                "R": self.knee_pop_count_r,
            },
            "shoulder_shrug_max_m": self.shoulder_shrug_max_m,
            "frames_sampled": self.frames_sampled,
            "notes": list(self.notes),
        }


def suggest_label(metrics: "MetricResult") -> Optional[str]:
    """Map a MetricResult to a weak good/bad label hint, or ``None`` if the
    metrics are too sparse / ambiguous to commit to either side.

    The thresholds at module top define the decision; logic intentionally
    stays trivial so it can be re-used as a weak-supervision feature in the
    stage-2 ML pipeline (where the real label is a human-confirmed
    good/bad). Treat the output strictly as a *hint* shown next to the
    Quality column - users must still confirm before it lands in the
    feedback log."""
    if metrics is None or metrics.frames_sampled <= 1:
        return None

    foot = metrics.foot_contact_iou
    hips = metrics.hips_translation_delta_p95
    shrug = metrics.shoulder_shrug_max_m
    wrist_total = int(metrics.wrist_flip_count_l) + int(metrics.wrist_flip_count_r)
    knee_total = int(metrics.knee_pop_count_l) + int(metrics.knee_pop_count_r)

    bad_signals: List[str] = []
    if foot is not None and foot <= LABEL_BAD_FOOT_IOU_MAX:
        bad_signals.append(f"foot_iou={foot:.2f}")
    if wrist_total >= LABEL_BAD_WRIST_FLIPS_MIN:
        bad_signals.append(f"wrist_flips={wrist_total}")
    if knee_total >= LABEL_BAD_KNEE_POPS_MIN:
        bad_signals.append(f"knee_pops={knee_total}")
    if hips is not None and hips >= LABEL_BAD_HIPS_DELTA_P95_MIN_M:
        bad_signals.append(f"hips_p95={hips:.2f}m")
    if bad_signals:
        return "bad"

    good_ok = True
    if foot is None or foot < LABEL_GOOD_FOOT_IOU_MIN:
        good_ok = False
    if wrist_total > LABEL_GOOD_WRIST_FLIPS_MAX:
        good_ok = False
    if knee_total > LABEL_GOOD_KNEE_POPS_MAX:
        good_ok = False
    if hips is None or hips > LABEL_GOOD_HIPS_DELTA_P95_MAX_M:
        good_ok = False
    if shrug is not None and shrug > LABEL_GOOD_SHOULDER_SHRUG_MAX_M:
        good_ok = False
    if good_ok:
        return "good"
    return None


def compute_metrics(
    source: FBCharacter,
    target: FBCharacter,
    *,
    source_height_m: Optional[float] = None,
) -> MetricResult:
    """Walk the current take's frames once and return all metrics.

    The current scene time is mutated during sampling and restored on exit.
    """
    result = MetricResult()
    take = FBSystem().CurrentTake
    if take is None or source is None or target is None:
        result.notes.append("compute_metrics: no current take or character missing")
        return result

    try:
        start = take.LocalTimeSpan.GetStart()
        stop = take.LocalTimeSpan.GetStop()
    except Exception as exc:
        result.notes.append(f"compute_metrics: cannot read take time span: {exc!r}")
        return result

    saved_time = FBSystem().LocalTime
    try:
        fps = _take_fps(take)
        if fps <= 0:
            fps = 30.0
        total_frames = max(1, int(round((stop.GetSecondDouble() - start.GetSecondDouble()) * fps)))
        stride = max(1, total_frames // MAX_SAMPLES)
        n_samples = total_frames // stride
        result.frames_sampled = n_samples

        slots = _collect_slot_models(source, target)
        samples = _sample_take(start, fps, stride, n_samples, slots)

        # Use source's height as the normaliser for translation diffs. Fall
        # back to a hand-tuned 1.7 m if missing.
        h = source_height_m if (source_height_m and source_height_m > 0.1) else 1.7

        result.foot_contact_iou = _foot_contact_iou(samples)
        result.wrist_flip_count_l, result.wrist_flip_count_r = _wrist_flip_counts(samples)
        result.hips_translation_delta_p95 = _hips_translation_delta(samples, height_m=h)
        result.knee_pop_count_l, result.knee_pop_count_r = _knee_pop_counts(samples, fps)
        result.shoulder_shrug_max_m = _shoulder_shrug_max(samples)
    except Exception as exc:
        result.notes.append(f"compute_metrics crashed: {exc!r}")
    finally:
        try:
            FBPlayerControl().Goto(saved_time)
            FBSystem().Scene.Evaluate()
        except Exception:
            pass
    return result


# ----------------------------------------------------------------------------
# Sampling
# ----------------------------------------------------------------------------


def _take_fps(take) -> float:
    try:
        return float(take.FrameRate)  # type: ignore[attr-defined]
    except Exception:
        try:
            return float(FBPlayerControl().GetTransportFps())
        except Exception:
            return 30.0


@dataclass
class _SlotPair:
    src_hips: Any = None
    tgt_hips: Any = None
    src_l_foot: Any = None
    tgt_l_foot: Any = None
    src_r_foot: Any = None
    tgt_r_foot: Any = None
    src_l_hand: Any = None
    tgt_l_hand: Any = None
    src_r_hand: Any = None
    tgt_r_hand: Any = None
    src_l_knee: Any = None
    tgt_l_knee: Any = None
    src_r_knee: Any = None
    tgt_r_knee: Any = None
    src_l_upleg: Any = None
    tgt_l_upleg: Any = None
    src_r_upleg: Any = None
    tgt_r_upleg: Any = None
    src_l_shoulder: Any = None
    tgt_l_shoulder: Any = None
    src_r_shoulder: Any = None
    tgt_r_shoulder: Any = None


def _collect_slot_models(source: FBCharacter, target: FBCharacter) -> _SlotPair:
    pair = _SlotPair()
    pair.src_hips = get_slot_model(source, HIPS_SLOT)
    pair.tgt_hips = get_slot_model(target, HIPS_SLOT)
    pair.src_l_foot = get_slot_model(source, LEFT_FOOT_SLOT)
    pair.tgt_l_foot = get_slot_model(target, LEFT_FOOT_SLOT)
    pair.src_r_foot = get_slot_model(source, RIGHT_FOOT_SLOT)
    pair.tgt_r_foot = get_slot_model(target, RIGHT_FOOT_SLOT)
    pair.src_l_hand = get_slot_model(source, LEFT_HAND_SLOT)
    pair.tgt_l_hand = get_slot_model(target, LEFT_HAND_SLOT)
    pair.src_r_hand = get_slot_model(source, RIGHT_HAND_SLOT)
    pair.tgt_r_hand = get_slot_model(target, RIGHT_HAND_SLOT)
    pair.src_l_knee = get_slot_model(source, LEFT_LEG_SLOT)
    pair.tgt_l_knee = get_slot_model(target, LEFT_LEG_SLOT)
    pair.src_r_knee = get_slot_model(source, RIGHT_LEG_SLOT)
    pair.tgt_r_knee = get_slot_model(target, RIGHT_LEG_SLOT)
    pair.src_l_upleg = get_slot_model(source, LEFT_UP_LEG_SLOT)
    pair.tgt_l_upleg = get_slot_model(target, LEFT_UP_LEG_SLOT)
    pair.src_r_upleg = get_slot_model(source, RIGHT_UP_LEG_SLOT)
    pair.tgt_r_upleg = get_slot_model(target, RIGHT_UP_LEG_SLOT)
    pair.src_l_shoulder = get_slot_model(source, LEFT_SHOULDER_SLOT)
    pair.tgt_l_shoulder = get_slot_model(target, LEFT_SHOULDER_SLOT)
    pair.src_r_shoulder = get_slot_model(source, RIGHT_SHOULDER_SLOT)
    pair.tgt_r_shoulder = get_slot_model(target, RIGHT_SHOULDER_SLOT)
    return pair


@dataclass
class _Sample:
    src_hips: Optional[List[float]] = None
    tgt_hips: Optional[List[float]] = None
    src_l_foot_y: Optional[float] = None
    src_r_foot_y: Optional[float] = None
    tgt_l_foot_y: Optional[float] = None
    tgt_r_foot_y: Optional[float] = None
    src_l_hand_quat: Optional[List[float]] = None
    src_r_hand_quat: Optional[List[float]] = None
    tgt_l_hand_quat: Optional[List[float]] = None
    tgt_r_hand_quat: Optional[List[float]] = None
    src_l_knee_angle: Optional[float] = None
    src_r_knee_angle: Optional[float] = None
    tgt_l_knee_angle: Optional[float] = None
    tgt_r_knee_angle: Optional[float] = None
    tgt_l_shoulder_y: Optional[float] = None
    tgt_r_shoulder_y: Optional[float] = None


def _sample_take(
    start: FBTime,
    fps: float,
    stride: int,
    n_samples: int,
    slots: _SlotPair,
) -> List[_Sample]:
    out: List[_Sample] = []
    player = FBPlayerControl()
    system = FBSystem()
    period_s = stride / float(fps)
    for i in range(n_samples):
        t = FBTime()
        t.SetSecondDouble(start.GetSecondDouble() + i * period_s)
        try:
            player.Goto(t)
            system.Scene.Evaluate()
        except Exception:
            continue
        s = _Sample()
        s.src_hips = _world_translation(slots.src_hips)
        s.tgt_hips = _world_translation(slots.tgt_hips)
        s.src_l_foot_y = _component(_world_translation(slots.src_l_foot), 1)
        s.src_r_foot_y = _component(_world_translation(slots.src_r_foot), 1)
        s.tgt_l_foot_y = _component(_world_translation(slots.tgt_l_foot), 1)
        s.tgt_r_foot_y = _component(_world_translation(slots.tgt_r_foot), 1)
        s.src_l_hand_quat = _world_quat(slots.src_l_hand)
        s.src_r_hand_quat = _world_quat(slots.src_r_hand)
        s.tgt_l_hand_quat = _world_quat(slots.tgt_l_hand)
        s.tgt_r_hand_quat = _world_quat(slots.tgt_r_hand)
        s.src_l_knee_angle = _three_point_angle(
            _world_translation(slots.src_l_upleg),
            _world_translation(slots.src_l_knee),
            _world_translation(slots.src_l_foot),
        )
        s.src_r_knee_angle = _three_point_angle(
            _world_translation(slots.src_r_upleg),
            _world_translation(slots.src_r_knee),
            _world_translation(slots.src_r_foot),
        )
        s.tgt_l_knee_angle = _three_point_angle(
            _world_translation(slots.tgt_l_upleg),
            _world_translation(slots.tgt_l_knee),
            _world_translation(slots.tgt_l_foot),
        )
        s.tgt_r_knee_angle = _three_point_angle(
            _world_translation(slots.tgt_r_upleg),
            _world_translation(slots.tgt_r_knee),
            _world_translation(slots.tgt_r_foot),
        )
        s.tgt_l_shoulder_y = _component(_world_translation(slots.tgt_l_shoulder), 1)
        s.tgt_r_shoulder_y = _component(_world_translation(slots.tgt_r_shoulder), 1)
        out.append(s)
    return out


def _world_translation(model) -> Optional[List[float]]:
    if model is None:
        return None
    try:
        v = FBVector3d()
        model.GetVector(v)
        return [float(v[0]), float(v[1]), float(v[2])]
    except Exception:
        return None


def _world_quat(model) -> Optional[List[float]]:
    """Return a quaternion [x, y, z, w] for the model's global rotation."""
    if model is None:
        return None
    try:
        m = FBMatrix()
        model.GetMatrix(m)
        # MoBu has FBMatrixToQuaternion but invoking it cross-version is
        # fragile; we extract from the 3x3 rotation block directly.
        return _matrix_to_quat([
            [m[0], m[1], m[2]],
            [m[4], m[5], m[6]],
            [m[8], m[9], m[10]],
        ])
    except Exception:
        return None


def _matrix_to_quat(r: Sequence[Sequence[float]]) -> List[float]:
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (r[2][1] - r[1][2]) / s
        y = (r[0][2] - r[2][0]) / s
        z = (r[1][0] - r[0][1]) / s
    elif (r[0][0] > r[1][1]) and (r[0][0] > r[2][2]):
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        w = (r[2][1] - r[1][2]) / s
        x = 0.25 * s
        y = (r[0][1] + r[1][0]) / s
        z = (r[0][2] + r[2][0]) / s
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        w = (r[0][2] - r[2][0]) / s
        x = (r[0][1] + r[1][0]) / s
        y = 0.25 * s
        z = (r[1][2] + r[2][1]) / s
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
        w = (r[1][0] - r[0][1]) / s
        x = (r[0][2] + r[2][0]) / s
        y = (r[1][2] + r[2][1]) / s
        z = 0.25 * s
    return [x, y, z, w]


def _component(vec: Optional[Sequence[float]], i: int) -> Optional[float]:
    if vec is None:
        return None
    try:
        return float(vec[i])
    except Exception:
        return None


def _three_point_angle(
    a: Optional[Sequence[float]],
    b: Optional[Sequence[float]],
    c: Optional[Sequence[float]],
) -> Optional[float]:
    """Angle (radians) at vertex ``b`` in triangle ``a-b-c``."""
    if a is None or b is None or c is None:
        return None
    ba = [a[i] - b[i] for i in range(3)]
    bc = [c[i] - b[i] for i in range(3)]
    nba = math.sqrt(sum(x * x for x in ba))
    nbc = math.sqrt(sum(x * x for x in bc))
    if nba < 1e-9 or nbc < 1e-9:
        return None
    cos_t = max(-1.0, min(1.0, sum(ba[i] * bc[i] for i in range(3)) / (nba * nbc)))
    return math.acos(cos_t)


# ----------------------------------------------------------------------------
# Metric computations on the sample stream
# ----------------------------------------------------------------------------


def _foot_contact_iou(samples: Sequence[_Sample]) -> Optional[float]:
    """IoU of (source feet on ground) and (target feet on ground) booleans.

    Threshold: foot Y within FOOT_CONTACT_HEIGHT_M * 100 cm of the per-side
    per-take floor (= min Y across all samples for that side).
    """
    if not samples:
        return None
    thresh_cm = FOOT_CONTACT_HEIGHT_M * 100.0

    def _binarize(per_side: Sequence[Optional[float]]) -> Optional[List[bool]]:
        present = [v for v in per_side if v is not None]
        if not present:
            return None
        floor = min(present)
        return [(v is not None) and (v - floor <= thresh_cm) for v in per_side]

    src_l = _binarize([s.src_l_foot_y for s in samples])
    src_r = _binarize([s.src_r_foot_y for s in samples])
    tgt_l = _binarize([s.tgt_l_foot_y for s in samples])
    tgt_r = _binarize([s.tgt_r_foot_y for s in samples])

    pairs = []
    if src_l is not None and tgt_l is not None:
        pairs.append((src_l, tgt_l))
    if src_r is not None and tgt_r is not None:
        pairs.append((src_r, tgt_r))
    if not pairs:
        return None
    inter = 0
    union = 0
    for s_bool, t_bool in pairs:
        for s, t in zip(s_bool, t_bool):
            if s or t:
                union += 1
            if s and t:
                inter += 1
    if union == 0:
        return 1.0
    return inter / union


def _wrist_flip_counts(samples: Sequence[_Sample]) -> tuple:
    def _flip_count(quats: Sequence[Optional[Sequence[float]]]) -> int:
        count = 0
        prev = None
        for q in quats:
            if q is None:
                prev = None
                continue
            if prev is not None:
                dot = abs(sum(prev[i] * q[i] for i in range(4)))
                dot = max(-1.0, min(1.0, dot))
                angle = 2.0 * math.acos(dot)
                if angle > WRIST_FLIP_JUMP_RAD:
                    count += 1
            prev = q
        return count

    l = _flip_count([s.tgt_l_hand_quat for s in samples])
    r = _flip_count([s.tgt_r_hand_quat for s in samples])
    return l, r


def _hips_translation_delta(
    samples: Sequence[_Sample],
    *,
    height_m: float,
) -> Optional[float]:
    """p95 of |source.hips.xz - target.hips.xz| / source_height (per frame)."""
    if not samples or height_m <= 0:
        return None
    diffs: List[float] = []
    height_cm = height_m * 100.0
    for s in samples:
        if s.src_hips is None or s.tgt_hips is None:
            continue
        dx = s.src_hips[0] - s.tgt_hips[0]
        dz = s.src_hips[2] - s.tgt_hips[2]
        diffs.append(math.sqrt(dx * dx + dz * dz) / height_cm)
    if not diffs:
        return None
    diffs.sort()
    idx = int(0.95 * (len(diffs) - 1))
    return diffs[idx]


def _knee_pop_counts(samples: Sequence[_Sample], fps: float) -> tuple:
    def _count_pops(series: Sequence[Optional[float]]) -> int:
        clean = [v for v in series if v is not None]
        if len(clean) < 5:
            return 0
        diffs2 = []
        for i in range(2, len(clean)):
            diffs2.append(clean[i] - 2.0 * clean[i - 1] + clean[i - 2])
        if not diffs2:
            return 0
        mean = sum(diffs2) / len(diffs2)
        var = sum((d - mean) ** 2 for d in diffs2) / len(diffs2)
        std = math.sqrt(var)
        if std < 1e-6:
            return 0
        return sum(1 for d in diffs2 if abs(d - mean) > KNEE_POP_OUTLIER_SIGMA * std)

    l = _count_pops([s.tgt_l_knee_angle for s in samples])
    r = _count_pops([s.tgt_r_knee_angle for s in samples])
    return l, r


def _shoulder_shrug_max(samples: Sequence[_Sample]) -> Optional[float]:
    """Max excursion (metres) of the target's shoulders above their median Y.

    A 'shrug' shows up as a sustained large positive deviation; we take the
    single largest deviation across both shoulders as a coarse signal.
    """
    if not samples:
        return None
    out: List[float] = []
    for series in ([s.tgt_l_shoulder_y for s in samples], [s.tgt_r_shoulder_y for s in samples]):
        vals = [v for v in series if v is not None]
        if len(vals) < 3:
            continue
        srt = sorted(vals)
        median = srt[len(srt) // 2]
        excursion_cm = max(v - median for v in vals)
        out.append(excursion_cm / 100.0)
    if not out:
        return None
    return max(out)
