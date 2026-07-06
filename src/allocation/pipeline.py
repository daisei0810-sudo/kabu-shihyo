"""Layer9資金配分パイプライン(Step11) — 算出→保存を統括する。

推奨配分・現在配分・差分はいずれも保有資産構成を示すため非公開
(private/allocation.csv、gitignore対象、docs/investment_os_design.md §8確定事項)。
現在配分の入力は private/holdings.csv (theme, current_pct のみ。金額・株数は
持たない、§8確定事項) をユーザーが手動保守する。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.allocation.correlation import compute_theme_correlation_matrix
from src.allocation.engine import compute_allocations
from src.allocation.models import AllocationResult
from src.allocation.policy import load_policy
from src.config import OUTPUTS, PRIVATE_OUTPUTS

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)
PRIVATE_DIR = Path(PRIVATE_OUTPUTS)
HOLDINGS_CSV = PRIVATE_DIR / "holdings.csv"


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("%s load failed: %s", path, exc)
        return pd.DataFrame()


def _save_allocations(results: list[AllocationResult]) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    rows = [{
        "theme": r.theme, "theme_score": r.theme_score, "risk_haircut": r.risk_haircut,
        "recommended_pct": r.recommended_pct, "current_pct": r.current_pct,
        "diff_pct": r.diff_pct, "rationale": r.rationale, "confidence": r.confidence,
        "as_of": r.as_of,
    } for r in results]
    path = PRIVATE_DIR / "allocation.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved(private): %s (%d rows)", path, len(rows))


def run_allocation(as_of: date | None = None) -> list[AllocationResult]:
    """Layer9を1回実行する。失敗しても例外は上げず、可能な範囲で継続する。"""
    d = as_of or date.today()

    theme_scores_df = _load_csv(OUTPUT_DIR / "theme_scores.csv")
    risk_by_theme_df = _load_csv(OUTPUT_DIR / "risk_level_by_theme.csv")
    holdings_df = _load_csv(HOLDINGS_CSV)
    policy = load_policy()

    if theme_scores_df.empty:
        logger.warning("theme_scores.csv が空 or 未生成のため配分計算をスキップ")
        return []

    themes = theme_scores_df["theme"].dropna().astype(str).tolist()
    correlation_matrix = compute_theme_correlation_matrix(themes)

    results = compute_allocations(
        theme_scores_df, risk_by_theme_df, correlation_matrix, policy, holdings_df, d,
    )

    try:
        _save_allocations(results)
    except Exception as exc:
        logger.warning("allocation.csv save failed: %s", exc)

    for r in results:
        logger.info(
            "  [%s] 推奨=%s%% 現在=%s%% 差分=%s%%",
            r.theme,
            f"{r.recommended_pct:.1f}" if r.recommended_pct is not None else "--",
            f"{r.current_pct:.1f}" if r.current_pct is not None else "未入力",
            f"{r.diff_pct:+.1f}" if r.diff_pct is not None else "--",
        )

    return results
