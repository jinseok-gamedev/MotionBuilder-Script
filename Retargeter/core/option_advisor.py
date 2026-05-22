"""Rule-based recommender for HumanIK and PlotConfig retargeting options.

Given a :class:`~Retargeter.core.skeleton_features.PairFeatures` describing
how source and target characters differ, this module produces an
:class:`OptionRecommendation` that the UI then applies to its widgets.

Design goals
------------

* **No black box.** Every change carries a human-readable ``reason`` so the
  operator can audit and override anything.
* **Declarative rules.** New heuristics are added as a single ``Rule`` entry
  in :data:`RULES`; the evaluator does the boilerplate.
* **Backend-swappable.** :class:`Recommender` is a Protocol so a future
  ``ModelBackedRecommender`` (an external sklearn / xgboost / torch model)
  can drop in with the same interface. Rule-based stays as the safe default
  / fallback.

The four HIK options the advisor speaks about
---------------------------------------------

* ``HIKForceActorSpaceId``       - bias retarget into actor space (shoulder
                                   width / hand position preservation).
* ``HIKScaleCompensationId``     - compensate large height differences.
* ``HIKTopSpineCorrectionId``    - clean up upper-spine drift when the
                                   target has a different spine subdivision.
* ``HIKFingerPropagationId``     - keep finger curl from leaking into the
                                   wrist when the rigs disagree on fingers.

The advisor emits booleans for these and lets
:func:`Retargeter.core.retarget_engine.apply_hik_options` resolve the
actual property name on the live character.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from typing import Protocol  # Python 3.8+
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[misc,assignment]

from .retarget_engine import PlotConfig
from .skeleton_features import PairFeatures


# The four HIK option keys the advisor speaks about. Kept as a tuple so the
# UI can iterate them in display order without duplicating the list.
HIK_OPTION_KEYS = (
    "HIKForceActorSpaceId",
    "HIKScaleCompensationId",
    "HIKTopSpineCorrectionId",
    "HIKFingerPropagationId",
)


# ----------------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------------


@dataclass
class OptionRecommendation:
    """The advisor's output. ``changed_fields`` lets the UI highlight only
    the widgets it actually touched (yellow background)."""

    plot: PlotConfig
    match_source: bool
    hik: Dict[str, bool] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    changed_fields: Set[str] = field(default_factory=set)
    advisor_version: str = "rules-v1"


class Recommender(Protocol):  # type: ignore[misc]
    """Interface every recommendation backend must implement."""

    def recommend(
        self,
        features: PairFeatures,
        *,
        current_plot: PlotConfig,
        current_match_source: bool,
        current_hik: Dict[str, bool],
    ) -> OptionRecommendation:
        ...


# ----------------------------------------------------------------------------
# Rule scaffold
# ----------------------------------------------------------------------------


@dataclass
class _Context:
    """Mutable bundle a Rule writes into. Kept private to this module."""

    plot: PlotConfig
    match_source: bool
    hik: Dict[str, bool]
    reasons: List[str]
    changed: Set[str]

    def set_match_source(self, value: bool, reason: str) -> None:
        if self.match_source != value:
            self.changed.add("match_source")
        self.match_source = bool(value)
        self.reasons.append(reason)

    def set_hik(self, key: str, value: bool, reason: str) -> None:
        previous = self.hik.get(key)
        if previous != value:
            self.changed.add(f"hik.{key}")
        self.hik[key] = bool(value)
        self.reasons.append(reason)

    def set_plot(self, attr: str, value: Any, reason: str) -> None:
        if getattr(self.plot, attr) != value:
            self.changed.add(f"plot.{attr}")
        setattr(self.plot, attr, value)
        self.reasons.append(reason)


# A rule is just a function that mutates the context. Keeping them as plain
# callables (rather than a class hierarchy) makes the rule list easy to read
# and re-order, and trivial to A/B test by commenting one out.
Rule = Callable[[PairFeatures, _Context], None]


def _rule_height_scale(features: PairFeatures, ctx: _Context) -> None:
    ratio = features.height_ratio
    if ratio is None:
        ctx.reasons.append("height_ratio: 측정 불가 (Hips/Foot 슬롯 누락) - match_source 그대로 둡니다")
        return
    if ratio < 0.7 or ratio > 1.4:
        ctx.set_match_source(False, f"키 차이 큼 (ratio={ratio:.2f}) - 본질 보존 우선, match_source=off")
        ctx.set_hik(
            "HIKScaleCompensationId",
            True,
            f"키 차이 큼 (ratio={ratio:.2f}) - HIKScaleCompensation=on",
        )
    else:
        ctx.set_match_source(True, f"키 차이 보통 (ratio={ratio:.2f}) - match_source=on")
        ctx.set_hik(
            "HIKScaleCompensationId",
            False,
            f"키 차이 보통 (ratio={ratio:.2f}) - HIKScaleCompensation=off",
        )


def _rule_shoulder_width(features: PairFeatures, ctx: _Context) -> None:
    ratio = features.shoulder_width_ratio
    if ratio is None:
        ctx.set_hik(
            "HIKForceActorSpaceId",
            False,
            "shoulder_width_ratio 측정 불가 - HIKForceActorSpace=off (기본)",
        )
        return
    diff = abs(ratio - 1.0)
    if diff > 0.25:
        ctx.set_hik(
            "HIKForceActorSpaceId",
            True,
            f"어깨너비 차이 {diff*100:.0f}% (ratio={ratio:.2f}) - actor space에서 손 위치 보존",
        )
    else:
        ctx.set_hik(
            "HIKForceActorSpaceId",
            False,
            f"어깨너비 차이 작음 (ratio={ratio:.2f}) - HIKForceActorSpace=off",
        )


def _rule_spine_segments(features: PairFeatures, ctx: _Context) -> None:
    delta = features.spine_segments_delta
    if delta >= 2:
        ctx.set_hik(
            "HIKTopSpineCorrectionId",
            True,
            f"스파인 분절 수 차이 {delta} - HIKTopSpineCorrection=on (상체 비틀림 보정)",
        )
    else:
        ctx.set_hik(
            "HIKTopSpineCorrectionId",
            False,
            f"스파인 분절 수 차이 {delta} - HIKTopSpineCorrection=off",
        )


def _rule_finger_topology(features: PairFeatures, ctx: _Context) -> None:
    if features.finger_count_match:
        ctx.set_hik(
            "HIKFingerPropagationId",
            False,
            "양쪽 손가락 슬롯 수 일치 - HIKFingerPropagation=off",
        )
        return
    src = features.source
    tgt = features.target
    ctx.set_hik(
        "HIKFingerPropagationId",
        True,
        (
            f"손가락 슬롯 수 불일치 "
            f"(L src={src.finger_count_l}/tgt={tgt.finger_count_l}, "
            f"R src={src.finger_count_r}/tgt={tgt.finger_count_r}) - "
            f"HIKFingerPropagation=on"
        ),
    )
    ctx.set_plot(
        "plot_translation",
        False,
        "손가락 불일치 - plot_translation=off (손 끝 떨림 노이즈 차단, 추후 검증 필요)",
    )


def _rule_pose_mismatch(features: PairFeatures, ctx: _Context) -> None:
    diff = features.arm_angle_diff_deg
    if diff is None:
        return
    if diff > 15.0:
        ctx.set_match_source(
            False,
            (
                f"팔 각도 차이 {diff:.1f}° (T-pose vs A-pose 추정) - "
                "match_source=off, 자동 보정만으로 부족하므로 수동 자세 매칭 권장"
            ),
        )


def _rule_plot_rate(features: PairFeatures, ctx: _Context) -> None:
    """Sync plot rate with the current take's frame rate.

    We avoid importing pyfbsdk at module-import time so unit tests / static
    checks can load this module outside of MoBu. The import lives inside
    the rule body and any failure leaves the existing plot_rate alone.
    """
    try:
        from pyfbsdk import FBSystem  # type: ignore

        take = FBSystem().CurrentTake
        if take is None:
            return
        try:
            period = take.LocalTimeSpan.GetStop() - take.LocalTimeSpan.GetStart()
            _ = period  # touch to ensure the take is alive
        except Exception:
            pass
        # Take.FrameRate exists on most MoBu versions; if not, FBPlayerControl
        # exposes the per-take rate.
        rate = None
        try:
            rate = float(take.FrameRate)  # type: ignore[attr-defined]
        except Exception:
            try:
                from pyfbsdk import FBPlayerControl  # type: ignore

                rate = float(FBPlayerControl().GetTransportFps())
            except Exception:
                rate = None
        if rate is None or rate <= 0:
            return
        rate_int = max(1, int(round(rate)))
        if ctx.plot.plot_rate != rate_int:
            ctx.set_plot(
                "plot_rate",
                rate_int,
                f"현재 take frame rate = {rate_int} fps에 맞춰 plot_rate 설정",
            )
    except Exception:
        return


# Declarative rule list. Order matters only inasmuch as later rules can
# override earlier ones (e.g. _rule_pose_mismatch overrides match_source).
RULES: List[Rule] = [
    _rule_height_scale,
    _rule_shoulder_width,
    _rule_spine_segments,
    _rule_finger_topology,
    _rule_pose_mismatch,
    _rule_plot_rate,
]


# ----------------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------------


class RuleBasedRecommender:
    """Default backend - applies :data:`RULES` in order."""

    advisor_version = "rules-v1"

    def __init__(self, rules: Optional[List[Rule]] = None):
        self._rules = list(rules) if rules is not None else list(RULES)

    def recommend(
        self,
        features: PairFeatures,
        *,
        current_plot: PlotConfig,
        current_match_source: bool,
        current_hik: Dict[str, bool],
    ) -> OptionRecommendation:
        # Start from the user's current values so untouched options survive
        # an Auto pass. The advisor's job is *deltas*, not a from-scratch
        # configuration override.
        plot = PlotConfig(
            plot_rate=current_plot.plot_rate,
            plot_translation=current_plot.plot_translation,
            use_constant_key_reducer=current_plot.use_constant_key_reducer,
            constant_key_reducer_keep_one=current_plot.constant_key_reducer_keep_one,
            precise_time_discontinuities=current_plot.precise_time_discontinuities,
            plot_all_takes=current_plot.plot_all_takes,
            rotation_filter=current_plot.rotation_filter,
        )
        hik = {key: bool(current_hik.get(key, False)) for key in HIK_OPTION_KEYS}
        ctx = _Context(
            plot=plot,
            match_source=bool(current_match_source),
            hik=hik,
            reasons=[],
            changed=set(),
        )

        if features is None:
            ctx.reasons.append("PairFeatures가 None - 추천을 건너뛰고 현재값을 유지합니다.")
        else:
            if features.notes:
                for note in features.notes:
                    ctx.reasons.append(f"피처 추출 경고: {note}")
            for rule in self._rules:
                try:
                    rule(features, ctx)
                except Exception as exc:
                    ctx.reasons.append(f"룰 {rule.__name__} 실행 중 오류 (무시): {exc!r}")

        return OptionRecommendation(
            plot=ctx.plot,
            match_source=ctx.match_source,
            hik=ctx.hik,
            reasons=ctx.reasons,
            changed_fields=ctx.changed,
            advisor_version=self.advisor_version,
        )


def default_recommender() -> Recommender:
    """Factory: rule-based today, ML-backed once a model file ships."""
    return RuleBasedRecommender()
