"""材料レイヤーの enum・定数タクソノミー(§6-8)。

既存 src/config.py の Layer/DataQuality は「時系列指標」の分類軸であり、
本モジュールの SourceRank は「材料(ニュース/IR)の発信元信頼性」という別軸。
両者は交差する独立した2軸であり、意図的に合流させない(Opus設計ドキュメント§6参照)。
"""

from __future__ import annotations

from enum import Enum


class SourceRank(str, Enum):
    """材料の発信元信頼性ランク(§8)。"""

    A = "A"  # 企業IR/決算/SEC/政府発表/取引所開示/顧客公式発表
    B = "B"  # Reuters/Bloomberg等の大手報道
    C = "C"  # 業界紙/アナリスト/サプライチェーン観測
    D = "D"  # SNS/未確認情報


# ランクの強さ順(小さいほど強い)。supersede判定等で使用。
RANK_ORDER: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3}


class SourceType(str, Enum):
    """情報源の種別。SourceRank へのマッピング元(§8)。"""

    COMPANY_IR = "company_ir"
    SEC = "sec"
    GOV = "gov"
    EXCHANGE = "exchange"
    CUSTOMER_OFFICIAL = "customer_official"
    WIRE = "wire"
    TRADE_PRESS = "trade_press"
    ANALYST = "analyst"
    SUPPLY_CHAIN = "supply_chain"
    SNS = "sns"
    UNKNOWN = "unknown"


# source_type → source_rank マッピング表(§8)
SOURCE_TYPE_TO_RANK: dict[SourceType, SourceRank] = {
    SourceType.COMPANY_IR: SourceRank.A,
    SourceType.SEC: SourceRank.A,
    SourceType.GOV: SourceRank.A,
    SourceType.EXCHANGE: SourceRank.A,
    SourceType.CUSTOMER_OFFICIAL: SourceRank.A,
    SourceType.WIRE: SourceRank.B,
    SourceType.TRADE_PRESS: SourceRank.C,
    SourceType.ANALYST: SourceRank.C,
    SourceType.SUPPLY_CHAIN: SourceRank.C,
    SourceType.SNS: SourceRank.D,
    SourceType.UNKNOWN: SourceRank.D,
}


class FreshnessScore(str, Enum):
    """鮮度スコア(§7)。S=24時間以内 / A=3日以内 / B=7日以内 / C=7日超。"""

    S = "S"
    A = "A"
    B = "B"
    C = "C"


class NodeType(str, Enum):
    """因果グラフノードの種別(§12)。"""

    COMPANY = "company"
    PRODUCT = "product"
    TECH = "tech"
    MATERIAL = "material"
    FACTOR = "factor"
    TICKER = "ticker"
    MACRO = "macro"
    POLICY = "policy"


class NotificationStatus(str, Enum):
    """材料の通知状態(§6)。"""

    PENDING = "pending"
    NOTIFIED = "notified"
    SUPPRESSED = "suppressed"
    RENOTIFIED = "renotified"


class MaterialRelation(str, Enum):
    """新規材料と既存材料の関係分類(§6 重複検知)。"""

    DUPLICATE = "duplicate"   # 同一事実の重複 → 再通知禁止
    CONFIRM = "confirm"       # 同内容の後追い → 再通知禁止
    UPDATE = "update"         # 新事実追加 → 再通知許可
    SUPERSEDE = "supersede"   # 上位ソースによる上書き → 再通知許可


# 再通知が許される関係(§6)
RENOTIFY_ALLOWED_RELATIONS: frozenset[MaterialRelation] = frozenset({
    MaterialRelation.UPDATE,
    MaterialRelation.SUPERSEDE,
})

# 重複検知のタイトル類似度閾値(初期値。実データで調整可能な定数として分離)
DEDUP_SIMILARITY_THRESHOLD = 0.72

# 鮮度スコアの時間閾値(時間)
FRESHNESS_THRESHOLDS_HOURS: dict[FreshnessScore, float] = {
    FreshnessScore.S: 24.0,
    FreshnessScore.A: 72.0,
    FreshnessScore.B: 168.0,
    # C はそれ以外(閾値なし)
}

# スコア変化とみなす最小差分(§6 再通知条件「スコア変化5点以上」)
SCORE_CHANGE_RENOTIFY_THRESHOLD = 5.0 / 100.0  # confidence_score は0-1スケール
