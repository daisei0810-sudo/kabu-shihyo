"""Layer6リスクエンジンのデータクラス。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskItem:
    """1銘柄×1リスクカテゴリの判定結果。"""

    theme: str
    target: str
    category: str                # RiskCategoryの値
    risk_score: float | None     # 0-100、Noneは判定不能
    deteriorated: bool | None    # None = データ不足で判定不能
    evidence: str
    data_quality: str            # "verified" | "proxy" | "estimated" | "unavailable"
    as_of: str


@dataclass
class ThemeRiskSummary:
    """1テーマのリスク集約(collapse_watch.CollapseWatchResultの一般化)。"""

    theme: str
    risk_level: int               # 0-3
    n_deteriorated: int
    n_monitorable: int
    items: list[RiskItem] = field(default_factory=list)
    note: str = ""
