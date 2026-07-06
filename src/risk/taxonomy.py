"""Layer6リスクカテゴリ・タクソノミー。"""

from __future__ import annotations

from enum import Enum


class RiskCategory(str, Enum):
    """§6監視項目(docs/investment_os_design.md §4.7)。"""

    REGULATION = "regulation"
    TECH_DEFEAT = "tech_defeat"
    DILUTION = "dilution"
    COMPETITION_LOSS = "competition_loss"
    CAPEX_CUT = "capex_cut"
    CUSTOMER_CHURN = "customer_churn"


# risk_level閾値。collapse_watch.pyのLEVEL_THRESHOLDS(6項目版、指示書15項目の
# 比率20%/33%/47%をスケール)をそのまま踏襲する(カテゴリ数も6のため直接再利用可能)。
RISK_LEVEL_THRESHOLDS: dict[int, int] = {1: 2, 2: 3, 3: 4}

# 悪化判定の移動平均乖離閾値(%)。collapse_watchの光通信バスケット判定(-7%)に準拠。
MOMENTUM_DETERIORATION_THRESHOLD = -7.0
MOMENTUM_MA_WINDOW = 25

# regulation判定に使う材料キーワード(規制・制裁色。policy_tailwindの支援色キーワードとは
# 逆方向のため意図的に別リストとする)。
REGULATION_KEYWORDS: tuple[str, ...] = (
    "規制", "制裁", "輸出規制", "独占禁止", "課徴金", "認可取消", "ライセンス取消", "禁輸",
)

# dilution(希薄化)判定キーワード。
DILUTION_KEYWORDS: tuple[str, ...] = (
    "新株予約権", "公募増資", "第三者割当", "希薄化", "転換社債",
)

# customer_churn(顧客離脱)判定キーワード。
CUSTOMER_CHURN_KEYWORDS: tuple[str, ...] = (
    "契約解除", "取引停止", "受注減", "取引終了", "契約打ち切り",
)

MATERIALS_LOOKBACK_DAYS = 180
