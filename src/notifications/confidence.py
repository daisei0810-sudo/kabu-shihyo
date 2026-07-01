"""変更確信度(change_confidence)の代理算出(§17条件1「確信度70以上」への対応)。

指示書§10の6項目加重確信度モデル(材料強度20+ソース信頼性20+市場未織り込み度20+
業績インパクト20+実需確認10+バリュエーション余地10)は未実装であり、材料の
confidence_score も現状常にNoneのため、厳密な§10確信度は算出できない。

代わりに、システムが既に持っている「確からしさ」の積で近似する代理指標を提供する。
全ての通知で、これが§10の正式値ではなく代理値であることを change_reason に明記すること
(推測でスコアを断定しないという既存プロジェクト思想の遵守)。
"""

from __future__ import annotations

from src.notifications.taxonomy import BASE_CONFIDENCE_BY_TRIGGER, TriggerType


def compute_change_confidence(
    trigger_type: TriggerType,
    data_confidence_pct: float | None,
    score_delta: float | None,
    delta_saturation: float = 20.0,
) -> float:
    """change_confidence(0-100)の代理値を算出する。

    = 基礎確信度(トリガー種別ごと) × データconfidence(0-1) × 変化量係数(変化が大きいほど1に近づく)
    """
    base = BASE_CONFIDENCE_BY_TRIGGER.get(trigger_type, 50.0)
    data_conf = data_confidence_pct if data_confidence_pct is not None else 0.5
    delta_factor = 1.0
    if score_delta is not None:
        delta_factor = min(1.0, abs(score_delta) / delta_saturation)
        delta_factor = max(delta_factor, 0.3)  # 変化量情報が無くても最低0.3は残す

    value = base * data_conf * delta_factor
    return round(max(0.0, min(100.0, value)), 1)


def material_rank_base_confidence(source_rank: str) -> float:
    """材料由来通知の基礎確信度(ソースランクA/B/C/Dに応じて調整)。"""
    rank_map = {"A": 75.0, "B": 60.0, "C": 30.0, "D": 10.0}
    return rank_map.get(source_rank, 30.0)
