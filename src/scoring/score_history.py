"""スコア履歴の蓄積 + score_change算出。

outputs/*.csv は毎回上書きされるため、スコアの時系列変化(score_change_1d/1w/1m)を
算出するには別途履歴を蓄積する必要がある。outputs/history/ に日次スナップショットを
1スコア=1CSVで追記し、GitHub Actionsの既存日次コミットフロー(data/・outputs/を
コミット)にそのまま乗せて永続化する想定。

蓄積開始直後は score_change を算出できない(履歴が無い)。この場合は推測でスコアの
変化を捏造せず、正直に None を返す。daily_report側で「履歴蓄積中」と表示すること。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS

logger = logging.getLogger(__name__)

HISTORY_DIR = Path(OUTPUTS) / "history"


def append_snapshot(
    score_name: str,
    value: float | None,
    confidence: float | None,
    as_of: date | None = None,
    history_dir: Path = HISTORY_DIR,
) -> None:
    """1スコアの当日スナップショットを outputs/history/{score_name}.csv に追記する。

    同一日に複数回実行された場合は当日分を上書きする(1日1レコード、冪等)。
    """
    history_dir.mkdir(parents=True, exist_ok=True)
    d = as_of or date.today()
    path = history_dir / f"{score_name}.csv"

    row = {"date": d.isoformat(), "score": value, "confidence": confidence}
    if path.exists():
        try:
            df = pd.read_csv(path)
        except Exception as exc:
            logger.warning("history load failed %s: %s", score_name, exc)
            df = pd.DataFrame(columns=["date", "score", "confidence"])
        df = df[df["date"] != row["date"]]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])

    df = df.sort_values("date")
    df.to_csv(path, index=False, encoding="utf-8-sig")


def load_history(score_name: str, history_dir: Path = HISTORY_DIR) -> pd.DataFrame | None:
    """スコア履歴を読み込む。存在しなければ None。"""
    path = history_dir / f"{score_name}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date")
    except Exception as exc:
        logger.warning("history load failed %s: %s", score_name, exc)
        return None


def compute_score_change(
    score_name: str,
    current_value: float | None,
    days_ago: int,
    today: date | None = None,
    history_dir: Path = HISTORY_DIR,
) -> float | None:
    """days_ago暦日前に最も近い過去スコアとの差分。履歴不足ならNone(捏造しない)。"""
    if current_value is None:
        return None
    df = load_history(score_name, history_dir)
    if df is None or df.empty:
        return None

    target_date = pd.Timestamp(today or date.today()) - pd.Timedelta(days=days_ago)
    past = df[df["date"] <= target_date]
    if past.empty:
        return None

    past_value = past.iloc[-1]["score"]
    if pd.isna(past_value):
        return None
    return round(current_value - float(past_value), 1)


def compute_all_changes(
    score_name: str, current_value: float | None, history_dir: Path = HISTORY_DIR
) -> dict[str, float | None]:
    """score_change_1d/1w/1m をまとめて計算する。"""
    return {
        "change_1d": compute_score_change(score_name, current_value, 1, history_dir=history_dir),
        "change_1w": compute_score_change(score_name, current_value, 7, history_dir=history_dir),
        "change_1m": compute_score_change(score_name, current_value, 30, history_dir=history_dir),
    }
