"""DecisionRecordの永続化(private/decisions/{as_of}.jsonl、1日1ファイル)。

保有銘柄ごとの売買判断そのものであり、docs/investment_os_design.md §8確定事項
により公開リポジトリ(git管理)には置かない。private/はプロジェクトルートの
.gitignore対象ディレクトリ。日次自動実行(--step all)にはまだ組み込んでいない
(永続化方式が確定するまでは手動実行のみ、Step5と同じ判断)。

notifications/decision_history.py と同じ「1日1ファイルのスナップショット」方式
(銘柄追加時のファイル増殖回避・前回比較の一括読込のため)を踏襲する。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from src.decision.models import ConditionStatus, DecisionRecord, ScenarioAssessment

PRIVATE_DECISIONS_DIR = Path("private/decisions")


def _record_to_dict(record: DecisionRecord) -> dict:
    return asdict(record)


def _dict_to_record(d: dict) -> DecisionRecord:
    def _to_assessment(sa: dict) -> ScenarioAssessment:
        return ScenarioAssessment(
            theme=sa["theme"],
            scenario_type=sa["scenario_type"],
            fulfillment_rate=sa["fulfillment_rate"],
            conditions=[ConditionStatus(**c) for c in sa.get("conditions", [])],
            unmet=[ConditionStatus(**c) for c in sa.get("unmet", [])],
            unobservable=[ConditionStatus(**c) for c in sa.get("unobservable", [])],
        )

    return DecisionRecord(
        decision_id=d["decision_id"],
        as_of=d["as_of"],
        target=d["target"],
        theme=d["theme"],
        action=d["action"],
        active_scenario=d["active_scenario"],
        scenario_assessments=[_to_assessment(sa) for sa in d.get("scenario_assessments", [])],
        reason=d.get("reason", ""),
        prev_decision_id=d.get("prev_decision_id"),
        change_reason=d.get("change_reason"),
        theme_score=d.get("theme_score"),
        confidence=d.get("confidence", 0.0),
        evidence_indicators=d.get("evidence_indicators", []),
    )


def save_decisions(
    records: list[DecisionRecord], as_of: date, decisions_dir: Path = PRIVATE_DECISIONS_DIR
) -> None:
    """当日分のDecisionRecordを1ファイルへ決定的シリアライズで書き出す(全件上書き)。"""
    decisions_dir.mkdir(parents=True, exist_ok=True)
    path = decisions_dir / f"{as_of.isoformat()}.jsonl"
    ordered = sorted(records, key=lambda r: r.target)
    lines = [
        json.dumps(_record_to_dict(r), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for r in ordered
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_decisions(
    as_of: date, decisions_dir: Path = PRIVATE_DECISIONS_DIR
) -> list[DecisionRecord]:
    """指定日のDecisionRecordを読み込む。無ければ空リスト。"""
    path = decisions_dir / f"{as_of.isoformat()}.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(_dict_to_record(json.loads(line)))
    return records


def load_previous(
    as_of: date, decisions_dir: Path = PRIVATE_DECISIONS_DIR
) -> list[DecisionRecord] | None:
    """as_of より前の最新スナップショットを1つ返す。無ければNone(初回=履歴なし)。"""
    if not decisions_dir.exists():
        return None
    candidates = sorted(
        p for p in decisions_dir.glob("*.jsonl") if p.stem < as_of.isoformat()
    )
    if not candidates:
        return None
    records = []
    for line in candidates[-1].read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(_dict_to_record(json.loads(line)))
    return records
