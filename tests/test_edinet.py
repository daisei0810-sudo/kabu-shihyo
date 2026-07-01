"""EDINET fetcher(未検証・キー未設定時のグレースフルスキップ)のテスト。

実際のAPIキーでの動作は検証していない(本セッションでは取得していないため)。
ここではキー未設定時の安全なスキップ動作と、日付解析ロジックのみを検証する。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.data_sources.edinet import (
    fetch_edinet_documents_for_date,
    fetch_edinet_for_companies,
    parse_submit_datetime,
)
from src.materials.ingest import _edinet_docs_to_drafts


class TestEdinetKeyAbsent:
    def test_fetch_documents_returns_empty_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDINET_API_KEY", raising=False)
        result = fetch_edinet_documents_for_date(date(2026, 7, 1))
        assert result == []

    def test_fetch_for_companies_returns_empty_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EDINET_API_KEY", raising=False)
        result = fetch_edinet_for_companies(["フジクラ"])
        assert result == []

    def test_fetch_for_companies_empty_aliases_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EDINET_API_KEY", "dummy-key-for-test")
        result = fetch_edinet_for_companies([])
        assert result == []


class TestParseSubmitDatetime:
    def test_none_returns_none(self) -> None:
        assert parse_submit_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_submit_datetime("") is None

    def test_space_separated_format(self) -> None:
        dt = parse_submit_datetime("2026-06-30 15:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 30
        assert dt.tzinfo is UTC

    def test_iso_format(self) -> None:
        dt = parse_submit_datetime("2026-06-30T15:00:00")
        assert dt is not None
        assert dt.day == 30

    def test_garbage_returns_none(self) -> None:
        assert parse_submit_datetime("not a date") is None


class TestEdinetDocsToDrafts:
    def test_builds_draft_with_company_hint(self) -> None:
        docs = [{
            "filerName": "フジクラ",
            "docDescription": "有価証券報告書",
            "docID": "S100XXXX",
            "formCode": "030000",
            "submitDateTime": "2026-06-30 15:00",
        }]
        drafts = _edinet_docs_to_drafts(docs, datetime(2026, 7, 1, tzinfo=UTC))
        assert len(drafts) == 1
        d = drafts[0]
        assert d.company_hint == "フジクラ"
        assert d.source_id == "edinet"
        assert d.source_rank.value == "A"
        assert "有価証券報告書" in d.title

    def test_missing_filer_name_falls_back(self) -> None:
        docs = [{"docDescription": "何かの開示", "docID": "S1"}]
        drafts = _edinet_docs_to_drafts(docs, datetime(2026, 7, 1, tzinfo=UTC))
        assert len(drafts) == 1
        assert "Unknown Filer" in drafts[0].title

    def test_empty_docs_returns_empty(self) -> None:
        assert _edinet_docs_to_drafts([], datetime(2026, 7, 1, tzinfo=UTC)) == []
