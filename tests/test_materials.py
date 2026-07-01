"""src/materials/ のテスト(Phase5: 材料DB基盤)。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from src.materials.dedup import (
    classify_relation,
    content_hash,
    dedup_bucket,
    detect_duplicate_material,
    normalize_title,
    should_renotify,
)
from src.materials.freshness import (
    compute_freshness_score,
    is_detection_delayed,
    is_notification_allowed,
)
from src.materials.material_id import generate_material_id
from src.materials.models import CausalEdge, CausalNode, Material, MaterialDraft
from src.materials.source_rank import assign_source_rank, can_affect_decision, infer_source_type
from src.materials.taxonomy import FreshnessScore, MaterialRelation, SourceRank, SourceType

# ---------------------------------------------------------------------------
# material_id
# ---------------------------------------------------------------------------


class TestMaterialId:
    def test_deterministic(self) -> None:
        title = "HBM long-term agreement signed"
        id1 = generate_material_id("Micron", title, date(2026, 6, 24), set())
        id2 = generate_material_id("Micron", title, date(2026, 6, 24), set())
        assert id1 == id2

    def test_expected_pattern(self) -> None:
        mid = generate_material_id("Micron", "HBM long-term agreement", date(2026, 6, 24), set())
        assert mid == "MICRON_HBM_LTA_20260624"

    def test_collision_suffix(self) -> None:
        existing = {"MICRON_HBM_LTA_20260624"}
        mid = generate_material_id("Micron", "HBM long-term agreement", date(2026, 6, 24), existing)
        assert mid == "MICRON_HBM_LTA_20260624_2"

    def test_unknown_company_fallback(self) -> None:
        mid = generate_material_id(
            "Some Random Corp", "capex announcement", date(2026, 1, 1), set()
        )
        assert mid.startswith("SOMERANDOMCORP") or "_CAPEX_20260101" in mid

    def test_config_instrument_seeds_alias(self) -> None:
        # config.INSTRUMENTS の フジクラ が自動シードされていること
        mid = generate_material_id("フジクラ", "capex increase", date(2026, 1, 1), set())
        assert mid.startswith("FUJIKURA_")


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_normalize_title_strips_punctuation(self) -> None:
        assert normalize_title("Micron: HBM Deal!!") == "micron hbm deal"

    def test_content_hash_same_for_normalized_equal(self) -> None:
        assert content_hash("Micron HBM Deal") == content_hash("micron, hbm deal!")

    def test_dedup_bucket_same_week(self) -> None:
        b1 = dedup_bucket("MICRON", "HBM", date(2026, 6, 24))
        b2 = dedup_bucket("MICRON", "HBM", date(2026, 6, 26))
        assert b1 == b2

    def test_exact_duplicate_detected(self) -> None:
        existing = [_make_material("MICRON_HBM_20260624", "Micron HBM deal", "MICRON|HBM|202626")]
        draft = MaterialDraft(
            title="Micron HBM deal",
            summary="",
            source_id="reuters",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 6, 24, tzinfo=UTC),
            detected_at=datetime(2026, 6, 24, tzinfo=UTC),
        )
        match = detect_duplicate_material(draft, "MICRON|HBM|202626", existing)
        assert match is not None
        assert match.relation == MaterialRelation.DUPLICATE
        assert match.similarity == 1.0

    def test_no_match_different_bucket(self) -> None:
        # STAGE0(完全一致)は全件対象なので、タイトルも変えてバケット絞り込みを検証する
        existing = [_make_material("MICRON_HBM_20260624", "Micron HBM deal", "MICRON|HBM|202626")]
        draft = MaterialDraft(
            title="Micron announces new fab investment",
            summary="",
            source_id="reuters",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 7, 24, tzinfo=UTC),
            detected_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
        match = detect_duplicate_material(draft, "MICRON|FAB|202630", existing)
        assert match is None

    def test_update_relation_on_new_fact_keyword(self) -> None:
        old = _make_material("MICRON_HBM_20260624", "Micron HBM deal talks", "MICRON|HBM|202626")
        draft = MaterialDraft(
            title="Micron HBM deal talks",
            summary="契約金額が確定した",
            source_id="reuters",
            source_rank=SourceRank.B,
            published_at=datetime(2026, 6, 25, tzinfo=UTC),
            detected_at=datetime(2026, 6, 25, tzinfo=UTC),
        )
        relation = classify_relation(draft, old)
        assert relation == MaterialRelation.UPDATE

    def test_should_renotify_update_allowed(self) -> None:
        from src.materials.models import MaterialMatch

        match = MaterialMatch("X", MaterialRelation.UPDATE, 0.9, "")
        assert should_renotify(match) is True

    def test_should_renotify_duplicate_denied(self) -> None:
        from src.materials.models import MaterialMatch

        match = MaterialMatch("X", MaterialRelation.DUPLICATE, 1.0, "")
        assert should_renotify(match) is False


def _seed_source(conn: sqlite3.Connection, source_id: str = "reuters") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, display_name, source_rank, source_type, is_customer_official, created_at) "
        "VALUES (?, ?, 'B', 'wire', 0, '2026-01-01T00:00:00Z')",
        (source_id, source_id),
    )


def _make_material(material_id: str, title: str, bucket: str) -> Material:
    return Material(
        material_id=material_id,
        title=title,
        summary="",
        source_id="reuters",
        source_rank="B",
        published_at="2026-06-24T00:00:00Z",
        first_detected_at="2026-06-24T00:00:00Z",
        content_hash=content_hash(title),
        dedup_bucket=bucket,
        created_at="2026-06-24T00:00:00Z",
        updated_at="2026-06-24T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


class TestFreshness:
    def test_within_24h_is_s(self) -> None:
        pub = datetime(2026, 6, 24, 0, 0, tzinfo=UTC)
        det = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)
        assert compute_freshness_score(pub, det) == FreshnessScore.S

    def test_within_3days_is_a(self) -> None:
        pub = datetime(2026, 6, 24, 0, 0, tzinfo=UTC)
        det = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)
        assert compute_freshness_score(pub, det) == FreshnessScore.A

    def test_within_7days_is_b(self) -> None:
        pub = datetime(2026, 6, 24, 0, 0, tzinfo=UTC)
        det = datetime(2026, 6, 30, 0, 0, tzinfo=UTC)
        assert compute_freshness_score(pub, det) == FreshnessScore.B

    def test_over_7days_is_c(self) -> None:
        pub = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        det = datetime(2026, 6, 30, 0, 0, tzinfo=UTC)
        assert compute_freshness_score(pub, det) == FreshnessScore.C

    def test_unknown_published_at_is_c(self) -> None:
        det = datetime(2026, 6, 30, 0, 0, tzinfo=UTC)
        assert compute_freshness_score(None, det) == FreshnessScore.C

    def test_detection_delayed_flag(self) -> None:
        pub = datetime(2026, 6, 24, 0, 0, tzinfo=UTC)
        det = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
        assert is_detection_delayed(pub, det) is True

    def test_not_delayed_within_24h(self) -> None:
        pub = datetime(2026, 6, 24, 0, 0, tzinfo=UTC)
        det = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
        assert is_detection_delayed(pub, det) is False

    def test_stale_without_new_fact_blocked(self) -> None:
        allowed, _ = is_notification_allowed(FreshnessScore.C, new_fact_flag=False)
        assert allowed is False

    def test_stale_with_new_fact_allowed(self) -> None:
        allowed, _ = is_notification_allowed(FreshnessScore.C, new_fact_flag=True)
        assert allowed is True

    def test_fresh_always_allowed(self) -> None:
        allowed, _ = is_notification_allowed(FreshnessScore.S, new_fact_flag=False)
        assert allowed is True


# ---------------------------------------------------------------------------
# source_rank
# ---------------------------------------------------------------------------


class TestSourceRank:
    def test_company_ir_is_rank_a(self) -> None:
        assert assign_source_rank(SourceType.COMPANY_IR) == SourceRank.A

    def test_wire_is_rank_b(self) -> None:
        assert assign_source_rank(SourceType.WIRE) == SourceRank.B

    def test_trade_press_is_rank_c(self) -> None:
        assert assign_source_rank(SourceType.TRADE_PRESS) == SourceRank.C

    def test_sns_is_rank_d(self) -> None:
        assert assign_source_rank(SourceType.SNS) == SourceRank.D

    def test_customer_official_overrides_to_a(self) -> None:
        assert assign_source_rank(SourceType.SNS, is_customer_official=True) == SourceRank.A

    def test_infer_source_type_reuters(self) -> None:
        assert infer_source_type("www.reuters.com") == SourceType.WIRE

    def test_infer_source_type_unknown(self) -> None:
        assert infer_source_type("some-random-blog.example") == SourceType.UNKNOWN

    def test_rank_d_cannot_affect_decision(self) -> None:
        assert can_affect_decision(SourceRank.D) is False

    def test_rank_c_alone_cannot_affect_decision(self) -> None:
        assert can_affect_decision(SourceRank.C) is False

    def test_rank_c_with_b_corroboration_can_affect(self) -> None:
        assert can_affect_decision(SourceRank.C, corroborating_ranks=[SourceRank.B]) is True

    def test_rank_c_with_customer_official_can_affect(self) -> None:
        assert can_affect_decision(SourceRank.C, is_customer_official=True) is True

    def test_rank_a_alone_can_affect(self) -> None:
        assert can_affect_decision(SourceRank.A) is True


# ---------------------------------------------------------------------------
# db (SQLite <-> JSONL roundtrip)
# ---------------------------------------------------------------------------


class TestDb:
    def test_upsert_and_get_material(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection, get_material, upsert_material

        conn = get_connection(tmp_path / "test.db")
        _seed_source(conn)
        m = _make_material("TEST_ID_20260101", "Test title", "TEST|X|202601")
        upsert_material(conn, m)
        fetched = get_material(conn, "TEST_ID_20260101")
        assert fetched is not None
        assert fetched.title == "Test title"

    def test_material_tickers_expansion(self, tmp_path: Path) -> None:
        from src.materials.db import get_connection, list_materials, upsert_material

        conn = get_connection(tmp_path / "test.db")
        _seed_source(conn)
        m = _make_material("TEST_ID_20260101", "Test title", "TEST|X|202601")
        m.related_tickers = ["fujikura", "kioxia"]
        upsert_material(conn, m)
        results = list_materials(conn, ticker_key="fujikura")
        assert len(results) == 1
        assert results[0].material_id == "TEST_ID_20260101"

    def test_dump_and_rebuild_roundtrip(self, tmp_path: Path) -> None:
        from src.materials.db import (
            dump_to_jsonl,
            get_connection,
            list_materials,
            rebuild_from_jsonl,
            upsert_material,
        )

        conn = get_connection(tmp_path / "test.db")
        _seed_source(conn)
        upsert_material(conn, _make_material("A_ID_20260101", "A title", "A|X|202601"))
        upsert_material(conn, _make_material("B_ID_20260102", "B title", "B|X|202601"))
        dump_dir = tmp_path / "dump"
        dump_to_jsonl(conn, dump_dir)

        conn2 = get_connection(tmp_path / "test2.db")
        rebuild_from_jsonl(conn2, dump_dir)
        ids = {m.material_id for m in list_materials(conn2)}
        assert ids == {"A_ID_20260101", "B_ID_20260102"}

    def test_jsonl_output_is_sorted(self, tmp_path: Path) -> None:
        from src.materials.db import dump_to_jsonl, get_connection, upsert_material

        conn = get_connection(tmp_path / "test.db")
        _seed_source(conn)
        upsert_material(conn, _make_material("Z_ID_20260101", "Z title", "Z|X|202601"))
        upsert_material(conn, _make_material("A_ID_20260101", "A title", "A|X|202601"))
        dump_dir = tmp_path / "dump"
        dump_to_jsonl(conn, dump_dir)

        lines = (dump_dir / "materials.jsonl").read_text(encoding="utf-8").splitlines()
        ids_in_file = [line.split('"material_id":"')[1].split('"')[0] for line in lines]
        assert ids_in_file == sorted(ids_in_file)

    def test_causal_node_and_edge_upsert(self, tmp_path: Path) -> None:
        from src.materials.db import (
            get_connection,
            list_causal_edges,
            list_causal_nodes,
            upsert_causal_edge,
            upsert_causal_node,
        )

        conn = get_connection(tmp_path / "test.db")
        node_a = CausalNode(
            node_id="NODE_A", node_name="A", node_type="company", created_at="now"
        )
        node_b = CausalNode(
            node_id="NODE_B", node_name="B", node_type="company", created_at="now"
        )
        upsert_causal_node(conn, node_a)
        upsert_causal_node(conn, node_b)
        edge = CausalEdge(
            edge_id="EDGE_A__B", from_node="NODE_A", to_node="NODE_B", created_at="now"
        )
        upsert_causal_edge(conn, edge)

        assert len(list_causal_nodes(conn)) == 2
        assert len(list_causal_edges(conn)) == 1


# ---------------------------------------------------------------------------
# causal_graph
# ---------------------------------------------------------------------------


class TestCausalGraph:
    def test_downstream_tickers_traversal(self, tmp_path: Path) -> None:
        from src.materials.causal_graph import (
            add_edge,
            downstream_tickers,
            make_node_id,
            make_ticker_node_id,
        )
        from src.materials.db import get_connection, upsert_causal_node

        conn = get_connection(tmp_path / "test.db")
        upsert_causal_node(conn, CausalNode(
            node_id=make_node_id("OpenAI"), node_name="OpenAI",
            node_type="company", created_at="now",
        ))
        upsert_causal_node(conn, CausalNode(
            node_id=make_ticker_node_id("fujikura"), node_name="フジクラ",
            node_type="ticker", ticker_key="fujikura", created_at="now",
        ))
        add_edge(conn, make_node_id("OpenAI"), make_ticker_node_id("fujikura"), lag_days=90)

        results = downstream_tickers(conn, make_node_id("OpenAI"))
        assert ("fujikura", 1) in results

    def test_downstream_tickers_no_infinite_loop_on_cycle(self, tmp_path: Path) -> None:
        from src.materials.causal_graph import add_edge, downstream_tickers
        from src.materials.db import get_connection, upsert_causal_node

        conn = get_connection(tmp_path / "test.db")
        upsert_causal_node(
            conn, CausalNode(node_id="NODE_A", node_name="A", node_type="company", created_at="now")
        )
        upsert_causal_node(
            conn, CausalNode(node_id="NODE_B", node_name="B", node_type="company", created_at="now")
        )
        add_edge(conn, "NODE_A", "NODE_B")
        add_edge(conn, "NODE_B", "NODE_A")  # 循環

        # ティッカーノードが無いので結果は空。かつ無限ループしないことが本質的な検証点。
        results = downstream_tickers(conn, "NODE_A", max_depth=10)
        assert results == []
