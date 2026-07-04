"""前回DecisionRecordとの差分検知(Layer2の必須表示項目「変更理由」を生成)。

既存 notifications/decision_history.py の思想(prevがNoneなら空リスト=捏造しない)
を踏襲するが、対象はDecisionRecord(JSONL)であり、CSVスナップショットではない。
"""

from __future__ import annotations

from src.decision.models import DecisionChange, DecisionRecord


def diff(prev: list[DecisionRecord], curr: list[DecisionRecord]) -> list[DecisionChange]:
    """target単位で前回と今回を突合し、変化したフィールドのみ返す。"""
    prev_by_target = {r.target: r for r in prev}
    changes: list[DecisionChange] = []

    for r in curr:
        p = prev_by_target.get(r.target)
        if p is None:
            continue
        if p.action != r.action:
            changes.append(DecisionChange(
                target=r.target, theme=r.theme, field="action",
                prev_value=p.action, curr_value=r.action,
            ))
        if p.active_scenario != r.active_scenario:
            changes.append(DecisionChange(
                target=r.target, theme=r.theme, field="active_scenario",
                prev_value=p.active_scenario, curr_value=r.active_scenario,
            ))
    return changes


def attach_change_context(
    curr: list[DecisionRecord], prev: list[DecisionRecord] | None
) -> list[DecisionRecord]:
    """curr各レコードに prev_decision_id / change_reason を設定する(破壊的更新)。

    prevがNone(初回実行)の場合は変化なし扱い(履歴が無い状態からの変化は捏造しない)。
    """
    if not prev:
        for r in curr:
            r.prev_decision_id = None
            r.change_reason = None
        return curr

    prev_by_target = {p.target: p for p in prev}
    for r in curr:
        p = prev_by_target.get(r.target)
        if p is None:
            r.prev_decision_id = None
            r.change_reason = None
            continue

        r.prev_decision_id = p.decision_id
        parts = []
        if p.action != r.action:
            parts.append(f"投資判断: {p.action}→{r.action}")
        if p.active_scenario != r.active_scenario:
            scenario_label = {"bull": "強気", "neutral": "中立", "bear": "弱気"}
            parts.append(
                f"シナリオ: {scenario_label.get(p.active_scenario, p.active_scenario)}→"
                f"{scenario_label.get(r.active_scenario, r.active_scenario)}"
            )
        r.change_reason = " / ".join(parts) if parts else None

    return curr
