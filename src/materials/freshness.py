"""鮮度スコアと通知可否判定(§7)。

GitHub Actionsが1日1回しか実行されない制約への対処:
  「情報公開から24時間超過後の初回通知は禁止」を文字通り適用すると、
  実行タイミング次第で正当な新規材料まで一律ブロックされてしまう
  (公開→検知のラグが構造的に最大24時間発生するため)。

  そこで禁止判定の基準を「公開→検知ラグ」ではなく「検知→通知ラグ」に置く:
  検知した材料をそのシステム実行で通知する限りは許可する。
  公開から検知まで時間が空いた場合は detection_delayed フラグを立て、
  隠さず「検知遅延」として明示する運用にする。
"""

from __future__ import annotations

from datetime import datetime

from src.materials.taxonomy import FRESHNESS_THRESHOLDS_HOURS, FreshnessScore

# 「古い情報」とみなす閾値(この鮮度以下は new_fact_flag が無いと通知禁止)
_STALE_FRESHNESS = FreshnessScore.C


def compute_freshness_score(
    published_at: datetime | None,
    detected_at: datetime,
) -> FreshnessScore:
    """情報公開から検知までの経過時間で鮮度を格付け。

    published_at が不明な場合は最低ランク(C)とし、検知遅延として扱う。
    """
    if published_at is None:
        return FreshnessScore.C

    hours = (detected_at - published_at).total_seconds() / 3600.0
    if hours <= FRESHNESS_THRESHOLDS_HOURS[FreshnessScore.S]:
        return FreshnessScore.S
    if hours <= FRESHNESS_THRESHOLDS_HOURS[FreshnessScore.A]:
        return FreshnessScore.A
    if hours <= FRESHNESS_THRESHOLDS_HOURS[FreshnessScore.B]:
        return FreshnessScore.B
    return FreshnessScore.C


def is_detection_delayed(published_at: datetime | None, detected_at: datetime) -> bool:
    """公開から検知まで24時間を超えているか(1日1回実行の構造的ラグの可視化用)。"""
    if published_at is None:
        return True
    hours = (detected_at - published_at).total_seconds() / 3600.0
    return hours > FRESHNESS_THRESHOLDS_HOURS[FreshnessScore.S]


def is_notification_allowed(
    freshness: FreshnessScore,
    new_fact_flag: bool,
) -> tuple[bool, str]:
    """初回通知の可否判定(§7)。

    鮮度Cかつ新事実フラグが立っていない場合のみ、蒸し返し防止で通知を抑制する。
    それ以外は許可(検知遅延がある場合も、通知本文側で detection_delayed を明示する前提)。
    """
    if freshness == _STALE_FRESHNESS and not new_fact_flag:
        return False, "公開から7日超過かつ新事実なし → 蒸し返し防止で通知抑制"
    return True, "通知可"
