"""材料・因果グラフのデータクラス(§6, §12)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.materials.taxonomy import MaterialRelation, NotificationStatus, SourceRank


@dataclass
class Source:
    """ソースマスタ(§8)。"""

    source_id: str
    display_name: str
    source_rank: str
    source_type: str
    domain_pattern: str | None = None
    is_customer_official: bool = False
    created_at: str = ""


@dataclass
class MaterialDraft:
    """材料登録前の下書き(material_id 未確定)。"""

    title: str
    summary: str
    source_id: str
    source_rank: SourceRank
    published_at: datetime | None
    detected_at: datetime
    related_tickers: list[str] = field(default_factory=list)
    affected_factors: list[str] = field(default_factory=list)
    is_customer_official: bool = False


@dataclass
class Material:
    """確定済み材料(§6 保存項目)。"""

    material_id: str
    title: str
    summary: str
    source_id: str | None
    source_rank: str
    published_at: str | None            # ISO8601 UTC文字列 or None
    first_detected_at: str
    first_evaluated_at: str | None = None
    first_notified_at: str | None = None
    related_tickers: list[str] = field(default_factory=list)
    affected_factors: list[str] = field(default_factory=list)
    confidence_score: float | None = None
    new_fact_flag: bool = True
    notification_status: str = NotificationStatus.PENDING.value
    previous_material_relation: str | None = None   # JSON文字列 {"prev_id":..,"relation":..}
    freshness_score: str | None = None
    detection_delayed: bool = False
    content_hash: str | None = None
    dedup_bucket: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MaterialMatch:
    """重複検知の判定結果。"""

    matched_id: str
    relation: MaterialRelation
    similarity: float
    reason: str


@dataclass
class CausalNode:
    """因果グラフノード(§12)。"""

    node_id: str
    node_name: str
    node_type: str
    description: str = ""
    impact_weight: float = 1.0
    lag_days: int | None = None
    ticker_key: str | None = None
    layer: str | None = None
    confidence_level: str | None = None
    created_at: str = ""


@dataclass
class CausalEdge:
    """因果グラフエッジ(§12)。"""

    edge_id: str
    from_node: str
    to_node: str
    impact_weight: float = 1.0
    lag_days: int | None = None
    confidence_level: str | None = None
    edge_type: str | None = None
    created_at: str = ""
