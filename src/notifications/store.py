"""notifications.jsonl / backtests.jsonl の永続化。

materials.py と異なり、notifications は関係(グラフ)を持たない単純な追記ログのため
SQLiteは使わずJSONL単体で完結させる(機構を軽くする設計判断)。
JSONLの決定的シリアライズ(id昇順ソート・キー順固定・1行1レコード)は
materials/db.py と同じ思想を踏襲し、git diffがクリーンになるようにする。
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

from src.notifications.models import Backtest, Notification

NOTIFICATIONS_PATH = "outputs/notifications/notifications.jsonl"
BACKTESTS_PATH = "outputs/notifications/backtests.jsonl"


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


def load_notifications(path: str = NOTIFICATIONS_PATH) -> list[Notification]:
    """notifications.jsonl を読み込む。存在しなければ空リスト。"""
    field_names = {f.name for f in fields(Notification)}
    return [
        Notification(**{k: v for k, v in rec.items() if k in field_names})
        for rec in _read_jsonl(Path(path))
    ]


def save_notifications(notifications: list[Notification], path: str = NOTIFICATIONS_PATH) -> None:
    """notifications.jsonl へ決定的シリアライズで書き出す(全件上書き)。"""
    _write_jsonl(Path(path), [asdict(n) for n in notifications], "notification_id")


def upsert_notifications(
    new_notifications: list[Notification], path: str = NOTIFICATIONS_PATH
) -> list[Notification]:
    """既存notifications.jsonlへ新規レコードを追加/更新し、全体を返す。

    notification_idが一致する既存レコードは新規で上書きする(冪等な日次再実行対応)。
    """
    existing = {n.notification_id: n for n in load_notifications(path)}
    for n in new_notifications:
        existing[n.notification_id] = n
    merged = list(existing.values())
    save_notifications(merged, path)
    return merged


def load_backtests(path: str = BACKTESTS_PATH) -> list[Backtest]:
    """backtests.jsonl を読み込む。存在しなければ空リスト。"""
    field_names = {f.name for f in fields(Backtest)}
    return [
        Backtest(**{k: v for k, v in rec.items() if k in field_names})
        for rec in _read_jsonl(Path(path))
    ]


def save_backtests(backtests: list[Backtest], path: str = BACKTESTS_PATH) -> None:
    """backtests.jsonl へ決定的シリアライズで書き出す(全件上書き)。"""
    _write_jsonl(Path(path), [asdict(b) for b in backtests], "backtest_id")


def upsert_backtests(new_backtests: list[Backtest], path: str = BACKTESTS_PATH) -> list[Backtest]:
    """既存backtests.jsonlへ新規/更新レコードをマージして全体を返す。"""
    existing = {b.backtest_id: b for b in load_backtests(path)}
    for b in new_backtests:
        existing[b.backtest_id] = b
    merged = list(existing.values())
    save_backtests(merged, path)
    return merged
