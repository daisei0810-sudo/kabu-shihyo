"""daily_report.py(公開レポート)のテスト。"""

from __future__ import annotations

from pathlib import Path

from src.reporting.daily_report import _section_manual_data_freshness


class TestSectionManualDataFreshness:
    def test_missing_file_shows_placeholder(self, tmp_path: Path) -> None:
        lines = _section_manual_data_freshness(tmp_path / "nonexistent.csv")
        assert any("未作成" in line for line in lines)

    def test_empty_csv_shows_placeholder(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ism_pmi_manual.csv"
        csv_path.write_text("month,value,updated_at,note\n", encoding="utf-8")
        lines = _section_manual_data_freshness(csv_path)
        assert any("データ行なし" in line for line in lines)

    def test_renders_staleness_note(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "ism_pmi_manual.csv"
        csv_path.write_text(
            "month,value,updated_at,note\n2020-01,50.0,2026-01-01,\n", encoding="utf-8",
        )
        lines = _section_manual_data_freshness(csv_path)
        joined = "\n".join(lines)
        assert "ISM製造業PMI" in joined
        assert "更新遅延" in joined  # 2020-01は現在から見て確実に遅延している
