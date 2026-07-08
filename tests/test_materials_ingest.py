"""Phase6(材料取込パイプライン)のテスト。ネットワークアクセスは行わない。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.data_sources.rss_fetcher import RssSourceConfig, _parse_rss_or_atom, load_rss_sources
from src.materials.ingest import (
    _edgar_hits_to_drafts,
    _parse_rss_date,
    _rss_entries_to_drafts,
    ingest_draft,
    run_ingest,
)
from src.materials.manual_input import read_manual_materials
from src.materials.models import MaterialDraft
from src.materials.taxonomy import NotificationStatus, SourceRank

_RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Example Feed</title>
<item>
  <title>Micron announces HBM long-term agreement</title>
  <link>https://example.com/1</link>
  <description>Details of the deal</description>
  <pubDate>Wed, 24 Jun 2026 09:00:00 GMT</pubDate>
  <guid>guid-1</guid>
</item>
</channel></rss>
"""

_ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <title>TSMC CoWoS capacity expansion</title>
  <link href="https://example.com/2"/>
  <summary>Expansion details</summary>
  <updated>2026-06-24T09:00:00Z</updated>
  <id>atom-1</id>
</entry>
</feed>
"""


class TestRssParsing:
    def test_parses_rss_item(self) -> None:
        items = _parse_rss_or_atom(_RSS_SAMPLE)
        assert len(items) == 1
        assert items[0]["title"] == "Micron announces HBM long-term agreement"
        assert items[0]["published"] == "Wed, 24 Jun 2026 09:00:00 GMT"

    def test_parses_atom_entry(self) -> None:
        items = _parse_rss_or_atom(_ATOM_SAMPLE)
        assert len(items) == 1
        assert items[0]["title"] == "TSMC CoWoS capacity expansion"

    def test_malformed_xml_returns_empty(self) -> None:
        assert _parse_rss_or_atom("<not valid xml") == []

    def test_empty_config_returns_empty_list(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.csv"
        assert load_rss_sources(str(missing)) == []

    def test_load_rss_sources_from_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "sources.csv"
        csv_path.write_text(
            "source_id,url,source_type,display_name,is_customer_official\n"
            "test_src,https://example.com/rss,gov,Test Source,false\n",
            encoding="utf-8",
        )
        sources = load_rss_sources(str(csv_path))
        assert len(sources) == 1
        assert sources[0].source_id == "test_src"
        assert sources[0].source_type == "gov"


class TestDraftBuilders:
    def test_parse_rss_date_rfc822(self) -> None:
        dt = _parse_rss_date("Wed, 24 Jun 2026 09:00:00 GMT")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 24

    def test_parse_rss_date_iso8601(self) -> None:
        dt = _parse_rss_date("2026-06-24T09:00:00Z")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_rss_date_invalid_returns_none(self) -> None:
        assert _parse_rss_date("not a date") is None

    def test_parse_rss_date_empty_returns_none(self) -> None:
        assert _parse_rss_date("") is None

    def test_edgar_hits_to_drafts(self) -> None:
        hits = [{
            "title": "Micron Technology Inc",
            "form_type": "8-K",
            "filed_at": "2026-06-24",
            "accession_no": "0001-26-000123",
            "query_company": "Micron",
        }]
        drafts = _edgar_hits_to_drafts(hits, datetime(2026, 6, 25, tzinfo=UTC))
        assert len(drafts) == 1
        assert drafts[0].source_rank == SourceRank.A
        assert "8-K" in drafts[0].title

    def test_edgar_company_hint_uses_filer_not_query(self) -> None:
        # 回帰テスト: EDGAR全文検索は「クエリ企業名を本文中に含む任意の開示」を
        # 返すため、company_hint は実際の提出者(title)であり検索queryではないこと。
        hits = [{
            "title": "LiveOne, Inc.  (LVO)  (CIK 0001491419)",
            "form_type": "10-K",
            "filed_at": "2026-06-29",
            "accession_no": "0001437749-26-021987",
            "query_company": "Tesla",
        }]
        drafts = _edgar_hits_to_drafts(hits, datetime(2026, 6, 29, tzinfo=UTC))
        assert drafts[0].company_hint is not None
        assert "LiveOne" in drafts[0].company_hint
        assert drafts[0].company_hint != "Tesla"

    def test_rss_entries_to_drafts_customer_official(self) -> None:
        source = RssSourceConfig(
            source_id="test_ir", url="https://example.com/rss",
            source_type="company_ir", display_name="Test IR",
            is_customer_official=True,
        )
        entries = [{"title": "Guidance update", "summary": "", "published": ""}]
        drafts = _rss_entries_to_drafts(source, entries, datetime(2026, 6, 25, tzinfo=UTC))
        assert len(drafts) == 1
        assert drafts[0].source_rank == SourceRank.A
        assert drafts[0].company_hint == "Test IR"

    def test_rss_wire_source_has_no_company_hint(self) -> None:
        # wire/gov等の複数企業横断フィードでは company_hint を推測しない
        source = RssSourceConfig(
            source_id="reuters_feed", url="https://example.com/rss",
            source_type="wire", display_name="Reuters",
        )
        entries = [{"title": "Some market news", "summary": "", "published": ""}]
        drafts = _rss_entries_to_drafts(source, entries, datetime(2026, 6, 25, tzinfo=UTC))
        assert drafts[0].company_hint is None

    def test_rss_entries_skip_empty_title(self) -> None:
        source = RssSourceConfig(
            source_id="x", url="https://example.com", source_type="wire",
            display_name="X",
        )
        entries = [{"title": "", "summary": "x", "published": ""}]
        drafts = _rss_entries_to_drafts(source, entries, datetime(2026, 6, 25, tzinfo=UTC))
        assert drafts == []


class TestManualInput:
    def test_reads_pending_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pending.csv"
        csv_path.write_text(
            "title,summary,source_type,display_name,is_customer_official,"
            "published_at,related_tickers,affected_factors\n"
            "Reuters: Micron HBM deal,details here,wire,Reuters,false,"
            "2026-06-24T09:00:00Z,kioxia;fujikura,hbm_price\n",
            encoding="utf-8",
        )
        drafts = read_manual_materials(str(csv_path), detected_at=datetime(2026, 6, 25, tzinfo=UTC))
        assert len(drafts) == 1
        d = drafts[0]
        assert d.source_rank == SourceRank.B
        assert d.related_tickers == ["kioxia", "fujikura"]
        assert d.affected_factors == ["hbm_price"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_manual_materials(str(tmp_path / "nope.csv")) == []

    def test_blank_title_skipped(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pending.csv"
        csv_path.write_text(
            "title,summary,source_type,display_name,is_customer_official,"
            "published_at,related_tickers,affected_factors\n"
            ",empty title row,wire,X,false,,,\n",
            encoding="utf-8",
        )
        assert read_manual_materials(str(csv_path)) == []

    def test_customer_official_forces_rank_a(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pending.csv"
        csv_path.write_text(
            "title,summary,source_type,display_name,is_customer_official,"
            "published_at,related_tickers,affected_factors\n"
            "Customer confirms order,,sns,Customer Co,true,,,\n",
            encoding="utf-8",
        )
        drafts = read_manual_materials(str(csv_path))
        assert drafts[0].source_rank == SourceRank.A


class TestIngestDraft:
    def test_new_material_gets_registered(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft = MaterialDraft(
            title="Micron announces HBM long-term agreement",
            summary="",
            source_id="reuters",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 6, 24, tzinfo=UTC),
            detected_at=datetime(2026, 6, 24, 1, 0, tzinfo=UTC),
        )
        m = ingest_draft(conn, draft)
        assert m is not None
        assert m.material_id.startswith("MICRON_")
        assert m.notification_status == NotificationStatus.PENDING.value

    def test_company_hint_overrides_misleading_body_text(self, tmp_path: Path) -> None:
        # 回帰テスト: 本文に別企業名(Tesla)が偶然含まれていても、
        # company_hint(実際の提出者)が優先されmaterial_idが誤帰属しないこと。
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft = MaterialDraft(
            title="LiveOne, Inc. 10-K",
            summary="matched_query=Tesla",
            source_id="sec_edgar",
            source_rank=SourceRank.A,
            published_at=datetime(2026, 6, 29, tzinfo=UTC),
            detected_at=datetime(2026, 6, 29, 1, 0, tzinfo=UTC),
            company_hint="LiveOne, Inc.",
        )
        m = ingest_draft(conn, draft)
        assert m is not None
        assert "TESLA" not in m.material_id
        assert "LIVEONE" in m.material_id

    def test_exact_duplicate_is_suppressed(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft1 = MaterialDraft(
            title="Micron announces HBM long-term agreement",
            summary="",
            source_id="reuters",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 6, 24, tzinfo=UTC),
            detected_at=datetime(2026, 6, 24, 1, 0, tzinfo=UTC),
        )
        first = ingest_draft(conn, draft1)
        assert first is not None

        draft2 = MaterialDraft(
            title="Micron announces HBM long-term agreement",
            summary="",
            source_id="another_wire",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 6, 24, tzinfo=UTC),
            detected_at=datetime(2026, 6, 24, 2, 0, tzinfo=UTC),
        )
        second = ingest_draft(conn, draft2)
        assert second is None  # 重複として抑制される

    def test_stale_material_without_new_fact_suppressed(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft = MaterialDraft(
            title="Old news about capex",
            summary="",
            source_id="reuters",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
            detected_at=datetime(2026, 6, 20, tzinfo=UTC),  # 19日後 = 鮮度C
        )
        m = ingest_draft(conn, draft)
        assert m is not None
        assert m.notification_status == NotificationStatus.SUPPRESSED.value


class TestRelatedTickersAutoLink:
    """related_tickers自動紐付け(EDINET/EDGAR/RSS取込時にinstruments.csvの
    keyを自動判定する)の回帰テスト。手動入力を上書きしないこと、追跡対象外の
    企業を誤って紐付けないことの両方を確認する。"""

    def test_edinet_filer_name_resolves_to_instrument_key(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft = MaterialDraft(
            title="臨時報告書",
            summary="",
            source_id="edinet",
            source_rank=SourceRank.A,
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            detected_at=datetime(2026, 7, 1, 1, 0, tzinfo=UTC),
            company_hint="株式会社フジクラ",
        )
        m = ingest_draft(conn, draft)
        assert m is not None
        assert m.related_tickers == ["fujikura"]

    def test_untracked_company_is_not_fabricated(self, tmp_path: Path) -> None:
        # LiveOne, Inc.はinstruments.csvに存在しないため、related_tickersは
        # 空のままであるべき(追跡対象外企業を誤って紐付けない)。
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft = MaterialDraft(
            title="LiveOne, Inc. 10-K",
            summary="matched_query=Tesla",
            source_id="sec_edgar",
            source_rank=SourceRank.A,
            published_at=datetime(2026, 6, 29, tzinfo=UTC),
            detected_at=datetime(2026, 6, 29, 1, 0, tzinfo=UTC),
            company_hint="LiveOne, Inc.",
        )
        m = ingest_draft(conn, draft)
        assert m is not None
        assert m.related_tickers == []

    def test_manual_related_tickers_are_not_overridden(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection

        conn = get_connection(tmp_path / "test.db")
        draft = MaterialDraft(
            title="村田製作所と共同開発",
            summary="",
            source_id="manual",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            detected_at=datetime(2026, 7, 1, 1, 0, tzinfo=UTC),
            company_hint="フジクラ",
            related_tickers=["murata"],  # 人間が明示指定(自動判定と食い違う想定)
        )
        m = ingest_draft(conn, draft)
        assert m is not None
        assert m.related_tickers == ["murata"]


class TestRunIngest:
    def test_run_ingest_with_manual_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manual_csv = tmp_path / "pending.csv"
        manual_csv.write_text(
            "title,summary,source_type,display_name,is_customer_official,"
            "published_at,related_tickers,affected_factors\n"
            "Reuters: Fujikura capex increase,,wire,Reuters,false,,,\n",
            encoding="utf-8",
        )

        def _read_manual(detected_at: datetime | None = None) -> list[MaterialDraft]:
            return read_manual_materials(str(manual_csv), detected_at=detected_at)

        monkeypatch.setattr("src.materials.ingest.read_manual_materials", _read_manual)
        monkeypatch.setattr(
            "src.materials.ingest.fetch_all_configured_sources", lambda: []
        )

        counts = run_ingest(
            db_path=str(tmp_path / "test.db"),
            dump_dir=str(tmp_path / "dump"),
            company_queries=None,
        )
        assert counts["manual"] == 1
        assert counts["sec_edgar"] == 0
        assert (tmp_path / "dump" / "materials.jsonl").exists()
