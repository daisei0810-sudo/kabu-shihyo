"""Investment OS Layer6(リスクエンジン)のテスト。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.risk.detectors import (
    detect_capex_cut,
    detect_competition_loss,
    detect_customer_churn,
    detect_dilution,
    detect_regulation,
    detect_tech_defeat,
)
from src.risk.engine import summarize_by_theme
from src.risk.models import RiskItem


def _write_price(processed_dir: Path, key: str, dates: pd.DatetimeIndex, closes: list) -> None:
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


class TestDetectCapexCut:
    def test_non_ai_datacenter_theme_is_unavailable(self, tmp_path: Path) -> None:
        item = detect_capex_cut("quantum", "quantinuum", date(2026, 7, 6), tmp_path)
        assert item.risk_score is None
        assert item.data_quality == "unavailable"

    def test_no_data_is_unavailable(self, tmp_path: Path) -> None:
        item = detect_capex_cut("ai_datacenter", "fujikura", date(2026, 7, 6), tmp_path)
        assert item.deteriorated is None

    def test_qoq_decline_flags_deterioration(self, tmp_path: Path) -> None:
        dates = pd.date_range("2025-01-01", periods=5, freq="QE")
        df = pd.DataFrame({"hyperscaler_capex_total": [100, 110, 120, 130, 100]}, index=dates)
        df.to_parquet(tmp_path / "capex_hyperscaler_total.parquet")
        item = detect_capex_cut("ai_datacenter", "fujikura", date(2026, 7, 6), tmp_path)
        assert item.deteriorated is True
        assert item.risk_score is not None and item.risk_score > 0

    def test_qoq_growth_not_deteriorated(self, tmp_path: Path) -> None:
        dates = pd.date_range("2025-01-01", periods=5, freq="QE")
        df = pd.DataFrame({"hyperscaler_capex_total": [100, 110, 120, 130, 150]}, index=dates)
        df.to_parquet(tmp_path / "capex_hyperscaler_total.parquet")
        item = detect_capex_cut("ai_datacenter", "fujikura", date(2026, 7, 6), tmp_path)
        assert item.deteriorated is False
        assert item.risk_score == 0.0


class TestDetectCompetitionLoss:
    def test_no_peer_basket_is_unavailable(self, tmp_path: Path) -> None:
        item = detect_competition_loss("semicap", "kioxia", date(2026, 7, 6), tmp_path)
        assert item.data_quality == "unavailable"

    def test_underperformance_flags_deterioration(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=50, freq="D")
        # targetは下落、ピアは横ばい → targetの相対モメンタムが劣化
        _write_price(tmp_path, "fanuc", dates, [100.0] * 10 + list(range(100, 60, -1)))
        _write_price(tmp_path, "harmonic", dates, [100.0] * 50)
        _write_price(tmp_path, "yaskawa", dates, [100.0] * 50)
        _write_price(tmp_path, "nabtesco", dates, [100.0] * 50)
        item = detect_competition_loss("robotics_fa", "fanuc", date(2026, 7, 6), tmp_path)
        assert item.deteriorated is True
        assert item.risk_score is not None and item.risk_score > 0


class TestDetectTechDefeat:
    def test_always_unavailable(self) -> None:
        item = detect_tech_defeat("quantum", "quantinuum", date(2026, 7, 6))
        assert item.risk_score is None
        assert item.data_quality == "unavailable"


class TestMaterialsBasedDetectors:
    def test_no_connection_is_unavailable(self) -> None:
        for fn in (detect_regulation, detect_dilution, detect_customer_churn):
            item = fn("ai_datacenter", "fujikura", None, date(2026, 7, 6))
            assert item.risk_score is None
            assert item.data_quality == "unavailable"


class TestSummarizeByTheme:
    def _item(self, theme: str, target: str, category: str, deteriorated: bool | None) -> RiskItem:
        return RiskItem(
            theme=theme, target=target, category=category, risk_score=80.0 if deteriorated else 0.0,
            deteriorated=deteriorated, evidence="test", data_quality="verified",
            as_of="2026-07-06",
        )

    def test_risk_level_zero_when_no_deterioration(self) -> None:
        items = [self._item("ai_datacenter", "fujikura", "capex_cut", False)]
        summaries = summarize_by_theme(items)
        assert summaries[0].risk_level == 0

    def test_risk_level_scales_with_deteriorated_categories(self) -> None:
        items = [
            self._item("robotics_fa", "fanuc", "capex_cut", True),
            self._item("robotics_fa", "fanuc", "competition_loss", True),
            self._item("robotics_fa", "fanuc", "dilution", True),
        ]
        summaries = summarize_by_theme(items)
        assert summaries[0].risk_level == 2  # 3カテゴリ悪化 → threshold{2:3}によりlevel2

    def test_unobservable_categories_excluded_from_monitorable(self) -> None:
        items = [
            self._item("ai_datacenter", "fujikura", "capex_cut", None),
            self._item("ai_datacenter", "fujikura", "competition_loss", True),
        ]
        summaries = summarize_by_theme(items)
        assert summaries[0].n_monitorable == 1
        assert summaries[0].n_deteriorated == 1

    def test_worst_case_across_instruments_in_same_theme(self) -> None:
        items = [
            self._item("robotics_fa", "fanuc", "capex_cut", False),
            self._item("robotics_fa", "harmonic", "capex_cut", True),
        ]
        summaries = summarize_by_theme(items)
        assert summaries[0].n_deteriorated == 1
