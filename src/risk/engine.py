"""Layer6リスクエンジン — 保有銘柄×6カテゴリを評価し、テーマへ集約する。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

from src.config import DATA_PROCESSED, held_instruments
from src.risk.detectors import (
    detect_capex_cut,
    detect_competition_loss,
    detect_customer_churn,
    detect_dilution,
    detect_regulation,
    detect_tech_defeat,
)
from src.risk.models import RiskItem, ThemeRiskSummary
from src.risk.taxonomy import RISK_LEVEL_THRESHOLDS

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)


def compute_risk_items(
    as_of: date | None = None,
    materials_conn: sqlite3.Connection | None = None,
    processed_dir: Path = PROCESSED_DIR,
) -> list[RiskItem]:
    """保有銘柄ごとに6カテゴリ全てを評価する。"""
    d = as_of or date.today()
    items: list[RiskItem] = []

    for inst in held_instruments():
        theme = inst.layer.value
        target = inst.key
        items.append(detect_capex_cut(theme, target, d, processed_dir))
        items.append(detect_competition_loss(theme, target, d, processed_dir))
        items.append(detect_tech_defeat(theme, target, d))
        items.append(detect_regulation(theme, target, materials_conn, d))
        items.append(detect_dilution(theme, target, materials_conn, d))
        items.append(detect_customer_churn(theme, target, materials_conn, d))

    return items


def _risk_level_from_count(n_deteriorated: int) -> int:
    level = 0
    for lv in (3, 2, 1):
        if n_deteriorated >= RISK_LEVEL_THRESHOLDS[lv]:
            level = lv
            break
    return level


def summarize_by_theme(items: list[RiskItem]) -> list[ThemeRiskSummary]:
    """テーマ単位で悪化カテゴリ数を集計し、risk_level(0-3)を算出する。

    同一テーマに複数銘柄がある場合、カテゴリごとに「いずれかの銘柄が悪化」を
    そのテーマの悪化とみなす(worst-case、collapse_watchの「悪化したら発報」思想を踏襲)。
    """
    themes = sorted({i.theme for i in items})
    summaries: list[ThemeRiskSummary] = []

    for theme in themes:
        theme_items = [i for i in items if i.theme == theme]
        by_category: dict[str, list[RiskItem]] = {}
        for i in theme_items:
            by_category.setdefault(i.category, []).append(i)

        monitorable_categories = {
            cat for cat, its in by_category.items()
            if any(it.deteriorated is not None for it in its)
        }
        deteriorated_categories = {
            cat for cat, its in by_category.items()
            if any(it.deteriorated is True for it in its)
        }

        n_monitorable = len(monitorable_categories)
        n_deteriorated = len(deteriorated_categories)
        risk_level = _risk_level_from_count(n_deteriorated)

        summaries.append(ThemeRiskSummary(
            theme=theme,
            risk_level=risk_level,
            n_deteriorated=n_deteriorated,
            n_monitorable=n_monitorable,
            items=theme_items,
            note=(
                f"監視可能{n_monitorable}カテゴリ中{n_deteriorated}カテゴリが悪化 → "
                f"risk_level{risk_level}。閾値(2/3/4)はcollapse_watch(§11)と同じ導出方式。"
            ),
        ))

    return summaries
