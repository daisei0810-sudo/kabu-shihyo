"""Investment OS Layer10(非公開の投資判断レポート)のテスト。"""

from __future__ import annotations

import pandas as pd

from src.decision.models import ConditionStatus, DecisionRecord, ScenarioAssessment
from src.reporting.decision_report import (
    _fmt_axis,
    _fmt_pct,
    _section_change_log,
    _section_conclusion,
    _section_decisions,
    _section_prediction_accuracy,
    _section_theme_scores,
)


def _make_record(target: str, action: str, scenario: str) -> DecisionRecord:
    assessments = [
        ScenarioAssessment(
            theme="ai_datacenter", scenario_type="bull", fulfillment_rate=0.5,
            conditions=[ConditionStatus(
                condition_id="c1", desc="d1", indicator_key="ind1", measured_value=1.0,
                threshold=0.0, met=True, data_quality="verified", as_of="2026-07-04",
            )],
        ),
        ScenarioAssessment(theme="ai_datacenter", scenario_type="neutral", fulfillment_rate=0.0),
        ScenarioAssessment(theme="ai_datacenter", scenario_type="bear", fulfillment_rate=0.0),
    ]
    return DecisionRecord(
        decision_id=f"dec_2026-07-04_{target}", as_of="2026-07-04", target=target,
        theme="ai_datacenter", action=action, active_scenario=scenario,
        scenario_assessments=assessments, reason="テスト理由", confidence=0.8,
    )


class TestFormatters:
    def test_fmt_pct_none_is_dashes(self) -> None:
        assert _fmt_pct(None) == "--"

    def test_fmt_pct_formats_percentage(self) -> None:
        assert _fmt_pct(0.55) == "55%"

    def test_fmt_axis_nan_is_dashes(self) -> None:
        assert _fmt_axis(float("nan")) == "--"

    def test_fmt_axis_formats_number(self) -> None:
        assert _fmt_axis(88.4) == "88"


class TestSectionThemeScores:
    def test_empty_df_shows_placeholder(self) -> None:
        lines = _section_theme_scores(pd.DataFrame())
        assert any("なし" in line for line in lines)

    def test_renders_row(self) -> None:
        df = pd.DataFrame([{
            "theme": "ai_datacenter", "name_ja": "AIデータセンター",
            "structural_change": None, "supply_demand": 99.0, "earnings": 100.0,
            "valuation": None, "fund_flow": 79.2, "policy_tailwind": None,
            "total": 95.8, "confidence_pct": 0.55,
        }])
        lines = _section_theme_scores(df)
        joined = "\n".join(lines)
        assert "AIデータセンター" in joined
        assert "96" in joined or "95" in joined  # totalの丸め


class TestSectionDecisions:
    def test_no_records_shows_placeholder(self) -> None:
        lines = _section_decisions([])
        assert any("なし" in line for line in lines)

    def test_renders_action_and_scenario(self) -> None:
        rec = _make_record("fujikura", "追加買い", "bull")
        lines = _section_decisions([rec])
        joined = "\n".join(lines)
        assert "fujikura" in joined
        assert "追加買い" in joined
        assert "現在地:強気" in joined

    def test_includes_change_reason_when_set(self) -> None:
        rec = _make_record("fujikura", "売却", "bear")
        rec.change_reason = "投資判断: 保有継続→売却"
        lines = _section_decisions([rec])
        assert any("投資判断: 保有継続→売却" in line for line in lines)


class TestSectionChangeLog:
    def test_no_previous_shows_placeholder(self) -> None:
        lines = _section_change_log([_make_record("fujikura", "保有継続", "neutral")], None)
        assert any("初回実行" in line for line in lines)

    def test_detects_action_change(self) -> None:
        prev = [_make_record("fujikura", "保有継続", "neutral")]
        curr = [_make_record("fujikura", "売却", "bear")]
        lines = _section_change_log(curr, prev)
        joined = "\n".join(lines)
        assert "fujikura" in joined
        assert "action" in joined


class TestSectionPredictionAccuracy:
    def test_empty_df_shows_placeholder(self) -> None:
        lines = _section_prediction_accuracy(pd.DataFrame())
        assert any("なし" in line for line in lines)

    def test_renders_summary_row(self) -> None:
        df = pd.DataFrame([{
            "n_predictions": 12, "n_pending_evaluations": 33, "n_evaluated": 0,
            "n_skipped": 3, "hit_rate": None, "avg_excess_return": None,
            "next_due_date": "2026-10-02",
        }])
        lines = _section_prediction_accuracy(df)
        joined = "\n".join(lines)
        assert "12件" in joined
        assert "2026-10-02" in joined


class TestSectionConclusion:
    def test_empty_records(self) -> None:
        lines = _section_conclusion([])
        assert any("なし" in line for line in lines)

    def test_flags_sell_actions(self) -> None:
        records = [
            _make_record("fujikura", "売却", "bear"),
            _make_record("murata", "保有継続", "neutral"),
        ]
        lines = _section_conclusion(records)
        joined = "\n".join(lines)
        assert "fujikura(売却)" in joined
