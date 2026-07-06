"""Investment OS Layer5(指標重み自動更新)のテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.config import DataQuality, DataSource, Indicator, Layer
from src.prediction.models import Evaluation, Prediction
from src.prediction.weight_updater import (
    MIN_EVALUATIONS_FOR_UPDATE,
    MULTIPLIER_MAX,
    MULTIPLIER_MIN,
    _clip_multiplier,
    _rank_weight_for_indicator,
    compute_attributions,
    update_weights,
)


def _prediction(pred_id: str, evidence: list[str]) -> Prediction:
    return Prediction(
        prediction_id=pred_id, created_at="now", as_of="2026-07-06",
        source_layer="decision", theme="ai_datacenter", target="fujikura",
        judgment="保有継続", expected_direction=1, score_at_prediction=90.0,
        confidence_at_prediction=0.8, baseline_date="2026-07-06", baseline_price=100.0,
        evidence_json=__import__("json").dumps(evidence),
    )


def _evaluation(
    eval_id: str, pred_id: str, status: str = "evaluated",
    direction_hit: bool | None = True, excess_return: float | None = 0.05,
) -> Evaluation:
    return Evaluation(
        evaluation_id=eval_id, prediction_id=pred_id, horizon="3m", due_date="2026-10-04",
        status=status, direction_hit=direction_hit, excess_return=excess_return,
    )


class TestClipMultiplier:
    def test_clips_to_min(self) -> None:
        assert _clip_multiplier(0.01) == MULTIPLIER_MIN

    def test_clips_to_max(self) -> None:
        assert _clip_multiplier(10.0) == MULTIPLIER_MAX

    def test_passes_through_in_range(self) -> None:
        assert _clip_multiplier(1.1) == pytest.approx(1.1)


class TestComputeAttributions:
    def test_pending_evaluations_excluded(self) -> None:
        preds = {"p1": _prediction("p1", ["ind_a"])}
        evals = [_evaluation("e1", "p1", status="pending")]
        attributions = compute_attributions(evals, preds)
        assert attributions == {}

    def test_none_direction_hit_excluded(self) -> None:
        preds = {"p1": _prediction("p1", ["ind_a"])}
        evals = [_evaluation("e1", "p1", direction_hit=None)]
        attributions = compute_attributions(evals, preds)
        assert attributions == {}

    def test_evaluated_prediction_attributes_to_all_evidence(self) -> None:
        preds = {"p1": _prediction("p1", ["ind_a", "ind_b"])}
        evals = [_evaluation("e1", "p1", direction_hit=True, excess_return=0.1)]
        attributions = compute_attributions(evals, preds)
        assert attributions["ind_a"] == [(True, 0.1)]
        assert attributions["ind_b"] == [(True, 0.1)]

    def test_missing_prediction_skipped(self) -> None:
        evals = [_evaluation("e1", "nonexistent")]
        attributions = compute_attributions(evals, {})
        assert attributions == {}


class TestRankWeightForIndicator:
    def test_no_scorecard_returns_neutral(self) -> None:
        assert _rank_weight_for_indicator("ind_a", pd.DataFrame()) == 1.0

    def test_indicator_not_in_scorecard_returns_neutral(self) -> None:
        df = pd.DataFrame([{"indicator": "other", "rank": "A"}])
        assert _rank_weight_for_indicator("ind_a", df) == 1.0

    def test_takes_worst_rank_across_targets(self) -> None:
        df = pd.DataFrame([
            {"indicator": "ind_a", "rank": "A"},
            {"indicator": "ind_a", "rank": "D"},
        ])
        assert _rank_weight_for_indicator("ind_a", df) == 0.0


class TestUpdateWeights:
    def _indicator(self, key: str, data_quality: DataQuality = DataQuality.VERIFIED) -> Indicator:
        return Indicator(
            key=key, name_ja=key, layer=Layer.AI_DATACENTER,
            source=DataSource.YFINANCE, data_quality=data_quality, targets=["fujikura"],
        )

    def test_below_threshold_keeps_default_multiplier(self) -> None:
        attributions: dict[str, list[tuple[bool, float | None]]] = {"ind_a": [(True, 0.05)] * 5}
        results = update_weights(attributions, pd.DataFrame(), None, [self._indicator("ind_a")])
        assert results[0].learned_multiplier == pytest.approx(1.0)
        assert results[0].n_evaluations == 5

    def test_high_hit_rate_increases_multiplier(self) -> None:
        attributions: dict[str, list[tuple[bool, float | None]]] = {
            "ind_a": [(True, 0.05)] * MIN_EVALUATIONS_FOR_UPDATE
        }
        results = update_weights(attributions, pd.DataFrame(), None, [self._indicator("ind_a")])
        assert results[0].learned_multiplier > 1.0
        assert results[0].hit_rate == pytest.approx(1.0)

    def test_low_hit_rate_decreases_multiplier(self) -> None:
        attributions: dict[str, list[tuple[bool, float | None]]] = {
            "ind_a": [(False, -0.05)] * MIN_EVALUATIONS_FOR_UPDATE
        }
        results = update_weights(attributions, pd.DataFrame(), None, [self._indicator("ind_a")])
        assert results[0].learned_multiplier < 1.0
        assert results[0].hit_rate == pytest.approx(0.0)

    def test_d_rank_zeroes_effective_weight_regardless_of_multiplier(self) -> None:
        attributions: dict[str, list[tuple[bool, float | None]]] = {
            "ind_a": [(True, 0.05)] * MIN_EVALUATIONS_FOR_UPDATE
        }
        scorecard = pd.DataFrame([{"indicator": "ind_a", "rank": "D"}])
        results = update_weights(attributions, scorecard, None, [self._indicator("ind_a")])
        assert results[0].effective_weight == 0.0

    def test_previous_multiplier_carried_forward(self) -> None:
        attributions: dict[str, list[tuple[bool, float | None]]] = {"ind_a": [(True, 0.05)] * 3}
        results = update_weights(
            attributions, pd.DataFrame(), {"ind_a": 1.5}, [self._indicator("ind_a")],
        )
        assert results[0].learned_multiplier == pytest.approx(1.5)

    def test_no_evidence_gives_none_hit_rate(self) -> None:
        results = update_weights({}, pd.DataFrame(), None, [self._indicator("ind_a")])
        assert results[0].hit_rate is None
        assert results[0].n_evaluations == 0
