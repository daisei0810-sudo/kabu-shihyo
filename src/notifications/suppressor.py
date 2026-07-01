"""§17通知禁止条件のフィルタ。材料由来とスコア由来で異なるロジックを適用する。

禁止条件#1,#3,#4,#5(材料由来)は既存のmaterials.pyの仕組み(dedup.py・freshness.py)を
再利用する。#2(スコア変化5点未満)はスコア由来通知にのみ適用する。#6(投資判断を
変えていない材料)は「related_ticker のdecisionが変化していない材料は抑制」として
呼び出し側(detectors.py)でDecisionChangeとの突合により実現する。
"""

from __future__ import annotations

from datetime import date, datetime

from src.materials.freshness import is_notification_allowed
from src.materials.models import Material
from src.materials.taxonomy import RENOTIFY_ALLOWED_RELATIONS, FreshnessScore, MaterialRelation
from src.notifications.models import Notification
from src.notifications.taxonomy import SCORE_NOTIFY_MIN_DELTA

DEFAULT_COOLDOWN_DAYS = 7


def should_suppress_score_notification(score_delta: float | None) -> tuple[bool, str]:
    """§17禁止条件#2: スコア変化5点未満は再通知しない(スコア由来通知にのみ適用)。"""
    if score_delta is None:
        return True, "変化量不明(履歴不足)"
    if abs(score_delta) < SCORE_NOTIFY_MIN_DELTA:
        return True, f"スコア変化{abs(score_delta):.1f}点 < 閾値{SCORE_NOTIFY_MIN_DELTA}点"
    return False, ""


def should_suppress_material_notification(
    material: Material,
    relation: MaterialRelation | None = None,
) -> tuple[bool, str]:
    """§17禁止条件#1,#3,#4,#5(材料由来通知にのみ適用)。"""
    # #1: 同一材料の重複(update/supersede以外の再通知は禁止)。
    #     ingest.py段階で既にduplicate/confirmは弾かれている想定だが、
    #     通知パイプライン側でも二重チェックする(防御的)。
    if relation is not None and relation not in RENOTIFY_ALLOWED_RELATIONS:
        return True, f"重複材料(relation={relation.value})、新事実なし"

    # #3: ソースDのみは投資判断変更に使わない
    if material.source_rank == "D":
        return True, "ソースD(SNS/未確認情報)のみ → 判断変更に使用不可"

    # #4: Cランク単独では格上げ・格下げしない(A/Bの裏付け or 顧客確認が必要)
    if material.source_rank == "C":
        return True, "Cランク単独(業界紙/アナリスト等) → A/Bの裏付けまたは顧客確認が必要"

    # #5: 公開から24時間超過かつ新事実なしは通知禁止(1日1回実行への緩和ルール込み、
    #     freshness.pyの既存ロジックをそのまま尊重する)
    freshness = (
        FreshnessScore(material.freshness_score)
        if material.freshness_score in {f.value for f in FreshnessScore}
        else FreshnessScore.C
    )
    allowed, reason = is_notification_allowed(freshness, material.new_fact_flag)
    if not allowed:
        return True, reason

    return False, ""


def is_notification_due(
    dedup_key: str,
    current_value: float,
    existing_notifications: list[Notification],
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    min_delta_for_override: float = SCORE_NOTIFY_MIN_DELTA,
    today: date | None = None,
) -> bool:
    """連日重複通知の抑制(状態継続型トリガー: dip/sell/capex向け)。

    同一dedup_keyの直近通知から cooldown_days 以内であれば、対象値が
    min_delta_for_override 以上さらに動いていない限り抑制する。
    閾値を跨ぎ続ける状態(例: dip_score>=75が5日連続)での連日発火を防ぐ。
    """
    today = today or date.today()
    same_key = [
        n for n in existing_notifications
        if n.dedup_key == dedup_key and n.status == "active"
    ]
    if not same_key:
        return True

    def _parse(n: Notification) -> datetime:
        try:
            return datetime.fromisoformat(n.notified_at)
        except ValueError:
            return datetime.min

    latest = max(same_key, key=_parse)
    try:
        last_date = datetime.fromisoformat(latest.notified_at).date()
    except ValueError:
        return True

    days_since = (today - last_date).days
    if days_since >= cooldown_days:
        return True

    last_value = latest.score_current if latest.score_current is not None else latest.dip_score
    if last_value is None:
        return True
    return abs(current_value - last_value) >= min_delta_for_override
