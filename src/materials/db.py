"""SQLite⇔JSONL 相互変換 + 材料/因果グラフのCRUD(§1, §15)。

正本は data/materials/*.jsonl。SQLite(data/materials.db)は実行毎に
JSONLから再構築される揮発キャッシュであり、git管理しない(.gitignore対象)。

diff安定化のため、JSONL書き出しは以下を厳守する(Opus設計ドキュメント§1準拠):
  - 主キー(material_id / node_id / edge_id)昇順ソート
  - キー順固定(sort_keys=True) + ensure_ascii=False + 決定的区切り文字
  - 1レコード = 1行(改行なし)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from pathlib import Path

from src.materials.models import CausalEdge, CausalNode, Material, Source

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_JSON_LIST_FIELDS = ("related_tickers", "affected_factors")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """スキーマ初期化済みの SQLite 接続を返す。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


# ---------------------------------------------------------------------------
# Source CRUD
# ---------------------------------------------------------------------------

def upsert_source(conn: sqlite3.Connection, s: Source) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_id, display_name, source_rank, source_type,
            domain_pattern, is_customer_official, created_at
        ) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(source_id) DO UPDATE SET
            display_name=excluded.display_name, source_rank=excluded.source_rank,
            source_type=excluded.source_type, domain_pattern=excluded.domain_pattern,
            is_customer_official=excluded.is_customer_official
        """,
        (
            s.source_id, s.display_name, s.source_rank, s.source_type,
            s.domain_pattern, int(s.is_customer_official), s.created_at,
        ),
    )


def ensure_source_exists(conn: sqlite3.Connection, s: Source) -> None:
    """source_id が未登録の場合のみ挿入する(既存の正確な登録を上書きしない安全弁)。

    RSS設定(config/rss_sources.csv)等で正式登録される前に、取込パイプラインが
    FK制約を満たすための最小限フォールバック登録に使う。
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO sources (
            source_id, display_name, source_rank, source_type,
            domain_pattern, is_customer_official, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            s.source_id, s.display_name, s.source_rank, s.source_type,
            s.domain_pattern, int(s.is_customer_official), s.created_at,
        ),
    )


def list_sources(conn: sqlite3.Connection) -> list[Source]:
    rows = conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["is_customer_official"] = bool(d["is_customer_official"])
        results.append(Source(**d))
    return results


# ---------------------------------------------------------------------------
# Material CRUD
# ---------------------------------------------------------------------------

def upsert_material(conn: sqlite3.Connection, m: Material) -> None:
    """材料を挿入または更新。material_tickers 展開テーブルも同期する。"""
    conn.execute(
        """
        INSERT INTO materials (
            material_id, title, summary, source_id, source_rank, published_at,
            first_detected_at, first_evaluated_at, first_notified_at,
            related_tickers, affected_factors, confidence_score, new_fact_flag,
            notification_status, previous_material_relation, freshness_score,
            detection_delayed, content_hash, dedup_bucket, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(material_id) DO UPDATE SET
            title=excluded.title, summary=excluded.summary,
            source_id=excluded.source_id, source_rank=excluded.source_rank,
            published_at=excluded.published_at,
            first_evaluated_at=excluded.first_evaluated_at,
            first_notified_at=excluded.first_notified_at,
            related_tickers=excluded.related_tickers,
            affected_factors=excluded.affected_factors,
            confidence_score=excluded.confidence_score,
            new_fact_flag=excluded.new_fact_flag,
            notification_status=excluded.notification_status,
            previous_material_relation=excluded.previous_material_relation,
            freshness_score=excluded.freshness_score,
            detection_delayed=excluded.detection_delayed,
            content_hash=excluded.content_hash,
            dedup_bucket=excluded.dedup_bucket,
            updated_at=excluded.updated_at
        """,
        (
            m.material_id, m.title, m.summary, m.source_id, m.source_rank,
            m.published_at, m.first_detected_at, m.first_evaluated_at,
            m.first_notified_at,
            json.dumps(m.related_tickers, ensure_ascii=False),
            json.dumps(m.affected_factors, ensure_ascii=False),
            m.confidence_score, int(m.new_fact_flag), m.notification_status,
            m.previous_material_relation, m.freshness_score,
            int(m.detection_delayed), m.content_hash, m.dedup_bucket,
            m.created_at, m.updated_at,
        ),
    )
    conn.execute("DELETE FROM material_tickers WHERE material_id = ?", (m.material_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO material_tickers (material_id, ticker_key) VALUES (?, ?)",
        [(m.material_id, tk) for tk in m.related_tickers],
    )


def _row_to_material(row: sqlite3.Row) -> Material:
    d = dict(row)
    d["related_tickers"] = json.loads(d["related_tickers"] or "[]")
    d["affected_factors"] = json.loads(d["affected_factors"] or "[]")
    d["new_fact_flag"] = bool(d["new_fact_flag"])
    d["detection_delayed"] = bool(d["detection_delayed"])
    return Material(**d)


def get_material(conn: sqlite3.Connection, material_id: str) -> Material | None:
    row = conn.execute(
        "SELECT * FROM materials WHERE material_id = ?", (material_id,)
    ).fetchone()
    return _row_to_material(row) if row else None


def list_materials(
    conn: sqlite3.Connection,
    ticker_key: str | None = None,
) -> list[Material]:
    """材料一覧。ticker_key 指定時はそれに関連する材料のみ。"""
    if ticker_key is None:
        rows = conn.execute("SELECT * FROM materials ORDER BY material_id").fetchall()
    else:
        rows = conn.execute(
            """
            SELECT m.* FROM materials m
            JOIN material_tickers mt ON mt.material_id = m.material_id
            WHERE mt.ticker_key = ?
            ORDER BY m.material_id
            """,
            (ticker_key,),
        ).fetchall()
    return [_row_to_material(r) for r in rows]


# ---------------------------------------------------------------------------
# 因果グラフ CRUD
# ---------------------------------------------------------------------------

def upsert_causal_node(conn: sqlite3.Connection, n: CausalNode) -> None:
    conn.execute(
        """
        INSERT INTO causal_graph_nodes (
            node_id, node_name, node_type, description, impact_weight,
            lag_days, ticker_key, layer, confidence_level, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(node_id) DO UPDATE SET
            node_name=excluded.node_name, node_type=excluded.node_type,
            description=excluded.description, impact_weight=excluded.impact_weight,
            lag_days=excluded.lag_days, ticker_key=excluded.ticker_key,
            layer=excluded.layer, confidence_level=excluded.confidence_level
        """,
        (
            n.node_id, n.node_name, n.node_type, n.description, n.impact_weight,
            n.lag_days, n.ticker_key, n.layer, n.confidence_level, n.created_at,
        ),
    )


def upsert_causal_edge(conn: sqlite3.Connection, e: CausalEdge) -> None:
    conn.execute(
        """
        INSERT INTO causal_graph_edges (
            edge_id, from_node, to_node, impact_weight, lag_days,
            confidence_level, edge_type, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(edge_id) DO UPDATE SET
            from_node=excluded.from_node, to_node=excluded.to_node,
            impact_weight=excluded.impact_weight, lag_days=excluded.lag_days,
            confidence_level=excluded.confidence_level, edge_type=excluded.edge_type
        """,
        (
            e.edge_id, e.from_node, e.to_node, e.impact_weight, e.lag_days,
            e.confidence_level, e.edge_type, e.created_at,
        ),
    )


def list_causal_nodes(conn: sqlite3.Connection) -> list[CausalNode]:
    rows = conn.execute("SELECT * FROM causal_graph_nodes ORDER BY node_id").fetchall()
    return [CausalNode(**dict(r)) for r in rows]


def list_causal_edges(conn: sqlite3.Connection) -> list[CausalEdge]:
    rows = conn.execute("SELECT * FROM causal_graph_edges ORDER BY edge_id").fetchall()
    return [CausalEdge(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# JSONL 相互変換(正本はJSONL側)
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list[dict], sort_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: r[sort_key])
    lines = [
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for r in ordered
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def dump_to_jsonl(conn: sqlite3.Connection, dump_dir: str | Path) -> None:
    """SQLite の内容を決定的な JSONL ファイル群へ書き出す(コミット対象)。"""
    d = Path(dump_dir)
    sources = [asdict(s) for s in list_sources(conn)]
    materials = [asdict(m) for m in list_materials(conn)]
    nodes = [asdict(n) for n in list_causal_nodes(conn)]
    edges = [asdict(e) for e in list_causal_edges(conn)]
    _write_jsonl(d / "sources.jsonl", sources, "source_id")
    _write_jsonl(d / "materials.jsonl", materials, "material_id")
    _write_jsonl(d / "causal_nodes.jsonl", nodes, "node_id")
    _write_jsonl(d / "causal_edges.jsonl", edges, "edge_id")


def rebuild_from_jsonl(conn: sqlite3.Connection, dump_dir: str | Path) -> None:
    """JSONL(正本)から SQLite を再構築する。実行の起点で必ず呼ぶ。

    sources → materials の順で投入する(materials.source_id の FK制約を満たすため)。
    """
    d = Path(dump_dir)
    source_fields = {f.name for f in fields(Source)}
    for rec in _read_jsonl(d / "sources.jsonl"):
        upsert_source(conn, Source(**{k: v for k, v in rec.items() if k in source_fields}))

    material_fields = {f.name for f in fields(Material)}
    for rec in _read_jsonl(d / "materials.jsonl"):
        upsert_material(conn, Material(**{k: v for k, v in rec.items() if k in material_fields}))

    node_fields = {f.name for f in fields(CausalNode)}
    for rec in _read_jsonl(d / "causal_nodes.jsonl"):
        upsert_causal_node(conn, CausalNode(**{k: v for k, v in rec.items() if k in node_fields}))

    edge_fields = {f.name for f in fields(CausalEdge)}
    for rec in _read_jsonl(d / "causal_edges.jsonl"):
        upsert_causal_edge(conn, CausalEdge(**{k: v for k, v in rec.items() if k in edge_fields}))
    conn.commit()


def verify_roundtrip(conn: sqlite3.Connection, dump_dir: str | Path) -> bool:
    """dump→rebuild のラウンドトリップで material_id 集合が一致するか検証する。"""
    before = {m.material_id for m in list_materials(conn)}
    dump_to_jsonl(conn, dump_dir)

    check_conn = sqlite3.connect(":memory:")
    check_conn.row_factory = sqlite3.Row
    check_conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    rebuild_from_jsonl(check_conn, dump_dir)
    after = {m.material_id for m in list_materials(check_conn)}
    check_conn.close()

    return before == after
