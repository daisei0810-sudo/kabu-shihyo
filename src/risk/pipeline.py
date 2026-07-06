"""Layer6リスクエンジンのパイプライン(Step10) — 判定→保存を統括する。

保有銘柄ごとの詳細(どのカテゴリでどう悪化しているか)は個別企業への懸念材料を
示すため private/risk_scores.csv(非公開)へ、テーマ集約のrisk_level(0-3、
具体的な理由は含まない)のみ outputs/risk_level_by_theme.csv(公開)へ保存する
(docs/investment_os_design.md §8確定事項)。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS, PRIVATE_OUTPUTS
from src.risk.engine import compute_risk_items, summarize_by_theme
from src.risk.models import RiskItem, ThemeRiskSummary
from src.scoring.theme_score import load_materials_conn

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)
PRIVATE_DIR = Path(PRIVATE_OUTPUTS)


def _save_private_risk_scores(items: list[RiskItem]) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    rows = [{
        "theme": i.theme, "target": i.target, "category": i.category,
        "risk_score": i.risk_score, "deteriorated": i.deteriorated,
        "evidence": i.evidence, "data_quality": i.data_quality, "as_of": i.as_of,
    } for i in items]
    path = PRIVATE_DIR / "risk_scores.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved(private): %s (%d rows)", path, len(rows))


def _save_public_risk_level(summaries: list[ThemeRiskSummary]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [{
        "theme": s.theme, "risk_level": s.risk_level,
        "n_deteriorated": s.n_deteriorated, "n_monitorable": s.n_monitorable,
        "note": s.note,
    } for s in summaries]
    path = OUTPUT_DIR / "risk_level_by_theme.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved: %s (%d rows)", path, len(rows))


def run_risk_engine(as_of: date | None = None) -> list[ThemeRiskSummary]:
    """Layer6を1回実行する。失敗しても例外は上げず、可能な範囲で継続する。"""
    d = as_of or date.today()

    materials_conn = load_materials_conn()
    try:
        items = compute_risk_items(as_of=d, materials_conn=materials_conn)
    finally:
        if materials_conn is not None:
            materials_conn.close()

    summaries = summarize_by_theme(items)

    try:
        _save_private_risk_scores(items)
        _save_public_risk_level(summaries)
    except Exception as exc:
        logger.warning("risk output save failed: %s", exc)

    for s in summaries:
        logger.info(
            "  [%s] risk_level=%d (%d/%d カテゴリ悪化)",
            s.theme, s.risk_level, s.n_deteriorated, s.n_monitorable,
        )

    return summaries
