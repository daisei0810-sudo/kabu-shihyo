"""投資判断履歴の蓄積 + 前回との差分検知(§17条件7)。

score_history.py(Phase7)は (date, score:float, confidence:float) の数値専用スキーマで、
outlook/action/decision のようなカテゴリ文字列を保存できない。無理に数値コード化すると
可読性・diff追跡性を損なうため、判断履歴専用の機構をここに新設する。

保存方式は score_history.py と同じ思想(日次追記・履歴不足時はNoneを返し捏造しない)を
踏襲するが、1銘柄1CSVではなく「全銘柄を1日1CSV」のスナップショット方式にする
(銘柄追加時のファイル増殖回避・比較の一括読込のため)。保有銘柄ごとのoutlook/action/
dip_decisionを含むため、outputs/ではなくprivate/(gitignore対象)配下に保存する
(docs/investment_os_design.md §8確定事項)。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import PRIVATE_OUTPUTS
from src.notifications.models import DecisionChange

logger = logging.getLogger(__name__)

DECISIONS_HISTORY_DIR = Path(PRIVATE_OUTPUTS) / "history" / "decisions"


def build_current_snapshot(signals_df: pd.DataFrame, dipsell_df: pd.DataFrame) -> pd.DataFrame:
    """portfolio_signal_scores.csv / dip_sell_scores.csv から当日スナップショットを構築する。

    snapshot_decisions()(保存用)とパイプライン側のdiff算出の両方から使う共通ロジック。
    """
    signals = signals_df.copy() if signals_df is not None else pd.DataFrame()
    dipsell = dipsell_df.copy() if dipsell_df is not None else pd.DataFrame()

    if not signals.empty:
        signals = signals[~signals["target"].astype(str).str.contains("demand", na=False)]

    snapshot_cols = ["target", "name_ja", "outlook", "action", "extended_score", "confidence_pct"]
    merged = (
        signals[snapshot_cols].copy()
        if not signals.empty
        else pd.DataFrame(columns=snapshot_cols)
    )

    if not dipsell.empty:
        ds_cols = dipsell[["target", "decision", "dip_score", "sell_score"]].rename(
            columns={"decision": "dip_decision"}
        )
        merged = merged.merge(ds_cols, on="target", how="outer")
    else:
        merged["dip_decision"] = None
        merged["dip_score"] = None
        merged["sell_score"] = None

    return merged


def snapshot_decisions(
    signals_df: pd.DataFrame,
    dipsell_df: pd.DataFrame,
    as_of: date | None = None,
    history_dir: Path = DECISIONS_HISTORY_DIR,
) -> None:
    """当日の判断状態を outputs/history/decisions/{as_of}.csv に書き出す(冪等)。"""
    history_dir.mkdir(parents=True, exist_ok=True)
    d = as_of or date.today()

    merged = build_current_snapshot(signals_df, dipsell_df)
    merged["as_of"] = d.isoformat()
    path = history_dir / f"{d.isoformat()}.csv"
    merged.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved decision snapshot: %s (%d rows)", path, len(merged))


def load_previous_decisions(
    as_of: date | None = None, history_dir: Path = DECISIONS_HISTORY_DIR
) -> pd.DataFrame | None:
    """as_of より前の最新スナップショットを1つ返す。無ければNone(初回=履歴なし)。"""
    if not history_dir.exists():
        return None
    d = as_of or date.today()
    candidates = sorted(
        p for p in history_dir.glob("*.csv")
        if p.stem < d.isoformat()
    )
    if not candidates:
        return None
    try:
        return pd.read_csv(candidates[-1])
    except Exception as exc:
        logger.warning("decision history load failed: %s", exc)
        return None


def diff_decisions(
    prev: pd.DataFrame | None, curr: pd.DataFrame
) -> list[DecisionChange]:
    """前回と今回を target で突合し、変化した銘柄のみ DecisionChange のリストを返す。

    prevがNone(初回実行)なら空リスト(履歴が無い状態からの変化は捏造しない)。
    """
    if prev is None or prev.empty or curr.empty:
        return []

    changes: list[DecisionChange] = []
    prev_idx = prev.set_index("target")
    curr_idx = curr.set_index("target")

    for target in curr_idx.index:
        if target not in prev_idx.index:
            continue
        p_row = prev_idx.loc[target]
        c_row = curr_idx.loc[target]
        name_ja = str(c_row.get("name_ja", target))

        for field_name in ("outlook", "action", "dip_decision"):
            p_val = p_row.get(field_name)
            c_val = c_row.get(field_name)
            if pd.isna(p_val) or pd.isna(c_val):
                continue
            if str(p_val) != str(c_val):
                changes.append(DecisionChange(
                    target=str(target),
                    name_ja=name_ja,
                    field=field_name,
                    prev_value=str(p_val),
                    curr_value=str(c_val),
                    prev_score=_safe_float(p_row.get("extended_score")),
                    curr_score=_safe_float(c_row.get("extended_score")),
                ))
    return changes


def _safe_float(val: object) -> float | None:
    try:
        f = float(val)  # type: ignore[arg-type]
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None
