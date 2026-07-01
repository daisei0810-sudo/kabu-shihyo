"""Phase7(実需指数・AIバブルスコア・サイクルスコア・崩壊警戒)のテスト。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.scoring.capex_trend import capex_trend_score, growth_rate_to_score
from src.scoring.collapse_watch import (
    LEVEL_THRESHOLDS,
    _level_change,
    _ma_deviation,
    compute_collapse_watch,
)
from src.scoring.components import ComponentScore, aggregate_components
from src.scoring.cycle_scores import (
    CONFIDENCE_CAP_SINGLE_STOCK,
    basket_score,
    compute_ai_cycle_score,
    compute_basket_cycle_score,
    compute_cycle_scores,
)
from src.scoring.demand_index import (
    compute_ai_bubble_score,
    compute_divergence,
    compute_real_demand_index,
)
from src.scoring.score_history import append_snapshot, compute_score_change, load_history

# ---------------------------------------------------------------------------
# capex_trend
# ---------------------------------------------------------------------------


class TestGrowthRateToScore:
    def test_zero_growth_is_50(self) -> None:
        assert growth_rate_to_score(0.0) == pytest.approx(50.0)

    def test_positive_saturation_clips_to_100(self) -> None:
        assert growth_rate_to_score(0.40) == pytest.approx(100.0)
        assert growth_rate_to_score(2.0) == pytest.approx(100.0)

    def test_negative_saturation_clips_to_0(self) -> None:
        assert growth_rate_to_score(-0.40) == pytest.approx(0.0)
        assert growth_rate_to_score(-2.0) == pytest.approx(0.0)

    def test_custom_saturation(self) -> None:
        assert growth_rate_to_score(0.30, saturation=0.30) == pytest.approx(100.0)


class TestCapexTrendScore:
    def test_insufficient_data_returns_none(self) -> None:
        s = pd.Series([100.0])
        score, note = capex_trend_score(s)
        assert score is None
        assert "不足" in note

    def test_qoq_fallback_when_less_than_5_periods(self) -> None:
        s = pd.Series([100.0, 110.0])  # +10% QoQ
        score, note = capex_trend_score(s)
        assert score is not None
        assert score > 50.0
        assert "QoQ" in note

    def test_yoy_used_when_5_or_more_periods(self) -> None:
        s = pd.Series([100.0, 105.0, 110.0, 108.0, 140.0])  # YoY = (140-100)/100 = +40%
        score, note = capex_trend_score(s)
        assert score == pytest.approx(100.0)
        assert "YoY" in note

    def test_zero_prior_value_returns_none(self) -> None:
        s = pd.Series([0.0, 105.0, 110.0, 108.0, 140.0])
        score, note = capex_trend_score(s)
        assert score is None


# ---------------------------------------------------------------------------
# components (再確認、Phase7消費側での契約を明示)
# ---------------------------------------------------------------------------


class TestAggregateComponents:
    def test_unavailable_lowers_confidence_but_not_score(self) -> None:
        comps = [
            ComponentScore("a", 80.0, 0.5, True, "verified"),
            ComponentScore("b", None, 0.5, False, "unavailable", "取得不可"),
        ]
        result = aggregate_components(comps, "test")
        assert result.score == pytest.approx(80.0)
        assert result.confidence_pct == pytest.approx(0.5)

    def test_all_unavailable_returns_none_score(self) -> None:
        comps = [ComponentScore("a", None, 1.0, False, "unavailable")]
        result = aggregate_components(comps, "test")
        assert result.score is None
        assert result.confidence_pct == 0.0


# ---------------------------------------------------------------------------
# cycle_scores
# ---------------------------------------------------------------------------


def _write_price_parquet(processed_dir: Path, key: str, n: int = 60, base: float = 100.0) -> None:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    prices = [base + i * 0.5 for i in range(n)]
    df = pd.DataFrame({"Close": prices}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


class TestBasketScore:
    def test_no_constituents_available_returns_none(self, tmp_path: Path) -> None:
        score, note, n = basket_score(["nonexistent"], tmp_path)
        assert score is None
        assert n == 0

    def test_basket_with_data_returns_score(self, tmp_path: Path) -> None:
        _write_price_parquet(tmp_path, "a")
        _write_price_parquet(tmp_path, "b")
        score, note, n = basket_score(["a", "b"], tmp_path)
        assert score is not None
        assert n == 2

    def test_partial_availability_counted_correctly(self, tmp_path: Path) -> None:
        _write_price_parquet(tmp_path, "a")
        score, note, n = basket_score(["a", "missing"], tmp_path)
        assert n == 1
        assert "1/2" in note


class TestBasketCycleScore:
    def test_single_stock_proxy_capped_at_030(self, tmp_path: Path) -> None:
        _write_price_parquet(tmp_path, "lasertec_rorze")
        result = compute_basket_cycle_score("cowos", tmp_path)
        assert result.confidence_pct <= CONFIDENCE_CAP_SINGLE_STOCK
        assert result.reference_only is True

    def test_multi_stock_basket_not_capped(self, tmp_path: Path) -> None:
        for key in ["fujikura", "sumitomo_electric", "furukawa_electric", "murata"]:
            _write_price_parquet(tmp_path, key)
        result = compute_basket_cycle_score("optical", tmp_path)
        assert result.reference_only is False
        assert result.confidence_pct == pytest.approx(0.5)

    def test_no_data_gives_zero_confidence(self, tmp_path: Path) -> None:
        result = compute_basket_cycle_score("quantum", tmp_path)
        assert result.score is None
        assert result.confidence_pct == 0.0


class TestAiCycleScore:
    def test_no_data_returns_none_score(self, tmp_path: Path) -> None:
        result = compute_ai_cycle_score(tmp_path)
        assert result.score is None
        assert result.confidence_pct == 0.0

    def test_partial_data_available(self, tmp_path: Path) -> None:
        _write_price_parquet(tmp_path, "index_sox", n=300)
        result = compute_ai_cycle_score(tmp_path)
        assert result.score is not None
        assert 0.0 < result.confidence_pct < 1.0


class TestComputeCycleScores:
    def test_returns_six_scores_electric_grid_excluded(self, tmp_path: Path) -> None:
        results = compute_cycle_scores(tmp_path)
        keys = {r.key for r in results}
        assert keys == {"ai_cycle", "optical", "quantum", "robotics", "cowos", "hbm"}
        assert "power_grid" not in keys  # 電力設備サイクルは実装しない


# ---------------------------------------------------------------------------
# demand_index
# ---------------------------------------------------------------------------


class TestDemandIndex:
    def test_real_demand_index_with_no_data_has_low_confidence(self, tmp_path: Path) -> None:
        result = compute_real_demand_index(tmp_path)
        # unavailable指標が weight を占めるため confidence は 0 (何も算入できない)
        assert result.confidence_pct == 0.0
        assert result.score is None

    def test_ai_bubble_score_with_no_data(self, tmp_path: Path) -> None:
        result = compute_ai_bubble_score(tmp_path)
        assert result.score is None

    def test_divergence_none_when_either_missing(self) -> None:
        from src.scoring.demand_index import DemandIndexResult

        a = DemandIndexResult("x", None, 0.0, 0.0, [])
        b = DemandIndexResult("y", 50.0, 0.5, 0.5, [])
        assert compute_divergence(a, b) is None

    def test_divergence_computed_when_both_available(self) -> None:
        from src.scoring.demand_index import DemandIndexResult

        a = DemandIndexResult("x", 60.0, 0.5, 0.5, [])
        b = DemandIndexResult("y", 80.0, 0.5, 0.5, [])
        assert compute_divergence(a, b) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# collapse_watch
# ---------------------------------------------------------------------------


class TestMaDeviation:
    def test_insufficient_data_returns_none(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        assert _ma_deviation(s, window=25) is None

    def test_flat_series_gives_zero_deviation(self) -> None:
        s = pd.Series([100.0] * 30)
        dev = _ma_deviation(s, window=25)
        assert dev == pytest.approx(0.0)


class TestLevelChange:
    def test_insufficient_data_returns_none(self) -> None:
        s = pd.Series([1.0, 2.0])
        assert _level_change(s, lookback=65) is None

    def test_computes_absolute_difference(self) -> None:
        s = pd.Series([4.0] * 60 + [4.5] * 6)  # 66 rows, lookback=65
        chg = _level_change(s, lookback=65)
        assert chg == pytest.approx(0.5)


class TestCollapseWatch:
    def test_level_thresholds_match_documented_values(self) -> None:
        assert LEVEL_THRESHOLDS == {1: 2, 2: 3, 3: 4}

    def test_no_data_gives_level_zero(self, tmp_path: Path) -> None:
        result = compute_collapse_watch(tmp_path)
        assert result.level == 0
        assert result.n_deteriorated == 0
        assert result.n_monitorable == 6
        assert len(result.unavailable_items) == 12

    def test_deteriorated_vix_triggers_item(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        df = pd.DataFrame({"Close": [30.0] * 30}, index=dates)  # VIX=30 > 25 閾値
        df.to_parquet(tmp_path / "price_index_vix.parquet")
        result = compute_collapse_watch(tmp_path)
        vix_item = next(i for i in result.items if i.name == "VIX急騰")
        assert vix_item.deteriorated is True
        assert result.n_deteriorated >= 1

    def test_force_reeval_only_at_level3(self, tmp_path: Path) -> None:
        result = compute_collapse_watch(tmp_path)
        assert result.level < 3
        assert result.force_reeval_holdings == []


# ---------------------------------------------------------------------------
# score_history
# ---------------------------------------------------------------------------


class TestScoreHistory:
    def test_no_history_returns_none_change(self, tmp_path: Path) -> None:
        chg = compute_score_change("test_score", 80.0, days_ago=1, history_dir=tmp_path)
        assert chg is None

    def test_append_and_load_roundtrip(self, tmp_path: Path) -> None:
        append_snapshot("test_score", 70.0, 0.5, as_of=date(2026, 6, 1), history_dir=tmp_path)
        df = load_history("test_score", history_dir=tmp_path)
        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["score"] == pytest.approx(70.0)

    def test_same_day_overwrite_is_idempotent(self, tmp_path: Path) -> None:
        append_snapshot("test_score", 70.0, 0.5, as_of=date(2026, 6, 1), history_dir=tmp_path)
        append_snapshot("test_score", 75.0, 0.5, as_of=date(2026, 6, 1), history_dir=tmp_path)
        df = load_history("test_score", history_dir=tmp_path)
        assert len(df) == 1
        assert df.iloc[0]["score"] == pytest.approx(75.0)

    def test_score_change_computed_from_past_snapshot(self, tmp_path: Path) -> None:
        append_snapshot("test_score", 60.0, 0.5, as_of=date(2026, 6, 1), history_dir=tmp_path)
        chg = compute_score_change(
            "test_score", 75.0, days_ago=1, today=date(2026, 6, 2), history_dir=tmp_path
        )
        assert chg == pytest.approx(15.0)

    def test_current_value_none_returns_none(self, tmp_path: Path) -> None:
        append_snapshot("test_score", 60.0, 0.5, as_of=date(2026, 6, 1), history_dir=tmp_path)
        chg = compute_score_change("test_score", None, days_ago=1, history_dir=tmp_path)
        assert chg is None
