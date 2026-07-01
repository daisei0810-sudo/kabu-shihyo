"""通知システムのタクソノミー(§17条件ID・閾値・ベンチマーク定義)。

重要な依存方向の制約(materials.pyと同じ思想を踏襲):
  notifications → scoring (可)
  notifications → materials (可、読み取りのみ)
  scoring → notifications (禁止)
  materials → notifications (禁止)
この制約により、既存のHard/Extended計算・材料取込は notifications の存在を
一切知らず、notificationsパッケージが壊れても既存フローに影響しない。
"""

from __future__ import annotations

from enum import Enum


class TriggerType(str, Enum):
    """通知のトリガー種別。"""

    DIP = "dip"                        # dip_score >= 閾値
    SELL = "sell"                      # sell_score >= 閾値
    DEMAND_INDEX = "demand_index"      # 実需指数の変化
    AI_BUBBLE = "ai_bubble"            # AIバブルスコアの変化
    COLLAPSE = "collapse"              # 崩壊警戒LEVEL上昇
    DECISION_CHANGE = "decision_change"  # outlook/action/dip_decisionの変化
    CAPEX = "capex"                    # ハイパースケーラーCAPEXの急変
    MATERIAL = "material"              # 材料由来(顧客確認/ガイダンス修正等)


# §17 通知条件ID(トレーサビリティ用)。材料由来条件は「実装済み・データ待ち」であり
# 未実装ではないことをコード上明示する。
class ConditionId(str, Enum):
    CHANGE_CONFIDENCE_70 = "c17_01_confidence"      # 代理判定(§10モデル未実装)
    DIP_SCORE_75 = "c17_02_dip"
    SELL_SCORE_70 = "c17_03_sell"
    DEMAND_INDEX_10PT = "c17_04_demand"
    AI_BUBBLE_10PT = "c17_05_bubble"
    COLLAPSE_LEVEL1 = "c17_06_collapse"
    DECISION_CHANGED = "c17_07_decision"
    CUSTOMER_CONFIRMATION = "c17_08_customer"       # 材料待ち
    GUIDANCE_REVISION = "c17_09_guidance"           # 材料待ち
    BACKLOG_CHANGE = "c17_10_backlog"                # 材料待ち
    CAPEX_CHANGE = "c17_11_capex"
    SUBSIDY_CONFIRMED = "c17_12_subsidy"             # 材料待ち


# 材料データ不足のため現状ほぼ発火しない条件(daily_reportで「待機中」と明示するために使用)
MATERIAL_DEPENDENT_CONDITIONS: frozenset[ConditionId] = frozenset({
    ConditionId.CUSTOMER_CONFIRMATION,
    ConditionId.GUIDANCE_REVISION,
    ConditionId.BACKLOG_CHANGE,
    ConditionId.SUBSIDY_CONFIRMED,
})

# --- 閾値定数(事前固定・後から検証可能) ---
DIP_TRIGGER_THRESHOLD = 75.0
SELL_TRIGGER_THRESHOLD = 70.0
DEMAND_INDEX_DELTA_THRESHOLD = 10.0
AI_BUBBLE_DELTA_THRESHOLD = 10.0
CAPEX_QOQ_DELTA_THRESHOLD = 0.15  # ±15%QoQで通知(collapse_watchの悪化判定=マイナスのみとは別軸)
CHANGE_CONFIDENCE_THRESHOLD = 70.0

# §17禁止条件#2: スコア変化5点未満は再通知しない(0-100スケール)
SCORE_NOTIFY_MIN_DELTA = 5.0

# 通知種別ごとの基礎確信度(change_confidence代理算出の起点、0-100)
BASE_CONFIDENCE_BY_TRIGGER: dict[TriggerType, float] = {
    TriggerType.DIP: 70.0,
    TriggerType.SELL: 70.0,
    TriggerType.COLLAPSE: 80.0,
    TriggerType.DEMAND_INDEX: 60.0,
    TriggerType.AI_BUBBLE: 60.0,
    TriggerType.DECISION_CHANGE: 50.0,
    TriggerType.CAPEX: 55.0,
    TriggerType.MATERIAL: 50.0,  # source_rankでさらに調整(confidence.py)
}

# layer別ベンチマーク指数(backtest評価用)。無料データの制約上、日本株・量子・ロボ・EVは
# 厳密な代表指数が無いためSOXを暫定ベンチとし、参考値である旨をbacktest側で明示する。
LAYER_BENCHMARK: dict[str, str | None] = {
    "semicap": "index_sox",
    "ai_datacenter": "index_sox",
    "robotics_fa": "index_sox",
    "ev_physical_ai": "index_sox",
    "quantum": "index_sox",
    "crypto_xrp": None,  # 適切なベンチマークが無料で存在しない → 絶対リターンのみ記録
    "china_ai": "index_sox",
    "policy": None,
}

BACKTEST_HORIZONS: dict[str, int] = {"1w": 7, "1m": 30, "3m": 90}

# Phase10(自動学習)着手の目安。100件蓄積は指示書§14の基準をそのまま踏襲。
AUTO_LEARNING_MIN_EVALUATED_BACKTESTS = 100
