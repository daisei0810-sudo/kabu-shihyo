"""Investment OS Layer7/8(新規発掘)のテスト。"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import DataQuality, Instrument, Layer
from src.discovery.companies import _momentum_pct, compute_discovery_companies
from src.discovery.themes import DiscoveryTheme, Theme, compute_discovery_themes


def _write_price(processed_dir: Path, key: str, dates: pd.DatetimeIndex, closes: list) -> None:
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


def _inst(key: str, layer: Layer, held: bool) -> Instrument:
    return Instrument(
        key=key, name_ja=key, layer=layer, ticker=f"{key.upper()}",
        held=held, data_quality=DataQuality.VERIFIED,
    )


class TestMomentumPct:
    def test_insufficient_data_is_none(self) -> None:
        close = pd.Series([100.0] * 10)
        assert _momentum_pct(close, lookback=65) is None

    def test_positive_move_is_positive_pct(self) -> None:
        close = pd.Series([100.0] * 30 + [110.0])
        result = _momentum_pct(close, lookback=25)
        assert result is not None
        assert abs(result - 10.0) < 1e-6


class TestComputeDiscoveryCompanies:
    def test_only_non_held_instruments_are_ranked(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed"
        processed.mkdir()
        output_dir = tmp_path / "outputs"
        output_dir.mkdir()

        dates = pd.date_range("2026-01-01", periods=70, freq="D")
        _write_price(processed, "held_co", dates, [100.0] * 70)
        _write_price(processed, "candidate_a", dates, [100.0] * 69 + [120.0])
        _write_price(processed, "candidate_b", dates, [100.0] * 70)

        instruments = [
            _inst("held_co", Layer.ROBOTICS_FA, held=True),
            _inst("candidate_a", Layer.ROBOTICS_FA, held=False),
            _inst("candidate_b", Layer.ROBOTICS_FA, held=False),
        ]
        pd.DataFrame([{
            "theme": "robotics_fa", "name_ja": "ロボティクス", "total": 80.0,
            "confidence_pct": 0.5,
        }]).to_csv(output_dir / "theme_scores.csv", index=False)

        results = compute_discovery_companies(
            as_of=date(2026, 7, 6), processed_dir=processed, output_dir=output_dir,
            instruments=instruments,
        )

        keys = {r.company for r in results}
        assert keys == {"candidate_a", "candidate_b"}  # heldは除外される

        by_key = {r.company: r for r in results}
        assert by_key["candidate_a"].relative_momentum is not None
        assert by_key["candidate_a"].relative_momentum > by_key["candidate_b"].relative_momentum
        assert by_key["candidate_a"].expected_value == 80.0

    def test_missing_theme_score_is_unavailable_not_fabricated(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed"
        processed.mkdir()
        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        instruments = [_inst("candidate_a", Layer.QUANTUM, held=False)]

        results = compute_discovery_companies(
            as_of=date(2026, 7, 6), processed_dir=processed, output_dir=output_dir,
            instruments=instruments,
        )
        assert results[0].expected_value is None
        assert results[0].data_quality == "unavailable"

    def test_rank_is_assigned_by_expected_value_desc(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed"
        processed.mkdir()
        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        instruments = [
            _inst("low_score", Layer.QUANTUM, held=False),
            _inst("high_score", Layer.ROBOTICS_FA, held=False),
        ]
        pd.DataFrame([
            {"theme": "quantum", "total": 40.0, "confidence_pct": 0.3},
            {"theme": "robotics_fa", "total": 90.0, "confidence_pct": 0.3},
        ]).to_csv(output_dir / "theme_scores.csv", index=False)

        results = compute_discovery_companies(
            as_of=date(2026, 7, 6), processed_dir=processed, output_dir=output_dir,
            instruments=instruments,
        )
        assert [r.company for r in results] == ["high_score", "low_score"]
        assert results[0].rank == 1
        assert results[1].rank == 2


class TestComputeDiscoveryThemes:
    def test_active_themes_are_excluded(self) -> None:
        themes = [
            Theme(key="ai_datacenter", name_ja="AI", status="active"),
            Theme(key="bio", name_ja="バイオ", status="watch"),
        ]
        results = compute_discovery_themes(as_of=date(2026, 7, 6), themes=themes)
        assert {r.theme for r in results} == {"bio"}

    def test_no_materials_conn_is_unavailable(self) -> None:
        themes = [Theme(key="bio", name_ja="バイオ", status="watch")]
        results = compute_discovery_themes(
            as_of=date(2026, 7, 6), materials_conn=None, themes=themes,
        )
        assert results[0].data_quality == "unavailable"
        assert "materials.db未取得" in results[0].materials_trend_note

    def test_keyword_match_counts_materials(self, tmp_path: Path) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE materials (published_at TEXT, title TEXT, summary TEXT)"
        )
        conn.execute(
            "INSERT INTO materials VALUES (?, ?, ?)",
            ("2026-07-01T00:00:00", "バイオ企業が新薬承認取得", ""),
        )
        conn.execute(
            "INSERT INTO materials VALUES (?, ?, ?)",
            ("2026-07-01T00:00:00", "半導体関連ニュース", ""),
        )
        conn.commit()

        themes = [Theme(key="bio", name_ja="バイオ", status="watch")]
        results = compute_discovery_themes(
            as_of=date(2026, 7, 6), materials_conn=conn, themes=themes,
        )
        assert results[0].materials_count == 1
        assert results[0].data_quality == "estimated"

    def test_unknown_watch_theme_without_keywords_is_unavailable(self) -> None:
        themes = [Theme(key="unknown_theme", name_ja="未知テーマ", status="watch")]
        results = compute_discovery_themes(
            as_of=date(2026, 7, 6), materials_conn=None, themes=themes,
        )
        assert results[0].data_quality == "unavailable"
        assert isinstance(results[0], DiscoveryTheme)
