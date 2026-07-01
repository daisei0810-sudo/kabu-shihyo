"""因果グラフのノード/エッジ CRUD + トラバーサル雛形(§12)。

このPhaseではテーブル構造と基本操作のみを提供する。実データ(指示書§12の
「OpenAI→Stargate→NVIDIA→TSMC→CoWoS→HBM→光通信→フジクラ」等の投入)は、
Phase6(ニュース・材料監視)完了後に別途投入する。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.config import INSTRUMENTS
from src.materials.models import CausalEdge, CausalNode


def make_node_id(name: str) -> str:
    """ノード名からノードIDを決定的に生成する(例: 'OpenAI' -> 'NODE_OPENAI')。"""
    token = "".join(ch for ch in name.upper() if ch.isalnum())
    return f"NODE_{token}" if token else "NODE_UNKNOWN"


def make_ticker_node_id(ticker_key: str) -> str:
    """Instrument.key からティッカーノードIDを生成する。"""
    return f"NODE_TICKER_{ticker_key.upper()}"


def make_edge_id(from_node: str, to_node: str) -> str:
    return f"EDGE_{from_node}__{to_node}"


def seed_ticker_nodes(conn: sqlite3.Connection) -> list[CausalNode]:
    """config.INSTRUMENTS の保有銘柄をティッカーノードとして登録する。"""
    from src.materials.db import upsert_causal_node

    now = datetime.now(UTC).isoformat()
    nodes: list[CausalNode] = []
    for inst in INSTRUMENTS:
        if not inst.held:
            continue
        node = CausalNode(
            node_id=make_ticker_node_id(inst.key),
            node_name=inst.name_ja,
            node_type="ticker",
            ticker_key=inst.key,
            layer=inst.layer.value,
            created_at=now,
        )
        upsert_causal_node(conn, node)
        nodes.append(node)
    return nodes


def add_edge(
    conn: sqlite3.Connection,
    from_node: str,
    to_node: str,
    impact_weight: float = 1.0,
    lag_days: int | None = None,
    confidence_level: str | None = None,
    edge_type: str | None = None,
) -> CausalEdge:
    """2ノード間にエッジを追加する。"""
    from src.materials.db import upsert_causal_edge

    edge = CausalEdge(
        edge_id=make_edge_id(from_node, to_node),
        from_node=from_node,
        to_node=to_node,
        impact_weight=impact_weight,
        lag_days=lag_days,
        confidence_level=confidence_level,
        edge_type=edge_type,
        created_at=datetime.now(UTC).isoformat(),
    )
    upsert_causal_edge(conn, edge)
    return edge


def downstream_tickers(
    conn: sqlite3.Connection,
    start_node_id: str,
    max_depth: int = 6,
) -> list[tuple[str, int]]:
    """start_node_id から到達可能な ticker ノードを (ticker_key, 経路長) で列挙する。

    単純な幅優先探索。閉路があっても max_depth で打ち切るため無限ループしない。
    """
    from src.materials.db import list_causal_edges, list_causal_nodes

    nodes_by_id = {n.node_id: n for n in list_causal_nodes(conn)}
    edges = list_causal_edges(conn)
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.from_node, []).append(e.to_node)

    visited: dict[str, int] = {start_node_id: 0}
    queue: list[tuple[str, int]] = [(start_node_id, 0)]
    results: list[tuple[str, int]] = []

    while queue:
        node_id, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for neighbor in adjacency.get(node_id, []):
            if neighbor in visited:
                continue
            visited[neighbor] = depth + 1
            queue.append((neighbor, depth + 1))
            neighbor_node = nodes_by_id.get(neighbor)
            if neighbor_node and neighbor_node.node_type == "ticker" and neighbor_node.ticker_key:
                results.append((neighbor_node.ticker_key, depth + 1))

    return results
