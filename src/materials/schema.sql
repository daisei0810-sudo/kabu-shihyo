-- ============================================================
-- Phase5 材料基盤スキーマ (SQLite)
-- 正本は data/materials/*.jsonl。このDBは実行毎にJSONLから
-- 再構築される揮発キャッシュ(gitignore対象、コミットしない)。
-- ============================================================
PRAGMA foreign_keys = ON;

-- ---------- ソースマスタ(§8) ----------
CREATE TABLE IF NOT EXISTS sources (
    source_id             TEXT PRIMARY KEY,
    display_name          TEXT NOT NULL,
    source_rank           TEXT NOT NULL,
    source_type           TEXT NOT NULL,
    domain_pattern         TEXT,
    is_customer_official   INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    CHECK (source_rank IN ('A','B','C','D'))
);

-- ---------- 材料本体(§6) ----------
CREATE TABLE IF NOT EXISTS materials (
    material_id                 TEXT PRIMARY KEY,
    title                       TEXT NOT NULL,
    summary                     TEXT,
    source_id                   TEXT,
    source_rank                 TEXT NOT NULL,
    published_at                TEXT,
    first_detected_at           TEXT NOT NULL,
    first_evaluated_at          TEXT,
    first_notified_at           TEXT,
    related_tickers             TEXT NOT NULL DEFAULT '[]',
    affected_factors            TEXT NOT NULL DEFAULT '[]',
    confidence_score            REAL,
    new_fact_flag               INTEGER NOT NULL DEFAULT 1,
    notification_status         TEXT NOT NULL DEFAULT 'pending',
    previous_material_relation  TEXT,
    freshness_score             TEXT,
    detection_delayed           INTEGER NOT NULL DEFAULT 0,
    content_hash                TEXT,
    dedup_bucket                 TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    CHECK (source_rank IN ('A','B','C','D')),
    CHECK (notification_status IN ('pending','notified','suppressed','renotified')),
    CHECK (freshness_score IS NULL OR freshness_score IN ('S','A','B','C'))
);
CREATE INDEX IF NOT EXISTS idx_materials_published   ON materials(published_at);
CREATE INDEX IF NOT EXISTS idx_materials_detected    ON materials(first_detected_at);
CREATE INDEX IF NOT EXISTS idx_materials_status      ON materials(notification_status);
CREATE INDEX IF NOT EXISTS idx_materials_dedupbucket ON materials(dedup_bucket);
CREATE INDEX IF NOT EXISTS idx_materials_contenthash ON materials(content_hash);

-- 材料↔ティッカーの正規化リンク(JOIN用の展開テーブル。正本はmaterials.related_tickersのJSON)
CREATE TABLE IF NOT EXISTS material_tickers (
    material_id TEXT NOT NULL,
    ticker_key  TEXT NOT NULL,
    PRIMARY KEY (material_id, ticker_key),
    FOREIGN KEY (material_id) REFERENCES materials(material_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mattick_ticker ON material_tickers(ticker_key);

-- ---------- 因果グラフ ノード(§12) ----------
CREATE TABLE IF NOT EXISTS causal_graph_nodes (
    node_id           TEXT PRIMARY KEY,
    node_name         TEXT NOT NULL,
    node_type         TEXT NOT NULL,
    description        TEXT,
    impact_weight     REAL DEFAULT 1.0,
    lag_days          INTEGER,
    ticker_key        TEXT,
    layer             TEXT,
    confidence_level  TEXT,
    created_at        TEXT NOT NULL,
    CHECK (confidence_level IS NULL OR confidence_level IN ('high','medium','low'))
);
CREATE INDEX IF NOT EXISTS idx_nodes_type   ON causal_graph_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_ticker ON causal_graph_nodes(ticker_key);

-- ---------- 因果グラフ エッジ(§12) ----------
CREATE TABLE IF NOT EXISTS causal_graph_edges (
    edge_id           TEXT PRIMARY KEY,
    from_node         TEXT NOT NULL,
    to_node           TEXT NOT NULL,
    impact_weight     REAL NOT NULL DEFAULT 1.0,
    lag_days          INTEGER,
    confidence_level  TEXT,
    edge_type         TEXT,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (from_node) REFERENCES causal_graph_nodes(node_id),
    FOREIGN KEY (to_node)   REFERENCES causal_graph_nodes(node_id),
    UNIQUE (from_node, to_node),
    CHECK (confidence_level IS NULL OR confidence_level IN ('high','medium','low'))
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON causal_graph_edges(from_node);
CREATE INDEX IF NOT EXISTS idx_edges_to   ON causal_graph_edges(to_node);
