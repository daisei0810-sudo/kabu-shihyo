"""src/scoring/ のテスト。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.scoring.engine import (
    EXTENDED_RANKS,
    HARD_RANKS,
    RANK_WEIGHTS,
    IndicatorContribution,
    ScoreEngine,
)
from src.scoring.normalizer import percentile_rank_score, score_from_series, zscore_to_score
from src.scoring.xrp_scores import (
    XrpComponentScore,
    _aggregate_components,
    _lock_demand_stage,
    _make_component,
    compute_xrp_lock_demand,
    compute_xrp_real_demand,
)

# ---------------------------------------------------------------------------
# normalizer
# ---------------------------------------------------------------------------


class TestNormalizer:
    def test_zscore_center_is_50(self) -> None:
        assert zscore_to_score(0.0) == pytest.approx(50.0)

    def test_zscore_clip_positive(self) -> None:
        assert zscore_to_score(3.0) == pytest.approx(100.0)
        assert zscore_to_score(99.0) == pytest.approx(100.0)

    def test_zscore_clip_negative(self) -> None:
        assert zscore_to_score(-3.0) == pytest.approx(0.0)
        assert zscore_to_score(-99.0) == pytest.approx(0.0)

    def test_zscore_none_returns_none(self) -> None:
        assert zscore_to_score(None) is None

    def test_zscore_nan_returns_none(self) -> None:
        assert zscore_to_score(float("nan")) is None

    def test_percentile_at_max(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        assert percentile_rank_score(s, 5.0) == pytest.approx(100.0)

    def test_percentile_below_min(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        assert percentile_rank_score(s, 0.0) == pytest.approx(0.0)

    def test_percentile_middle(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        score = percentile_rank_score(s, 3.0)
        assert 40.0 <= score <= 70.0

    def test_score_from_series_insufficient(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        score, note = score_from_series(s, 3.0)
        assert score is None
        assert "不足" in note

    def test_score_from_series_empty(self) -> None:
        s = pd.Series([], dtype=float)
        score, note = score_from_series(s, 1.0)
        assert score is None

    def test_score_from_series_large_dataset(self) -> None:
        s = pd.Series(range(100), dtype=float)
        score, note = score_from_series(s, 99.0)
        assert score is not None
        assert score > 90.0

    def test_score_from_series_no_current(self) -> None:
        s = pd.Series(range(50), dtype=float)
        score, note = score_from_series(s, None)
        assert score is None
        assert "データなし" in note


# ---------------------------------------------------------------------------
# xrp_scores
# ---------------------------------------------------------------------------


class TestLockDemandStages:
    def test_stage_undeveloped(self) -> None:
        assert _lock_demand_stage(20.0) == "未発生"

    def test_stage_initial(self) -> None:
        assert _lock_demand_stage(40.0) == "初動"

    def test_stage_accelerating(self) -> None:
        assert _lock_demand_stage(60.0) == "加速"

    def test_stage_serious(self) -> None:
        assert _lock_demand_stage(85.0) == "本格化"

    def test_stage_shock(self) -> None:
        assert _lock_demand_stage(95.0) == "需給ショック"

    def test_stage_boundary_30(self) -> None:
        assert _lock_demand_stage(30.0) == "初動"

    def test_stage_boundary_50(self) -> None:
        assert _lock_demand_stage(50.0) == "加速"


class TestAggregateComponents:
    def test_no_available_returns_none(self) -> None:
        comps = [
            XrpComponentScore(
                name="X", score=None, weight=0.5,
                available=False, data_quality="unavailable",
            ),
        ]
        result = _aggregate_components(comps, "test")
        assert result.score is None
        assert result.confidence_pct == 0.0

    def test_single_available_component(self) -> None:
        comps = [
            XrpComponentScore(
                name="A", score=60.0, weight=1.0,
                available=True, data_quality="verified",
            ),
        ]
        result = _aggregate_components(comps, "test")
        assert result.score == pytest.approx(60.0, abs=0.1)
        assert result.confidence_pct == pytest.approx(1.0, abs=0.01)

    def test_unavailable_reduces_confidence(self) -> None:
        # verified=1.0, unavailable=0.45 → total=1.45
        comps = [
            XrpComponentScore(
                name="A", score=80.0, weight=1.0,
                available=True, data_quality="verified",
            ),
            XrpComponentScore(
                name="B", score=None, weight=0.45,
                available=False, data_quality="unavailable",
            ),
        ]
        result = _aggregate_components(comps, "test")
        assert result.score == pytest.approx(80.0, abs=0.1)
        assert result.confidence_pct == pytest.approx(1.0 / 1.45, abs=0.01)

    def test_weighted_average_two_components(self) -> None:
        comps = [
            XrpComponentScore(
                name="A", score=60.0, weight=0.6,
                available=True, data_quality="verified",
            ),
            XrpComponentScore(
                name="B", score=40.0, weight=0.4,
                available=True, data_quality="verified",
            ),
        ]
        result = _aggregate_components(comps, "test")
        # weighted avg = (60*0.6 + 40*0.4) / 1.0 = 52.0
        assert result.score == pytest.approx(52.0, abs=0.1)
        assert result.confidence_pct == pytest.approx(1.0, abs=0.01)

    def test_data_coverage(self) -> None:
        comps = [
            XrpComponentScore(
                name="A", score=70.0, weight=0.5,
                available=True, data_quality="verified",
            ),
            XrpComponentScore(
                name="B", score=None, weight=0.5,
                available=False, data_quality="unavailable",
            ),
        ]
        result = _aggregate_components(comps, "test")
        # 1 verified available / 2 total = 0.5
        assert result.data_coverage_pct == pytest.approx(0.5, abs=0.01)


class TestXrpDemandNoData:
    def test_lock_demand_no_crash_without_data(self) -> None:
        result = compute_xrp_lock_demand()
        # score は None か 0-100
        assert result.score is None or 0.0 <= result.score <= 100.0
        assert 0.0 <= result.confidence_pct <= 1.0
        # stage は score がある場合のみ設定される
        if result.score is not None:
            assert result.stage != ""

    def test_real_demand_no_crash_without_data(self) -> None:
        result = compute_xrp_real_demand()
        assert result.score is None or 0.0 <= result.score <= 100.0
        assert 0.0 <= result.confidence_pct <= 1.0


class TestMakeComponent:
    def test_make_component_no_data(self) -> None:
        comp = _make_component("test", None, None, weight=0.5)
        assert comp.available is False
        assert comp.score is None

    def test_make_component_insufficient_data(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])  # < 30 rows
        comp = _make_component("test", s, 3.0, weight=0.5)
        assert comp.available is False
        assert comp.score is None

    def test_make_component_sufficient_data(self) -> None:
        s = pd.Series(range(50), dtype=float)
        comp = _make_component("test", s, 49.0, weight=0.5)
        assert comp.available is True
        assert comp.score is not None
        assert 0.0 <= comp.score <= 100.0


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class TestScoreEngine:
    def test_rank_weights_ordering(self) -> None:
        assert RANK_WEIGHTS["A+"] >= RANK_WEIGHTS["A"]
        assert RANK_WEIGHTS["A"] >= RANK_WEIGHTS["B"]
        assert RANK_WEIGHTS["B"] >= RANK_WEIGHTS["C"]
        assert RANK_WEIGHTS["D"] == 0.0

    def test_hard_is_subset_of_extended(self) -> None:
        assert HARD_RANKS.issubset(EXTENDED_RANKS)

    def test_engine_no_scorecard_returns_none(self, tmp_path: pytest.TempPathFactory) -> None:
        engine = ScoreEngine(scorecard_path=str(tmp_path / "nonexistent.csv"))
        result = engine.compute("fujikura")
        assert result.hard_score is None
        assert result.extended_score is None
        assert "Step2" in result.note

    def test_engine_proxy_not_in_hard(self, tmp_path: pytest.TempPathFactory) -> None:
        """proxy指標は Hard スコアに算入されない。"""
        sc_data = pd.DataFrame([{
            "indicator": "sox_index",
            "target": "lasertec_rorze",
            "data_quality": "proxy",
            "confidence_weight": 0.5,
            "rank": "B",
            "adopted": True,
            "confidence_note": "",
        }])
        sc_path = tmp_path / "scorecard.csv"
        sc_data.to_csv(sc_path, index=False)

        engine = ScoreEngine(
            scorecard_path=str(sc_path),
            processed_dir=str(tmp_path),
        )
        result = engine.compute("lasertec_rorze")
        assert len(result.contributions) == 1
        assert result.contributions[0].key == "sox_index"
        # proxy は Hard に入らない
        assert result.contributions[0].in_hard is False
        # proxy の B は Extended に入る
        assert result.contributions[0].in_extended is True

    def test_engine_verified_b_in_hard(self, tmp_path: pytest.TempPathFactory) -> None:
        """verified B ランクは Hard に算入される。"""
        sc_data = pd.DataFrame([{
            "indicator": "xrpl_tx_count",
            "target": "xrp",
            "data_quality": "verified",
            "confidence_weight": 1.0,
            "rank": "B",
            "adopted": True,
            "confidence_note": "",
        }])
        sc_path = tmp_path / "scorecard.csv"
        sc_data.to_csv(sc_path, index=False)

        engine = ScoreEngine(
            scorecard_path=str(sc_path),
            processed_dir=str(tmp_path),
        )
        result = engine.compute("xrp")
        assert len(result.contributions) == 1
        assert result.contributions[0].in_hard is True

    def test_engine_d_rank_excluded_from_extended(self, tmp_path: pytest.TempPathFactory) -> None:
        """D ランクは Extended にも算入されない。"""
        sc_data = pd.DataFrame([{
            "indicator": "stablecoin_tvl",
            "target": "xrp",
            "data_quality": "verified",
            "confidence_weight": 1.0,
            "rank": "D",
            "adopted": False,
            "confidence_note": "履歴不足",
        }])
        sc_path = tmp_path / "scorecard.csv"
        sc_data.to_csv(sc_path, index=False)

        engine = ScoreEngine(
            scorecard_path=str(sc_path),
            processed_dir=str(tmp_path),
        )
        result = engine.compute("xrp")
        assert result.contributions[0].in_extended is False
        assert result.extended_score is None

    def test_weighted_avg_all_none(self) -> None:
        contribs = [
            IndicatorContribution(
                key="x", data_quality="verified", rank="C",
                adopted=False, raw_value=None, score_0_100=None,
                rank_weight=0.3, quality_weight=0.5, effective_weight=0.15,
                in_hard=False, in_extended=True,
            ),
        ]
        score, conf = ScoreEngine._weighted_avg(contribs)
        assert score is None
        assert conf == 0.0

    def test_weighted_avg_single(self) -> None:
        contribs = [
            IndicatorContribution(
                key="x", data_quality="verified", rank="B",
                adopted=True, raw_value=100.0, score_0_100=75.0,
                rank_weight=0.6, quality_weight=1.0, effective_weight=0.6,
                in_hard=True, in_extended=True,
            ),
        ]
        score, conf = ScoreEngine._weighted_avg(contribs)
        assert score == pytest.approx(75.0, abs=0.1)
        assert conf == pytest.approx(1.0, abs=0.01)
