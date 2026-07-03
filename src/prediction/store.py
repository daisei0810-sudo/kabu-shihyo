"""predictions.jsonl / evaluations.jsonl の永続化。

notifications/store.py と同じ思想: 単純な追記ログのためSQLiteは使わずJSONL単体で
完結させる。正本はgit管理し、決定的シリアライズ(id昇順ソート・キー順固定・
1行1レコード)でdiffをクリーンに保つ。
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

from src.prediction.models import Evaluation, Prediction

PREDICTIONS_PATH = "data/predictions/predictions.jsonl"
EVALUATIONS_PATH = "data/predictions/evaluations.jsonl"


def _write_jsonl(path: Path, records: list[dict], sort_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: r[sort_key])
    lines = [
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for r in ordered
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def load_predictions(path: str = PREDICTIONS_PATH) -> list[Prediction]:
    """predictions.jsonl を読み込む。存在しなければ空リスト。"""
    field_names = {f.name for f in fields(Prediction)}
    return [
        Prediction(**{k: v for k, v in rec.items() if k in field_names})
        for rec in _read_jsonl(Path(path))
    ]


def save_predictions(predictions: list[Prediction], path: str = PREDICTIONS_PATH) -> None:
    """predictions.jsonl へ決定的シリアライズで書き出す(全件上書き)。"""
    _write_jsonl(Path(path), [asdict(p) for p in predictions], "prediction_id")


def upsert_predictions(
    new_predictions: list[Prediction], path: str = PREDICTIONS_PATH
) -> list[Prediction]:
    """既存predictions.jsonlへ新規/更新レコードをマージして全体を返す(冪等)。"""
    existing = {p.prediction_id: p for p in load_predictions(path)}
    for p in new_predictions:
        existing[p.prediction_id] = p
    merged = list(existing.values())
    save_predictions(merged, path)
    return merged


def load_evaluations(path: str = EVALUATIONS_PATH) -> list[Evaluation]:
    """evaluations.jsonl を読み込む。存在しなければ空リスト。"""
    field_names = {f.name for f in fields(Evaluation)}
    return [
        Evaluation(**{k: v for k, v in rec.items() if k in field_names})
        for rec in _read_jsonl(Path(path))
    ]


def save_evaluations(evaluations: list[Evaluation], path: str = EVALUATIONS_PATH) -> None:
    """evaluations.jsonl へ決定的シリアライズで書き出す(全件上書き)。"""
    _write_jsonl(Path(path), [asdict(e) for e in evaluations], "evaluation_id")


def upsert_evaluations(
    new_evaluations: list[Evaluation], path: str = EVALUATIONS_PATH
) -> list[Evaluation]:
    """既存evaluations.jsonlへ新規/更新レコードをマージして全体を返す(冪等)。"""
    existing = {e.evaluation_id: e for e in load_evaluations(path)}
    for e in new_evaluations:
        existing[e.evaluation_id] = e
    merged = list(existing.values())
    save_evaluations(merged, path)
    return merged
