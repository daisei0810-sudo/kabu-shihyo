"""通知・事後検証のデータクラス(§13, §18)。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Notification:
    """1件の通知(§18テンプレートの全項目をカバー)。"""

    notification_id: str
    trigger_type: str
    condition_id: str
    dedup_key: str

    # --- 時刻(§18) ---
    info_as_of: str
    confirmed_at: str
    notified_at: str
    published_at: str | None = None
    first_detected_at: str | None = None
    first_judged_at: str | None = None
    freshness_score: str | None = None

    # --- 対象・判断変更サマリー ---
    target: str | None = None
    name_ja: str | None = None
    layer: str | None = None
    prev_judgment: str | None = None
    curr_judgment: str | None = None
    change_reason: str = ""
    change_confidence: float | None = None

    # --- 押し目・売り時(Phase8簡易版由来) ---
    dip_score: float | None = None
    sell_score: float | None = None
    hold_score: float | None = None
    dip_sell_decision: str | None = None
    recommended_action: str | None = None
    dip_sell_provisional: bool = True

    # --- 先行指標ダッシュボードのスナップショット(通知時点で凍結) ---
    real_demand_index: float | None = None
    ai_bubble_score: float | None = None
    cycle_scores_json: str = "{}"
    collapse_level: int | None = None

    # --- スコア変化系 ---
    score_name: str | None = None
    score_current: float | None = None
    score_delta_1d: float | None = None
    score_delta_1w: float | None = None

    # --- 材料系(現状ほぼ全てNone。材料データ蓄積後に埋まる) ---
    material_id: str | None = None
    source_rank: str | None = None
    material_relation: str | None = None
    detection_delayed: bool = False

    # --- 根拠・分類 ---
    bull_points_json: str = "[]"
    bear_points_json: str = "[]"
    invalidation_conditions_json: str = "[]"
    sources_json: str = "[]"

    status: str = "active"   # "active" | "archived"


@dataclass
class DecisionChange:
    """投資判断の変化(§17条件7の検知結果)。"""

    target: str
    name_ja: str
    field: str          # "outlook" | "action" | "dip_decision"
    prev_value: str
    curr_value: str
    prev_score: float | None = None
    curr_score: float | None = None


@dataclass
class Backtest:
    """1通知×1ホライズンの事後検証レコード(§13)。"""

    backtest_id: str
    notification_id: str
    horizon: str                    # "1w" | "1m" | "3m"
    baseline_date: str
    eval_due_date: str
    ticker: str | None = None
    baseline_price: float | None = None
    evaluated_at: str | None = None
    actual_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None
    max_drawdown: float | None = None
    false_positive_flag: bool | None = None
    late_detection_flag: bool | None = None
    overreaction_flag: bool | None = None
    benchmark_is_approximate: bool = False
    status: str = "pending"         # "pending" | "evaluated" | "skipped_no_data"


@dataclass
class BacktestSummary:
    """事後検証の集計サマリー(daily_report表示用)。学習は行わない(Phase10へ先送り)。"""

    n_pending: int
    n_evaluated: int
    n_skipped: int
    avg_excess_return: float | None = None
    false_positive_rate: float | None = None
    next_due_date: str | None = None
    components: list[Backtest] = field(default_factory=list)
