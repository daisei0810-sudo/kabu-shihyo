"""Layer7/8のパイプライン(Step12) — 判定→保存を統括する。

保有銘柄の判断を含まないため、両方とも公開データとして outputs/ へ出力する
(docs/investment_os_design.md §8確定事項の対象外)。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS
from src.discovery.companies import DiscoveryCompany, compute_discovery_companies
from src.discovery.themes import DiscoveryTheme, load_and_compute_discovery_themes

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)


def _save_companies(items: list[DiscoveryCompany]) -> None:
    rows = [{
        "rank": i.rank, "company": i.company, "name_ja": i.name_ja, "theme": i.theme,
        "thesis": i.thesis, "expected_value": i.expected_value,
        "relative_momentum": i.relative_momentum, "risks": i.risks,
        "current_position": i.current_position, "confidence_pct": i.confidence_pct,
        "data_quality": i.data_quality, "as_of": i.as_of,
    } for i in items]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "discovery_companies.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved: %s (%d rows)", path, len(rows))


def _save_themes(items: list[DiscoveryTheme]) -> None:
    rows = [{
        "theme": t.theme, "name_ja": t.name_ja, "materials_count": t.materials_count,
        "materials_trend_note": t.materials_trend_note, "tam_estimate": t.tam_estimate,
        "growth_rate": t.growth_rate, "feasibility": t.feasibility,
        "candidates": t.candidates, "time_horizon": t.time_horizon,
        "data_quality": t.data_quality, "as_of": t.as_of,
    } for t in items]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "discovery_themes.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved: %s (%d rows)", path, len(rows))


def run_discovery(as_of: date | None = None) -> tuple[list[DiscoveryCompany], list[DiscoveryTheme]]:
    """Layer7(発掘)とLayer8(新テーマ)を1回実行する。失敗しても継続する。"""
    d = as_of or date.today()

    companies = compute_discovery_companies(as_of=d)
    themes = load_and_compute_discovery_themes(as_of=d)

    try:
        _save_companies(companies)
        _save_themes(themes)
    except Exception as exc:
        logger.warning("discovery output save failed: %s", exc)

    for c in companies[:5]:
        logger.info(
            "  [rank%d] %s(%s) expected_value=%s rel_momentum=%s",
            c.rank, c.company, c.theme,
            f"{c.expected_value:.0f}" if c.expected_value is not None else "--",
            f"{c.relative_momentum:+.1f}%" if c.relative_momentum is not None else "--",
        )
    for t in themes:
        logger.info("  [watch] %s: %s", t.theme, t.materials_trend_note)

    return companies, themes
