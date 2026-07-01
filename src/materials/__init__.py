"""材料(ニュース/IR/政府発表)基盤 — Phase5。

指示書「AI・半導体・量子・ロボティクス投資監視システム 最終統合指示書」
§6(材料ID・重複通知防止) / §7(鮮度管理) / §8(ソース信頼性) / §12(因果グラフ) /
§15(DBスキーマ) の基盤レイヤーを実装する。

重要な設計原則(Opus設計ドキュメント準拠):
  - このパッケージは src.scoring を import しない。逆も禁止。
    既存の Hard/Extended スコア計算(confidence_pct 等)へは一切干渉しない。
  - 正本は data/materials/*.jsonl。SQLite(data/materials.db)は実行毎に
    JSONL から再構築される揮発キャッシュであり、git 管理しない。
  - このPhaseはニュース取得器そのものは含まない(Phase6で実装)。
    材料の登録・重複検知・鮮度判定・因果グラフ構造のみを提供する。
"""

from src.materials.taxonomy import (
    FreshnessScore,
    NodeType,
    NotificationStatus,
    SourceRank,
    SourceType,
)

__all__ = [
    "SourceRank",
    "SourceType",
    "FreshnessScore",
    "NodeType",
    "NotificationStatus",
]
