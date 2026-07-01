"""通知検知ロジック(§17条件2-7, 11)。edge-triggered設計で連日スパムを防ぐ。

状態継続型トリガー(dip/sell/capex)は suppressor.is_notification_due() の
cooldown+差分オーバーライドで連日重複を抑制する。デルタ型(demand_index/ai_bubble)は
score_history.compute_score_change の 1日変化量が既に「変化があった日のみ非None」に
近い性質を持つため自然にedge-triggeredになる。decision_change/collapseは前回状態との
比較(diff_decisions / collapse_levelのscore_history)で構造的にedge-triggeredになる。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.notifications.confidence import compute_change_confidence
from src.notifications.models import DecisionChange, Notification
from src.notifications.suppressor import is_notification_due
from src.notifications.taxonomy import (
    AI_BUBBLE_DELTA_THRESHOLD,
    CAPEX_QOQ_DELTA_THRESHOLD,
    DEMAND_INDEX_DELTA_THRESHOLD,
    DIP_TRIGGER_THRESHOLD,
    SELL_TRIGGER_THRESHOLD,
    ConditionId,
    TriggerType,
)


def make_notification_id(
    trigger_type: str, target: str | None, condition_id: str, info_as_of: str
) -> str:
    """決定的なnotification_idを生成する(同日・同銘柄・同条件なら常に同一ID)。"""
    key = f"{trigger_type}|{target or '-'}|{condition_id}|{info_as_of}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]  # noqa: S324
    return f"ntf_{digest}"


@dataclass
class SnapshotContext:
    """通知に凍結保存する「先行指標ダッシュボード」のスナップショット(§18)。"""

    info_as_of: str
    confirmed_at: str
    real_demand_index: float | None
    ai_bubble_score: float | None
    cycle_scores_json: str
    collapse_level: int | None


def build_snapshot_context(
    demand_df: pd.DataFrame | None,
    cycles_df: pd.DataFrame | None,
    collapse_level: int | None,
    info_as_of: str,
) -> SnapshotContext:
    """demand_index_scores.csv / cycle_scores.csv / collapse_level からスナップショットを構築。"""
    real_demand = None
    ai_bubble = None
    if demand_df is not None and not demand_df.empty:
        real_row = demand_df[demand_df["label"] == "real_demand_index"]
        bubble_row = demand_df[demand_df["label"] == "ai_bubble_score"]
        if not real_row.empty:
            real_demand = _safe_float(real_row.iloc[0].get("score"))
        if not bubble_row.empty:
            ai_bubble = _safe_float(bubble_row.iloc[0].get("score"))

    cycles: dict[str, float | None] = {}
    if cycles_df is not None and not cycles_df.empty:
        for _, row in cycles_df.iterrows():
            cycles[str(row.get("key"))] = _safe_float(row.get("score"))

    return SnapshotContext(
        info_as_of=info_as_of,
        confirmed_at=datetime.now(UTC).isoformat(),
        real_demand_index=real_demand,
        ai_bubble_score=ai_bubble,
        cycle_scores_json=json.dumps(cycles, ensure_ascii=False),
        collapse_level=collapse_level,
    )


def _safe_float(val: object) -> float | None:
    try:
        f = float(val)  # type: ignore[arg-type]
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _base_notification(
    trigger_type: TriggerType, condition_id: ConditionId, ctx: SnapshotContext,
    target: str | None = None,
) -> Notification:
    info_as_of = ctx.info_as_of
    nid = make_notification_id(trigger_type.value, target, condition_id.value, info_as_of)
    dedup_key = f"{trigger_type.value}|{target or '-'}|{condition_id.value}"
    return Notification(
        notification_id=nid,
        trigger_type=trigger_type.value,
        condition_id=condition_id.value,
        dedup_key=dedup_key,
        info_as_of=info_as_of,
        confirmed_at=ctx.confirmed_at,
        notified_at=ctx.confirmed_at,
        target=target,
        real_demand_index=ctx.real_demand_index,
        ai_bubble_score=ctx.ai_bubble_score,
        cycle_scores_json=ctx.cycle_scores_json,
        collapse_level=ctx.collapse_level,
    )


# ---------------------------------------------------------------------------
# dip / sell (§17条件2,3)
# ---------------------------------------------------------------------------

def detect_dip_sell(
    dipsell_df: pd.DataFrame,
    prev_decisions_df: pd.DataFrame | None,
    ctx: SnapshotContext,
    existing_notifications: list[Notification],
) -> list[Notification]:
    """dip_score>=75 / sell_score>=70 を閾値クロス+cooldownで検知する。"""
    if dipsell_df is None or dipsell_df.empty:
        return []

    prev_by_target: dict[str, pd.Series] = {}
    if prev_decisions_df is not None and not prev_decisions_df.empty:
        prev_by_target = {
            str(row["target"]): row for _, row in prev_decisions_df.iterrows()
        }

    results: list[Notification] = []
    for _, row in dipsell_df.iterrows():
        target = str(row.get("target"))
        name_ja = str(row.get("name_ja", target))
        dip = _safe_float(row.get("dip_score"))
        sell = _safe_float(row.get("sell_score"))

        trigger_specs = (
            (dip, DIP_TRIGGER_THRESHOLD, TriggerType.DIP,
             ConditionId.DIP_SCORE_75, "dip_score"),
            (sell, SELL_TRIGGER_THRESHOLD, TriggerType.SELL,
             ConditionId.SELL_SCORE_70, "sell_score"),
        )
        for score, threshold, trigger, condition, label in trigger_specs:
            if score is None or score < threshold:
                continue
            n = _base_notification(trigger, condition, ctx, target=target)
            if not is_notification_due(n.dedup_key, score, existing_notifications):
                continue

            prev_row = prev_by_target.get(target)
            prev_val = _safe_float(prev_row.get(label)) if prev_row is not None else None
            is_new = prev_val is None or prev_val < threshold
            status_note = "新規到達" if is_new else "継続中(前回通知から一定変化あり)"

            n.name_ja = name_ja
            n.layer = None
            n.curr_judgment = str(row.get("decision", ""))
            n.change_reason = f"{label}={score:.0f}(閾値{threshold:.0f}以上)。{status_note}"
            n.dip_score = dip
            n.sell_score = sell
            n.hold_score = _safe_float(row.get("hold_score"))
            n.dip_sell_decision = str(row.get("decision", ""))
            n.recommended_action = str(row.get("recommended_action", ""))
            n.dip_sell_provisional = bool(row.get("provisional", True))
            n.score_name = label
            n.score_current = score
            n.change_confidence = compute_change_confidence(trigger, None, score - threshold)
            n.change_reason += " [dip/sell判定はテクニカル指標+スコアのみで近似した暫定版]"
            results.append(n)
    return results


# ---------------------------------------------------------------------------
# 実需指数 / AIバブルスコア (§17条件4,5)
# ---------------------------------------------------------------------------

def detect_demand_bubble(demand_df: pd.DataFrame, ctx: SnapshotContext) -> list[Notification]:
    """実需指数・AIバブルスコアの1日変化が閾値以上のとき通知する(自然にedge-triggered)。"""
    if demand_df is None or demand_df.empty:
        return []

    results: list[Notification] = []
    thresholds = {
        "real_demand_index": (DEMAND_INDEX_DELTA_THRESHOLD, TriggerType.DEMAND_INDEX,
                               ConditionId.DEMAND_INDEX_10PT, "実需指数"),
        "ai_bubble_score": (AI_BUBBLE_DELTA_THRESHOLD, TriggerType.AI_BUBBLE,
                             ConditionId.AI_BUBBLE_10PT, "AIバブルスコア"),
    }
    for _, row in demand_df.iterrows():
        label = str(row.get("label"))
        if label not in thresholds:
            continue
        threshold, trigger, condition, name_ja = thresholds[label]
        delta = _safe_float(row.get("change_1d"))
        score = _safe_float(row.get("score"))
        if delta is None or abs(delta) < threshold:
            continue

        n = _base_notification(trigger, condition, ctx, target=None)
        n.name_ja = name_ja
        n.curr_judgment = f"{score:.1f}" if score is not None else "--"
        direction = "改善方向" if delta > 0 else "悪化方向"
        n.change_reason = f"{name_ja}が1日で{delta:+.1f}点変化({direction})"
        n.score_name = label
        n.score_current = score
        n.score_delta_1d = delta
        n.change_confidence = compute_change_confidence(
            trigger, _safe_float(row.get("confidence_pct")), delta
        )
        results.append(n)
    return results


# ---------------------------------------------------------------------------
# 崩壊警戒 (§17条件6)
# ---------------------------------------------------------------------------

def detect_collapse(
    collapse_level: int | None,
    prev_collapse_level: float | None,
    ctx: SnapshotContext,
) -> list[Notification]:
    """AIサイクル崩壊警戒LEVELが前回から上昇した場合のみ通知する。

    前回履歴が無い(prev_collapse_level=None)場合は「上昇したか」を判定できないため
    通知しない(捏造しない)。
    """
    if collapse_level is None or prev_collapse_level is None:
        return []
    if collapse_level <= prev_collapse_level:
        return []

    n = _base_notification(TriggerType.COLLAPSE, ConditionId.COLLAPSE_LEVEL1, ctx, target=None)
    n.name_ja = "AIサイクル崩壊先行警戒"
    n.prev_judgment = f"LEVEL{int(prev_collapse_level)}"
    n.curr_judgment = f"LEVEL{collapse_level}"
    n.change_reason = f"崩壊警戒がLEVEL{int(prev_collapse_level)}→LEVEL{collapse_level}へ上昇"
    n.change_confidence = compute_change_confidence(
        TriggerType.COLLAPSE, None, float(collapse_level - prev_collapse_level) * 20
    )
    return [n]


# ---------------------------------------------------------------------------
# 投資判断変化 (§17条件7)
# ---------------------------------------------------------------------------

def detect_decision_changes(
    changes: list[DecisionChange], ctx: SnapshotContext
) -> list[Notification]:
    """diff_decisions()の結果をそのまま通知化する(既に差分抽出済みなのでedge-triggered)。"""
    results: list[Notification] = []
    for c in changes:
        n = _base_notification(
            TriggerType.DECISION_CHANGE, ConditionId.DECISION_CHANGED, ctx, target=c.target
        )
        n.name_ja = c.name_ja
        n.prev_judgment = c.prev_value
        n.curr_judgment = c.curr_value
        score_delta = None
        if c.prev_score is not None and c.curr_score is not None:
            score_delta = c.curr_score - c.prev_score
        n.change_reason = (
            f"{c.field}: {c.prev_value} → {c.curr_value}"
            + (f"(スコア{score_delta:+.0f})" if score_delta is not None else "")
        )
        n.change_confidence = compute_change_confidence(
            TriggerType.DECISION_CHANGE, None, score_delta
        )
        results.append(n)
    return results


# ---------------------------------------------------------------------------
# CAPEX急変 (§17条件11のスコア側)
# ---------------------------------------------------------------------------

def detect_capex(
    processed_dir: Path,
    ctx: SnapshotContext,
    existing_notifications: list[Notification],
) -> list[Notification]:
    """ハイパースケーラーCAPEXのQoQ変化が閾値以上のとき通知する(cooldown付き)。"""
    path = processed_dir / "capex_hyperscaler_total.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        s = df["hyperscaler_capex_total"].dropna()
    except Exception:
        return []
    if len(s) < 2:
        return []

    prev, curr = float(s.iloc[-2]), float(s.iloc[-1])
    if prev == 0:
        return []
    qoq = (curr - prev) / abs(prev)
    if abs(qoq) < CAPEX_QOQ_DELTA_THRESHOLD:
        return []

    n = _base_notification(TriggerType.CAPEX, ConditionId.CAPEX_CHANGE, ctx, target=None)
    if not is_notification_due(n.dedup_key, qoq * 100, existing_notifications):
        return []

    n.name_ja = "ハイパースケーラーCAPEX"
    n.curr_judgment = f"QoQ {qoq:+.0%}"
    n.change_reason = (
        f"ハイパースケーラーCAPEXがQoQで{qoq:+.0%}変化"
        f"(閾値±{CAPEX_QOQ_DELTA_THRESHOLD:.0%})"
    )
    n.score_name = "capex_hyperscaler_total"
    n.score_current = qoq * 100
    n.change_confidence = compute_change_confidence(TriggerType.CAPEX, None, qoq * 100)
    return [n]
