"""Layer2 意思決定エンジン — シナリオ評価 + DecisionRecord生成。

`outputs/portfolio_signal_scores.csv`(Step3出力)を入力契約として読み、
既存 `scoring.portfolio._map_decision()` と同一のスコアベース判定ロジックを
そのまま使う。シナリオ(bull/neutral/bear)の成立率は判断の構造化開示として
付加するのみで、判断そのものを差し替えない。

decide() は prev_decision_id/change_reason を設定しない(前日比較は
decision/diff.py の責務)。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import INSTRUMENTS, OUTPUTS
from src.decision.assessment import assess_scenario
from src.decision.conditions import PROCESSED_DIR
from src.decision.models import DecisionRecord, ScenarioAssessment
from src.decision.scenarios import SCENARIOS_DIR, load_scenarios
from src.decision.taxonomy import LEGACY_ACTION_TO_L2

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)
SIGNALS_CSV = OUTPUT_DIR / "portfolio_signal_scores.csv"
THEME_SCORES_CSV = OUTPUT_DIR / "theme_scores.csv"

_INSTRUMENT_KEYS: frozenset[str] = frozenset(i.key for i in INSTRUMENTS)
_LAYER_BY_KEY: dict[str, str] = {i.key: i.layer.value for i in INSTRUMENTS}


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("%s load failed: %s", path, exc)
        return pd.DataFrame()


def _pick_active_scenario(
    bull: ScenarioAssessment, neutral: ScenarioAssessment, bear: ScenarioAssessment,
) -> str:
    """成立率最大のシナリオを「現在地」とする。同率時はneutral→bullの順で保守的に選ぶ。"""
    best = max(bull.fulfillment_rate, neutral.fulfillment_rate, bear.fulfillment_rate)
    if neutral.fulfillment_rate == best:
        return "neutral"
    if bull.fulfillment_rate == best:
        return "bull"
    return "bear"


def _build_reason(
    action: str, signal_note: str, active: str, assessment: ScenarioAssessment,
) -> str:
    scenario_label = {"bull": "強気", "neutral": "中立", "bear": "弱気"}[active]
    return (
        f"{signal_note} | シナリオ: {scenario_label}"
        f"(成立率{assessment.fulfillment_rate:.0%}, "
        f"観測可能{len(assessment.conditions) - len(assessment.unobservable)}/"
        f"{len(assessment.conditions)}条件)"
    )


def decide(
    as_of: date | None = None,
    signals_path: Path = SIGNALS_CSV,
    theme_scores_path: Path = THEME_SCORES_CSV,
    scenarios_dir: Path = SCENARIOS_DIR,
    processed_dir: Path = PROCESSED_DIR,
) -> list[DecisionRecord]:
    """当日のDecisionRecordを保有銘柄ごとに生成する。"""
    d = as_of or date.today()
    signals_df = _load_csv(signals_path)
    theme_scores_df = _load_csv(theme_scores_path)
    records: list[DecisionRecord] = []

    if signals_df.empty:
        logger.warning("portfolio_signal_scores.csv が空 or 未生成のためdecideをスキップ")
        return records

    for _, row in signals_df.iterrows():
        target = str(row.get("target", ""))
        if target not in _INSTRUMENT_KEYS:
            continue
        action_raw = row.get("action")
        if pd.isna(action_raw) or not str(action_raw):
            continue

        theme = _LAYER_BY_KEY.get(target, "")
        scenarios = load_scenarios(theme, scenarios_dir)
        confidence = float(row.get("confidence_pct") or 0.0)

        if scenarios is None:
            bull = ScenarioAssessment(theme=theme, scenario_type="bull", fulfillment_rate=0.0)
            neutral = ScenarioAssessment(theme=theme, scenario_type="neutral", fulfillment_rate=0.0)
            bear = ScenarioAssessment(theme=theme, scenario_type="bear", fulfillment_rate=0.0)
            active = "neutral"
            reason_suffix = "シナリオ未整備(config/scenarios/未作成のテーマ)"
        else:
            bull = assess_scenario(theme, scenarios.bull, target, d, processed_dir)
            neutral = assess_scenario(theme, scenarios.neutral, target, d, processed_dir)
            bear = assess_scenario(theme, scenarios.bear, target, d, processed_dir)
            active = _pick_active_scenario(bull, neutral, bear)
            reason_suffix = None

        assessments = [bull, neutral, bear]
        active_assessment = {"bull": bull, "neutral": neutral, "bear": bear}[active]

        legacy_action = str(action_raw)
        l2_action = LEGACY_ACTION_TO_L2.get(legacy_action, "保有継続")

        signal_note = str(row.get("signal_note", ""))
        reason = (
            f"{signal_note} | {reason_suffix}" if reason_suffix
            else _build_reason(l2_action, signal_note, active, active_assessment)
        )

        theme_score_val: float | None = None
        if not theme_scores_df.empty and "theme" in theme_scores_df.columns:
            match = theme_scores_df[theme_scores_df["theme"] == theme]
            if not match.empty and pd.notna(match.iloc[0].get("total")):
                theme_score_val = float(match.iloc[0]["total"])

        evidence = sorted({
            c.indicator_key for c in active_assessment.conditions if c.met is not None
        })

        records.append(DecisionRecord(
            decision_id=f"dec_{d.isoformat()}_{target}",
            as_of=d.isoformat(),
            target=target,
            theme=theme,
            action=l2_action,
            active_scenario=active,
            scenario_assessments=assessments,
            reason=reason,
            theme_score=theme_score_val,
            confidence=confidence,
            evidence_indicators=evidence,
        ))

    return records
