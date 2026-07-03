"""予測の事後評価(Step7後半) — due_date到来分を実際の株価で評価する。

notifications/backtest_eval.pyのevaluate_due_backtests()と同じ設計
(eval_due_date<=today のpending分だけ評価、冪等upsert)。評価対象がテーマ
全体(target無し)ではなく必ず銘柄単位である点、方向的中(direction_hit)の
判定を持つ点がLayer5固有の拡張。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.prediction.models import Evaluation, Prediction, PredictionAccuracySummary
from src.prediction.prices import PROCESSED_DIR, price_at_or_before, resolve_price_series
from src.prediction.taxonomy import DIRECTIONAL_JUDGMENTS

logger = logging.getLogger(__name__)


def _max_drawdown(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    window = series[(series.index >= start) & (series.index <= end)]
    if window.empty:
        return None
    running_max = window.cummax()
    drawdown = (window - running_max) / running_max
    return float(drawdown.min())


def evaluate_due(
    evaluations: list[Evaluation],
    predictions_by_id: dict[str, Prediction],
    today: date | None = None,
    processed_dir: Path = PROCESSED_DIR,
) -> list[Evaluation]:
    """due_date <= today のpending評価を実際の価格で評価する。"""
    today = today or date.today()
    today_ts = pd.Timestamp(today)
    updated: list[Evaluation] = []

    price_cache: dict[str, tuple[pd.Series | None, bool]] = {}

    def _get_price(key: str) -> tuple[pd.Series | None, bool]:
        if key not in price_cache:
            price_cache[key] = resolve_price_series(key, processed_dir)
        return price_cache[key]

    for ev in evaluations:
        if ev.status != "pending":
            continue
        due = pd.Timestamp(ev.due_date)
        if due > today_ts:
            continue

        pred = predictions_by_id.get(ev.prediction_id)
        if pred is None or pred.baseline_price is None:
            ev.status = "skipped_no_data"
            ev.evaluated_at = datetime.now().isoformat()
            updated.append(ev)
            continue

        target_series, _ = _get_price(pred.target)
        eval_price = price_at_or_before(target_series, due) if target_series is not None else None
        if eval_price is None:
            ev.status = "skipped_no_data"
            ev.evaluated_at = datetime.now().isoformat()
            updated.append(ev)
            continue

        actual_return = (eval_price - pred.baseline_price) / pred.baseline_price
        baseline_ts = pd.Timestamp(pred.baseline_date)

        benchmark_return = None
        if pred.benchmark_key:
            bench_series, _ = _get_price(pred.benchmark_key)
            if bench_series is not None:
                bench_base = price_at_or_before(bench_series, baseline_ts)
                bench_eval = price_at_or_before(bench_series, due)
                if bench_base and bench_eval and bench_base != 0:
                    benchmark_return = (bench_eval - bench_base) / bench_base

        excess_return = (
            actual_return - benchmark_return if benchmark_return is not None else None
        )
        max_dd = _max_drawdown(target_series, baseline_ts, due)

        direction_hit = None
        if pred.judgment in DIRECTIONAL_JUDGMENTS:
            reference_return = excess_return if excess_return is not None else actual_return
            direction_hit = (pred.expected_direction * reference_return) > 0

        ev.evaluated_at = datetime.now().isoformat()
        ev.actual_return = round(actual_return, 4)
        ev.benchmark_return = round(benchmark_return, 4) if benchmark_return is not None else None
        ev.excess_return = round(excess_return, 4) if excess_return is not None else None
        ev.max_drawdown = round(max_dd, 4) if max_dd is not None else None
        ev.direction_hit = direction_hit
        ev.status = "evaluated"
        updated.append(ev)

    return updated


def summarize(evaluations: list[Evaluation], n_predictions: int) -> PredictionAccuracySummary:
    """事後検証結果を集計する(学習はしない、表示用サマリーのみ)。"""
    n_pending = sum(1 for e in evaluations if e.status == "pending")
    n_evaluated = sum(1 for e in evaluations if e.status == "evaluated")
    n_skipped = sum(1 for e in evaluations if e.status == "skipped_no_data")

    evaluated = [e for e in evaluations if e.status == "evaluated"]
    excess_returns = [e.excess_return for e in evaluated if e.excess_return is not None]
    avg_excess = round(sum(excess_returns) / len(excess_returns), 4) if excess_returns else None

    hits = [e.direction_hit for e in evaluated if e.direction_hit is not None]
    hit_rate = round(sum(1 for h in hits if h) / len(hits), 3) if hits else None

    pending = [e for e in evaluations if e.status == "pending"]
    next_due = min((e.due_date for e in pending), default=None)

    return PredictionAccuracySummary(
        n_predictions=n_predictions,
        n_pending_evaluations=n_pending,
        n_evaluated=n_evaluated,
        n_skipped=n_skipped,
        hit_rate=hit_rate,
        avg_excess_return=avg_excess,
        next_due_date=next_due,
    )
