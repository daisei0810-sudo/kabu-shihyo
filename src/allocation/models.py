"""Layer9資金配分エンジンのデータクラス。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AllocationPolicy:
    """config/allocation_policy.yaml の内容。"""

    min_pct: float = 0.0
    max_pct: float = 40.0
    cash_floor_pct: float = 5.0
    correlation_penalty_threshold: float = 0.7
    correlation_penalty_factor: float = 0.7
    rebalance_threshold_pct: float = 5.0


@dataclass
class AllocationResult:
    """1テーマの配分提案。"""

    theme: str
    theme_score: float | None
    risk_haircut: float              # 0-1(risk_levelから導出)
    recommended_pct: float | None    # None = theme_score算出不可のため配分対象外
    current_pct: float | None        # private/holdings.csvから(未入力ならNone)
    diff_pct: float | None
    rationale: str
    confidence: float
    as_of: str
