"""シナリオ評価 — 条件群からScenarioAssessment(成立率・未成立条件)を組み立てる。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.decision.conditions import PROCESSED_DIR, evaluate_condition
from src.decision.models import Scenario, ScenarioAssessment


def assess_scenario(
    theme: str,
    scenario: Scenario,
    target_key: str,
    as_of: date,
    processed_dir: Path = PROCESSED_DIR,
) -> ScenarioAssessment:
    """1シナリオ(bull/neutral/bear)を実データで評価する。

    fulfillment_rate = Σ(成立条件のweight) / Σ(観測可能な条件のweight)。
    条件が1つも無い、または全条件が観測不能の場合は0.0(成立していないとみなす、
    捏造しない)。
    """
    statuses = [
        evaluate_condition(cond, target_key, as_of, processed_dir)
        for cond in scenario.conditions
    ]
    weight_by_id = {c.condition_id: c.weight for c in scenario.conditions}

    observable = [s for s in statuses if s.met is not None]
    met = [s for s in observable if s.met]
    unmet = [s for s in observable if not s.met]
    unobservable = [s for s in statuses if s.met is None]

    observable_weight = sum(weight_by_id[s.condition_id] for s in observable)
    met_weight = sum(weight_by_id[s.condition_id] for s in met)
    fulfillment_rate = (met_weight / observable_weight) if observable_weight > 0 else 0.0

    return ScenarioAssessment(
        theme=theme,
        scenario_type=scenario.scenario_type,
        fulfillment_rate=round(fulfillment_rate, 3),
        conditions=statuses,
        unmet=unmet,
        unobservable=unobservable,
    )
