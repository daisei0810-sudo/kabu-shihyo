"""§13 事後検証(backtests)の生成・評価・集計。

導入直後は評価待ち(pending)のbacktestsが積み上がるだけで実際のexcess_return等は
ほぼ空欄になる。これは正常な立ち上がり状態であり、隠さず daily_report に
「評価待ちN件」として表示する(捏造しない)。

ticker(target)が無い通知(実需指数・AIバブルスコア・崩壊警戒・CAPEX等の市場全体系)は
自然な評価対象銘柄が無いためbacktestを作成しない。この制約も正直に明示する。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, INSTRUMENTS
from src.notifications.models import Backtest, BacktestSummary, Notification
from src.notifications.taxonomy import BACKTEST_HORIZONS, LAYER_BENCHMARK

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

_LAYER_BY_TICKER: dict[str, str] = {i.key: i.layer.value for i in INSTRUMENTS}

# dip=押し目(価格上昇を期待) / sell=売り時(下落・伸び悩みを警戒) の方向性判定に使う
_DIRECTIONAL_TRIGGERS = {"dip": 1, "sell": -1}


def _load_close_series(key: str, processed_dir: Path) -> pd.Series | None:
    path = processed_dir / f"price_{key}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        if "Close" not in df.columns:
            return None
        return df["Close"].dropna().sort_index()
    except Exception as exc:
        logger.warning("price load failed key=%s: %s", key, exc)
        return None


def _price_at_or_before(series: pd.Series, target_date: pd.Timestamp) -> float | None:
    """target_date以前で最も近い終値を返す(ルックアヘッド回避)。"""
    window = series[series.index <= target_date]
    if window.empty:
        return None
    return float(window.iloc[-1])


def create_pending_backtests(
    notifications: list[Notification],
    existing_backtests: list[Backtest],
    processed_dir: Path = PROCESSED_DIR,
) -> list[Backtest]:
    """新規通知に対しpending状態のbacktestを生成する(既存分は上書きしない)。"""
    existing_ids = {b.backtest_id for b in existing_backtests}
    new_backtests: list[Backtest] = []

    for n in notifications:
        if not n.target:
            continue  # 市場全体系の通知(実需指数等)は対象銘柄が無いため評価対象外

        price_series = _load_close_series(n.target, processed_dir)
        if price_series is None or price_series.empty:
            continue

        try:
            baseline_date = pd.Timestamp(n.info_as_of.split("T")[0])
        except (ValueError, IndexError):
            baseline_date = pd.Timestamp(date.today())

        baseline_price = _price_at_or_before(price_series, baseline_date)
        if baseline_price is None or baseline_price == 0:
            continue

        for horizon, days in BACKTEST_HORIZONS.items():
            backtest_id = f"{n.notification_id}_{horizon}"
            if backtest_id in existing_ids:
                continue
            eval_due = baseline_date + timedelta(days=days)
            new_backtests.append(Backtest(
                backtest_id=backtest_id,
                notification_id=n.notification_id,
                ticker=n.target,
                horizon=horizon,
                baseline_date=baseline_date.date().isoformat(),
                baseline_price=baseline_price,
                eval_due_date=eval_due.date().isoformat(),
                status="pending",
            ))
    return new_backtests


def _max_drawdown(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    window = series[(series.index >= start) & (series.index <= end)]
    if window.empty:
        return None
    running_max = window.cummax()
    drawdown = (window - running_max) / running_max
    return float(drawdown.min())


def evaluate_due_backtests(
    backtests: list[Backtest],
    notifications_by_id: dict[str, Notification],
    today: date | None = None,
    processed_dir: Path = PROCESSED_DIR,
) -> list[Backtest]:
    """eval_due_date <= today の pending backtest を評価する。"""
    today = today or date.today()
    today_ts = pd.Timestamp(today)
    updated: list[Backtest] = []

    price_cache: dict[str, pd.Series | None] = {}

    def _get_price(key: str) -> pd.Series | None:
        if key not in price_cache:
            price_cache[key] = _load_close_series(key, processed_dir)
        return price_cache[key]

    for bt in backtests:
        if bt.status != "pending":
            continue
        due = pd.Timestamp(bt.eval_due_date)
        if due > today_ts:
            continue

        ticker_series = _get_price(bt.ticker) if bt.ticker else None
        if ticker_series is None or bt.baseline_price is None:
            bt.status = "skipped_no_data"
            bt.evaluated_at = datetime.now().isoformat()
            updated.append(bt)
            continue

        eval_price = _price_at_or_before(ticker_series, due)
        if eval_price is None:
            bt.status = "skipped_no_data"
            bt.evaluated_at = datetime.now().isoformat()
            updated.append(bt)
            continue

        actual_return = (eval_price - bt.baseline_price) / bt.baseline_price

        layer = _LAYER_BY_TICKER.get(bt.ticker or "", None)
        benchmark_key = LAYER_BENCHMARK.get(layer, None) if layer else None
        benchmark_return = None
        benchmark_approx = False
        if benchmark_key:
            bench_series = _get_price(benchmark_key)
            baseline_ts = pd.Timestamp(bt.baseline_date)
            if bench_series is not None:
                bench_base = _price_at_or_before(bench_series, baseline_ts)
                bench_eval = _price_at_or_before(bench_series, due)
                if bench_base and bench_eval and bench_base != 0:
                    benchmark_return = (bench_eval - bench_base) / bench_base
                    benchmark_approx = True  # SOX等はlayer横断の暫定ベンチマーク(参考値)

        excess_return = (
            actual_return - benchmark_return if benchmark_return is not None else None
        )

        baseline_ts = pd.Timestamp(bt.baseline_date)
        max_dd = _max_drawdown(ticker_series, baseline_ts, due)

        notification = notifications_by_id.get(bt.notification_id)
        false_positive = None
        if notification is not None and notification.trigger_type in _DIRECTIONAL_TRIGGERS:
            expected_sign = _DIRECTIONAL_TRIGGERS[notification.trigger_type]
            false_positive = (expected_sign * actual_return) < 0

        overreaction = None
        if excess_return is not None:
            overreaction = abs(excess_return) < 0.03 and abs(actual_return) > 0.10

        late_detection = (
            notification.detection_delayed if notification is not None else None
        )

        bt.evaluated_at = datetime.now().isoformat()
        bt.actual_return = round(actual_return, 4)
        bt.benchmark_return = round(benchmark_return, 4) if benchmark_return is not None else None
        bt.excess_return = round(excess_return, 4) if excess_return is not None else None
        bt.max_drawdown = round(max_dd, 4) if max_dd is not None else None
        bt.false_positive_flag = false_positive
        bt.late_detection_flag = late_detection
        bt.overreaction_flag = overreaction
        bt.benchmark_is_approximate = benchmark_approx
        bt.status = "evaluated"
        updated.append(bt)

    return updated


def summarize_backtests(backtests: list[Backtest]) -> BacktestSummary:
    """事後検証結果を集計する(学習はしない、表示用サマリーのみ)。"""
    n_pending = sum(1 for b in backtests if b.status == "pending")
    n_evaluated = sum(1 for b in backtests if b.status == "evaluated")
    n_skipped = sum(1 for b in backtests if b.status == "skipped_no_data")

    evaluated = [b for b in backtests if b.status == "evaluated"]
    excess_returns = [b.excess_return for b in evaluated if b.excess_return is not None]
    avg_excess = round(sum(excess_returns) / len(excess_returns), 4) if excess_returns else None

    fp_flags = [b.false_positive_flag for b in evaluated if b.false_positive_flag is not None]
    fp_rate = round(sum(1 for f in fp_flags if f) / len(fp_flags), 3) if fp_flags else None

    pending = [b for b in backtests if b.status == "pending"]
    next_due = min((b.eval_due_date for b in pending), default=None)

    return BacktestSummary(
        n_pending=n_pending,
        n_evaluated=n_evaluated,
        n_skipped=n_skipped,
        avg_excess_return=avg_excess,
        false_positive_rate=fp_rate,
        next_due_date=next_due,
        components=backtests,
    )
