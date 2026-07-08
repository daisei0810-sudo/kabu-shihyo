"""ISM製造業PMI手動入力ローダーのテスト。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.data_sources.ism_pmi_manual import (
    IsmPmiManualLoader,
    expected_latest_month,
    staleness_note,
)


class TestExpectedLatestMonth:
    def test_past_day5_expects_previous_month(self) -> None:
        assert expected_latest_month(date(2026, 7, 8)) == date(2026, 6, 1)

    def test_before_day5_expects_two_months_back(self) -> None:
        # 今月分の公表直後・直前は前月分すら未公表の可能性があるため保守的に前々月
        assert expected_latest_month(date(2026, 7, 3)) == date(2026, 5, 1)

    def test_year_boundary(self) -> None:
        assert expected_latest_month(date(2026, 1, 8)) == date(2025, 12, 1)


class TestStalenessNote:
    def test_up_to_date_not_flagged(self) -> None:
        note, is_stale = staleness_note(date(2026, 6, 1), today=date(2026, 7, 8))
        assert is_stale is False
        assert "最新" in note

    def test_stale_by_several_months_flagged(self) -> None:
        note, is_stale = staleness_note(date(2025, 8, 1), today=date(2026, 7, 8))
        assert is_stale is True
        assert "10ヶ月分" in note
        assert "ismworld.org" in note

    def test_exactly_expected_month_not_stale(self) -> None:
        # 期待値ちょうど(境界値)はまだ遅延とみなさない
        note, is_stale = staleness_note(date(2026, 5, 1), today=date(2026, 7, 3))
        assert is_stale is False


class TestIsmPmiManualLoader:
    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        loader = IsmPmiManualLoader(raw_dir=str(tmp_path / "raw"), processed_dir=str(tmp_path))
        results = loader.fetch(csv_path=tmp_path / "nonexistent.csv")
        assert len(results) == 1
        assert not results[0].is_ok()
        assert "存在しません" in (results[0].error or "")

    def test_empty_csv_returns_error(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ism_pmi_manual.csv"
        csv_path.write_text("month,value,updated_at,note\n", encoding="utf-8")
        loader = IsmPmiManualLoader(raw_dir=str(tmp_path / "raw"), processed_dir=str(tmp_path))
        results = loader.fetch(csv_path=csv_path)
        assert not results[0].is_ok()
        assert "データ行がありません" in (results[0].error or "")

    def test_valid_csv_loads_and_saves_parquet(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ism_pmi_manual.csv"
        csv_path.write_text(
            "month,value,updated_at,note\n"
            "2026-05,48.5,2026-07-08,\n"
            "2026-06,49.1,2026-07-08,\n",
            encoding="utf-8",
        )
        loader = IsmPmiManualLoader(raw_dir=str(tmp_path / "raw"), processed_dir=str(tmp_path))
        results = loader.fetch(csv_path=csv_path)
        assert results[0].is_ok()
        assert len(results[0].df) == 2
        assert (tmp_path / "ism_pmi_manual.parquet").exists()

    def test_malformed_row_is_skipped_not_crashed(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ism_pmi_manual.csv"
        csv_path.write_text(
            "month,value,updated_at,note\n"
            "2026-05,not_a_number,2026-07-08,\n"
            "2026-06,49.1,2026-07-08,\n",
            encoding="utf-8",
        )
        loader = IsmPmiManualLoader(raw_dir=str(tmp_path / "raw"), processed_dir=str(tmp_path))
        results = loader.fetch(csv_path=csv_path)
        assert results[0].is_ok()
        assert len(results[0].df) == 1  # 不正行はスキップ、クラッシュしない
