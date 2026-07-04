"""Investment OS Layer4(テーマスコアリング、6軸ルーブリック)のテスト。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.scoring.theme_score import (
    _earnings_axis,
    _fund_flow_axis,
    _load_structural_scores,
    _policy_tailwind_axis,
    _structural_change_axis,
    _valuation_axis,
    compute_theme_score,
)


class TestStructuralChangeAxis:
    def test_missing_theme_is_unavailable(self) -> None:
        df = pd.DataFrame(columns=["theme", "score", "updated_at", "note"])
        axis = _structural_change_axis("ai_datacenter", df)
        assert axis.score is None
        assert axis.available is False
        assert axis.data_quality == "unavailable"

    def test_manual_score_scaled_to_0_100(self) -> None:
        df = pd.DataFrame([{"theme": "ai_datacenter", "score": 15, "updated_at": "2026-07-01"}])
        axis = _structural_change_axis("ai_datacenter", df)
        assert axis.score == pytest.approx(50.0)
        assert axis.available is True
        assert axis.weight == 30.0

    def test_load_structural_scores_missing_file_returns_empty(self, tmp_path: Path) -> None:
        df = _load_structural_scores(tmp_path / "nonexistent.csv")
        assert df.empty


class TestEarningsAxis:
    def test_no_capex_data_is_unavailable(self, tmp_path: Path) -> None:
        axis = _earnings_axis("ai_datacenter", tmp_path)
        assert axis.score is None
        assert axis.data_quality == "unavailable"

    def test_capex_growth_scores_high(self, tmp_path: Path) -> None:
        dates = pd.date_range("2024-01-01", periods=6, freq="QE")
        df = pd.DataFrame({"hyperscaler_capex_total": [100, 105, 110, 115, 120, 200]}, index=dates)
        df.to_parquet(tmp_path / "capex_hyperscaler_total.parquet")
        axis = _earnings_axis("ai_datacenter", tmp_path)
        assert axis.score is not None
        assert axis.score > 50.0  # YoY大幅増 → 高スコア


class TestValuationAxis:
    def test_always_unavailable(self) -> None:
        axis = _valuation_axis()
        assert axis.score is None
        assert axis.data_quality == "unavailable"


class TestFundFlowAxis:
    def test_no_volume_data_is_unavailable(self, tmp_path: Path) -> None:
        axis = _fund_flow_axis("ev_physical_ai", tmp_path)
        assert axis.score is None

    def test_volume_data_produces_score(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        df = pd.DataFrame({"Volume": [1000.0] * 39 + [5000.0]}, index=dates)
        df.to_parquet(tmp_path / "price_tesla.parquet")
        axis = _fund_flow_axis("ev_physical_ai", tmp_path)
        assert axis.score is not None
        assert axis.available is True


class TestPolicyTailwindAxis:
    def test_no_connection_is_unavailable(self) -> None:
        axis = _policy_tailwind_axis("ai_datacenter", None, date(2026, 7, 4))
        assert axis.score is None
        assert axis.data_quality == "unavailable"


class TestComputeThemeScore:
    def test_only_structural_axis_available_total_matches_structural(
        self, tmp_path: Path
    ) -> None:
        manual_df = pd.DataFrame(
            [{"theme": "ai_datacenter", "score": 30, "updated_at": "2026-07-01"}]
        )
        result = compute_theme_score(
            theme="ai_datacenter", name_ja="AIデータセンター", manual_df=manual_df,
            cycle_by_key={}, xrp_real=None, xrp_lock=None, materials_conn=None,
            as_of=date(2026, 7, 4), processed_dir=tmp_path,
        )
        # 構造変化のみ available(score=100) → total=100, confidence=30/100
        assert result.total == pytest.approx(100.0)
        assert result.confidence_pct == pytest.approx(0.3)

    def test_no_data_at_all_gives_none_total(self, tmp_path: Path) -> None:
        manual_df = pd.DataFrame(columns=["theme", "score", "updated_at"])
        result = compute_theme_score(
            theme="bio", name_ja="バイオ", manual_df=manual_df,
            cycle_by_key={}, xrp_real=None, xrp_lock=None, materials_conn=None,
            as_of=date(2026, 7, 4), processed_dir=tmp_path,
        )
        assert result.total is None
        assert result.confidence_pct == 0.0
