"""通知パイプライン(Step6) — 検知→抑制→採番→保存→backtest生成/評価→判断履歴保存を統括する。

実行順序(冪等性のため厳守):
  ①前回判断履歴を読込 → ②当日CSV読込 → ③diff算出 → ④通知検知・生成 → ⑤保存 →
  ⑥backtest生成/評価 → ⑦当日の判断スナップショットを書き込み
(⑦を①より先に行うと比較対象の「前回」が当日分で上書きされてしまうため、順序を守る)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, INSTRUMENTS, OUTPUTS
from src.notifications.backtest_eval import (
    create_pending_backtests,
    evaluate_due_backtests,
    summarize_backtests,
)
from src.notifications.decision_history import (
    build_current_snapshot,
    diff_decisions,
    load_previous_decisions,
    snapshot_decisions,
)
from src.notifications.detectors import (
    build_snapshot_context,
    detect_capex,
    detect_collapse,
    detect_decision_changes,
    detect_demand_bubble,
    detect_dip_sell,
)
from src.notifications.models import BacktestSummary
from src.notifications.store import (
    load_backtests,
    load_notifications,
    upsert_backtests,
    upsert_notifications,
)
from src.scoring.score_history import compute_score_change

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)
PROCESSED_DIR = Path(DATA_PROCESSED)
_LAYER_BY_TICKER: dict[str, str] = {i.key: i.layer.value for i in INSTRUMENTS}


def _load_csv(name: str) -> pd.DataFrame | None:
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("load failed: %s: %s", name, exc)
        return None


def _current_collapse_level(collapse_df: pd.DataFrame | None) -> int | None:
    if collapse_df is None or collapse_df.empty or "deteriorated" not in collapse_df.columns:
        return None
    from src.scoring.collapse_watch import LEVEL_THRESHOLDS

    n_det = int(collapse_df["deteriorated"].fillna(False).astype(bool).sum())
    level = 0
    for lv in (3, 2, 1):
        if n_det >= LEVEL_THRESHOLDS[lv]:
            level = lv
            break
    return level


def run_notifications(today: date | None = None) -> BacktestSummary:
    """通知パイプラインを1回実行する。失敗しても例外は上げず、可能な範囲で継続する。"""
    d = today or date.today()
    now_iso = datetime.now().isoformat()

    # --- ①前回判断履歴を読込(当日分で上書きされる前に) ---
    prev_decisions = load_previous_decisions(as_of=d)

    # --- ②当日CSV読込 ---
    signals_df = _load_csv("portfolio_signal_scores.csv")
    dipsell_df = _load_csv("dip_sell_scores.csv")
    demand_df = _load_csv("demand_index_scores.csv")
    cycles_df = _load_csv("cycle_scores.csv")
    collapse_df = _load_csv("collapse_watch.csv")

    curr_snapshot = build_current_snapshot(
        signals_df if signals_df is not None else pd.DataFrame(),
        dipsell_df if dipsell_df is not None else pd.DataFrame(),
    )

    # --- ③diff算出 ---
    try:
        changes = diff_decisions(prev_decisions, curr_snapshot)
    except Exception as exc:
        logger.warning("diff_decisions failed: %s", exc)
        changes = []

    collapse_level = _current_collapse_level(collapse_df)
    collapse_delta_1d = (
        compute_score_change("collapse_level", float(collapse_level), 1)
        if collapse_level is not None else None
    )
    prev_collapse_level = (
        collapse_level - collapse_delta_1d
        if collapse_level is not None and collapse_delta_1d is not None else None
    )

    info_as_of = d.isoformat()
    ctx = build_snapshot_context(demand_df, cycles_df, collapse_level, info_as_of)

    existing_notifications = load_notifications()

    # --- ④通知検知・生成 ---
    new_notifications = []
    try:
        new_notifications += detect_dip_sell(
            dipsell_df if dipsell_df is not None else pd.DataFrame(),
            prev_decisions, ctx, existing_notifications,
        )
    except Exception as exc:
        logger.warning("detect_dip_sell failed: %s", exc)
    try:
        new_notifications += detect_demand_bubble(
            demand_df if demand_df is not None else pd.DataFrame(), ctx
        )
    except Exception as exc:
        logger.warning("detect_demand_bubble failed: %s", exc)
    try:
        new_notifications += detect_collapse(collapse_level, prev_collapse_level, ctx)
    except Exception as exc:
        logger.warning("detect_collapse failed: %s", exc)
    try:
        new_notifications += detect_decision_changes(changes, ctx)
    except Exception as exc:
        logger.warning("detect_decision_changes failed: %s", exc)
    try:
        new_notifications += detect_capex(PROCESSED_DIR, ctx, existing_notifications)
    except Exception as exc:
        logger.warning("detect_capex failed: %s", exc)

    for n in new_notifications:
        if n.target and not n.layer:
            n.layer = _LAYER_BY_TICKER.get(n.target)
        n.notified_at = now_iso

    # --- ⑤保存 ---
    all_notifications = upsert_notifications(new_notifications)
    logger.info(
        "notifications: 新規%d件 / 累計%d件", len(new_notifications), len(all_notifications)
    )

    # --- ⑥backtest生成/評価 ---
    existing_backtests = load_backtests()
    pending_new = create_pending_backtests(new_notifications, existing_backtests)
    notifications_by_id = {n.notification_id: n for n in all_notifications}
    due_updates = evaluate_due_backtests(
        existing_backtests + pending_new, notifications_by_id, today=d
    )
    all_backtests = upsert_backtests(pending_new + due_updates)
    summary = summarize_backtests(all_backtests)
    logger.info(
        "backtests: 評価待ち%d件 / 評価済み%d件 / データ無し%d件",
        summary.n_pending, summary.n_evaluated, summary.n_skipped,
    )

    # --- ⑦当日の判断スナップショットを書き込み ---
    try:
        snapshot_decisions(
            signals_df if signals_df is not None else pd.DataFrame(),
            dipsell_df if dipsell_df is not None else pd.DataFrame(),
            as_of=d,
        )
    except Exception as exc:
        logger.warning("snapshot_decisions failed: %s", exc)

    return summary
