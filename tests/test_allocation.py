"""Investment OS Layer9(資金配分エンジン)のテスト。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.allocation.correlation import compute_theme_correlation_matrix, theme_price_basket
from src.allocation.engine import (
    _apply_correlation_penalty,
    _normalize_and_clip,
    compute_allocations,
)
from src.allocation.models import AllocationPolicy
from src.allocation.policy import load_policy


def _write_price(processed_dir: Path, key: str, dates: pd.DatetimeIndex, closes: list) -> None:
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


class TestLoadPolicy:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        policy = load_policy(tmp_path / "nonexistent.yaml")
        assert policy.max_pct == 40.0
        assert policy.cash_floor_pct == 5.0

    def test_loads_custom_values(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yaml"
        path.write_text("max_pct: 30.0\ncash_floor_pct: 10.0\n", encoding="utf-8")
        policy = load_policy(path)
        assert policy.max_pct == 30.0
        assert policy.cash_floor_pct == 10.0
        assert policy.min_pct == 0.0  # デフォルト値を維持


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------


class TestCorrelation:
    def test_theme_price_basket_no_holdings_returns_none(self, tmp_path: Path) -> None:
        basket = theme_price_basket("china_ai", tmp_path)
        assert basket is None

    def test_correlation_matrix_insufficient_themes_is_empty(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        _write_price(tmp_path, "fujikura", dates, list(range(100, 200)))
        matrix = compute_theme_correlation_matrix(["ai_datacenter"], tmp_path)
        assert matrix.empty


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class TestApplyCorrelationPenalty:
    def test_no_correlation_matrix_no_penalty(self) -> None:
        raw = {"a": 50.0, "b": 60.0}
        result = _apply_correlation_penalty(raw, pd.DataFrame(), 0.7, 0.7)
        assert result == raw

    def test_high_correlation_penalizes_weaker_theme(self) -> None:
        raw = {"a": 50.0, "b": 80.0}
        matrix = pd.DataFrame({"a": [1.0, 0.9], "b": [0.9, 1.0]}, index=["a", "b"])
        result = _apply_correlation_penalty(raw, matrix, 0.7, 0.5)
        assert result["a"] == pytest.approx(25.0)  # 弱い方(a)にペナルティ
        assert result["b"] == pytest.approx(80.0)  # 強い方(b)は変化なし

    def test_low_correlation_no_penalty(self) -> None:
        raw = {"a": 50.0, "b": 80.0}
        matrix = pd.DataFrame({"a": [1.0, 0.2], "b": [0.2, 1.0]}, index=["a", "b"])
        result = _apply_correlation_penalty(raw, matrix, 0.7, 0.5)
        assert result == raw


class TestNormalizeAndClip:
    def test_sums_to_budget(self) -> None:
        policy = AllocationPolicy(min_pct=0.0, max_pct=100.0, cash_floor_pct=5.0)
        raw = {"a": 50.0, "b": 30.0, "c": 20.0}
        result = _normalize_and_clip(raw, policy)
        assert sum(result.values()) == pytest.approx(95.0, abs=0.01)

    def test_respects_max_pct_cap(self) -> None:
        policy = AllocationPolicy(min_pct=0.0, max_pct=40.0, cash_floor_pct=5.0)
        raw = {"a": 90.0, "b": 5.0, "c": 5.0}
        result = _normalize_and_clip(raw, policy)
        assert result["a"] <= 40.0 + 1e-6

    def test_zero_total_raw_gives_zero_allocation(self) -> None:
        policy = AllocationPolicy()
        result = _normalize_and_clip({"a": 0.0, "b": 0.0}, policy)
        assert result == {"a": 0.0, "b": 0.0}

    def test_cascading_cap_converges_without_exceeding_max(self) -> None:
        # aが上限クリップされた後の再分配で、今度はbも上限を超えるケース
        # (1回の再分配では取りこぼす。反復ウォーターフィルで両方が上限に収まることを確認)
        policy = AllocationPolicy(min_pct=0.0, max_pct=40.0, cash_floor_pct=5.0)
        result = _normalize_and_clip({"a": 80.0, "b": 40.0}, policy)
        assert result["a"] <= 40.0 + 1e-6
        assert result["b"] <= 40.0 + 1e-6


class TestComputeAllocations:
    def test_theme_without_score_is_excluded_from_recommendation(self) -> None:
        theme_scores_df = pd.DataFrame([
            {"theme": "ai_datacenter", "total": 90.0, "confidence_pct": 0.8},
            {"theme": "bio", "total": None, "confidence_pct": 0.0},
        ])
        results = compute_allocations(
            theme_scores_df, pd.DataFrame(), pd.DataFrame(), AllocationPolicy(),
        )
        by_theme = {r.theme: r for r in results}
        assert by_theme["ai_datacenter"].recommended_pct is not None
        assert by_theme["bio"].recommended_pct is None

    def test_risk_haircut_reduces_allocation(self) -> None:
        theme_scores_df = pd.DataFrame([
            {"theme": "ai_datacenter", "total": 80.0, "confidence_pct": 0.8},
            {"theme": "quantum", "total": 80.0, "confidence_pct": 0.8},
        ])
        risk_df = pd.DataFrame([{"theme": "quantum", "risk_level": 3}])
        # max_pct=100(無制限)にしてクリップの影響を排除し、ヘアカット単体の効果を検証する
        policy = AllocationPolicy(max_pct=100.0)
        results = compute_allocations(theme_scores_df, risk_df, pd.DataFrame(), policy)
        by_theme = {r.theme: r for r in results}
        quantum_pct = by_theme["quantum"].recommended_pct
        ai_pct = by_theme["ai_datacenter"].recommended_pct
        assert quantum_pct is not None and ai_pct is not None
        assert by_theme["quantum"].risk_haircut == pytest.approx(0.5)
        assert quantum_pct < ai_pct

    def test_current_pct_and_diff_from_holdings(self) -> None:
        theme_scores_df = pd.DataFrame([
            {"theme": "ai_datacenter", "total": 80.0, "confidence_pct": 0.8},
        ])
        holdings_df = pd.DataFrame([{"theme": "ai_datacenter", "current_pct": 10.0}])
        results = compute_allocations(
            theme_scores_df, pd.DataFrame(), pd.DataFrame(), AllocationPolicy(), holdings_df,
        )
        rec = results[0]
        assert rec.recommended_pct is not None
        assert rec.current_pct == pytest.approx(10.0)
        assert rec.diff_pct == pytest.approx(rec.recommended_pct - 10.0)

    def test_missing_theme_score_but_has_holdings_still_reported(self) -> None:
        theme_scores_df = pd.DataFrame([
            {"theme": "ai_datacenter", "total": 80.0, "confidence_pct": 0.8},
        ])
        holdings_df = pd.DataFrame([{"theme": "bio", "current_pct": 5.0}])
        results = compute_allocations(
            theme_scores_df, pd.DataFrame(), pd.DataFrame(), AllocationPolicy(), holdings_df,
        )
        by_theme = {r.theme: r for r in results}
        assert by_theme["bio"].recommended_pct is None
        assert by_theme["bio"].current_pct == pytest.approx(5.0)
