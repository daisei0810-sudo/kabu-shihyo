"""Investment OS Layer2(意思決定エンジン)のテスト。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.decision.assessment import assess_scenario
from src.decision.conditions import evaluate_condition, latest_feature_value
from src.decision.diff import attach_change_context, diff
from src.decision.engine import decide
from src.decision.models import ConditionDef, DecisionRecord, Scenario
from src.decision.scenarios import load_scenarios
from src.decision.store import load_decisions, load_previous, save_decisions
from src.decision.taxonomy import LEGACY_ACTION_TO_L2


def _write_price(processed_dir: Path, key: str, dates: pd.DatetimeIndex, closes: list) -> None:
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


# ---------------------------------------------------------------------------
# conditions
# ---------------------------------------------------------------------------


class TestConditions:
    def test_latest_feature_value_level(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2026-01-01", periods=3))
        assert latest_feature_value(s, "level") == pytest.approx(3.0)

    def test_latest_feature_value_unknown_feature_is_none(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2026-01-01", periods=3))
        assert latest_feature_value(s, "nonexistent") is None

    def test_evaluate_condition_unknown_indicator_is_unobservable(self, tmp_path: Path) -> None:
        cond = ConditionDef(
            condition_id="c1", desc="test", indicator="nonexistent_indicator",
            feature="level", op=">", threshold=0.0,
        )
        status = evaluate_condition(cond, "fujikura", date(2026, 7, 4), tmp_path)
        assert status.met is None

    def test_evaluate_condition_known_indicator_no_data_is_unobservable(
        self, tmp_path: Path
    ) -> None:
        cond = ConditionDef(
            condition_id="c1", desc="test", indicator="xrp_price",
            feature="level", op=">", threshold=0.0,
        )
        status = evaluate_condition(cond, "xrp", date(2026, 7, 4), tmp_path)
        assert status.met is None
        assert status.data_quality == "verified"


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------


class TestAssessScenario:
    def test_no_conditions_gives_zero_fulfillment(self, tmp_path: Path) -> None:
        scenario = Scenario("bull", [])
        result = assess_scenario("ai_datacenter", scenario, "fujikura", date(2026, 7, 4), tmp_path)
        assert result.fulfillment_rate == 0.0
        assert result.unmet == []
        assert result.unobservable == []

    def test_unobservable_condition_excluded_from_rate(self, tmp_path: Path) -> None:
        scenario = Scenario("bull", [
            ConditionDef("c1", "desc", "nonexistent_indicator", "level", ">", 0.0, weight=1.0),
        ])
        result = assess_scenario("ai_datacenter", scenario, "fujikura", date(2026, 7, 4), tmp_path)
        assert result.fulfillment_rate == 0.0
        assert len(result.unobservable) == 1
        assert result.unmet == []


# ---------------------------------------------------------------------------
# scenarios (YAML loader)
# ---------------------------------------------------------------------------


class TestScenariosLoader:
    def test_load_scenarios_roundtrip(self, tmp_path: Path) -> None:
        doc = {
            "theme": "test_theme",
            "scenarios": {
                "bull": {"conditions": [
                    {"id": "c1", "desc": "d1", "indicator": "xrp_price",
                     "feature": "dz", "op": ">", "threshold": 0.0, "weight": 1.0},
                ]},
                "neutral": {"conditions": []},
                "bear": {"conditions": []},
            },
        }
        path = tmp_path / "test_theme.yaml"
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(doc, f, allow_unicode=True)

        ts = load_scenarios("test_theme", tmp_path)
        assert ts is not None
        assert ts.theme == "test_theme"
        assert len(ts.bull.conditions) == 1
        assert ts.bull.conditions[0].indicator == "xrp_price"
        assert len(ts.neutral.conditions) == 0

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_scenarios("nonexistent_theme", tmp_path) is None


# ---------------------------------------------------------------------------
# taxonomy
# ---------------------------------------------------------------------------


class TestTaxonomy:
    def test_legacy_action_mapping_covers_all_portfolio_actions(self) -> None:
        for legacy in ("追加", "保有継続(監視)", "保有継続", "利確検討", "撤退候補", "要確認"):
            assert legacy in LEGACY_ACTION_TO_L2
        assert LEGACY_ACTION_TO_L2["追加"] == "追加買い"
        assert LEGACY_ACTION_TO_L2["撤退候補"] == "売却"


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class TestDecideEngine:
    def test_empty_signals_returns_empty(self, tmp_path: Path) -> None:
        records = decide(
            as_of=date(2026, 7, 4),
            signals_path=tmp_path / "nonexistent.csv",
            theme_scores_path=tmp_path / "nonexistent2.csv",
            scenarios_dir=tmp_path / "scenarios",
            processed_dir=tmp_path,
        )
        assert records == []

    def test_decides_for_held_instrument_without_scenario_file(self, tmp_path: Path) -> None:
        signals_path = tmp_path / "portfolio_signal_scores.csv"
        pd.DataFrame([
            {"target": "fujikura", "name_ja": "フジクラ", "layer": "ai_datacenter",
             "extended_score": 95.0, "confidence_pct": 1.0, "outlook": "強気",
             "action": "追加", "signal_note": "スコア高(95)"},
        ]).to_csv(signals_path, index=False)

        records = decide(
            as_of=date(2026, 7, 4),
            signals_path=signals_path,
            theme_scores_path=tmp_path / "nonexistent.csv",
            scenarios_dir=tmp_path / "scenarios",   # 空ディレクトリ = シナリオ未整備
            processed_dir=tmp_path,
        )
        assert len(records) == 1
        rec = records[0]
        assert rec.target == "fujikura"
        assert rec.action == "追加買い"    # LEGACY_ACTION_TO_L2経由の変換
        assert rec.active_scenario == "neutral"
        assert "シナリオ未整備" in rec.reason

    def test_excludes_aggregate_rows_and_empty_action(self, tmp_path: Path) -> None:
        signals_path = tmp_path / "portfolio_signal_scores.csv"
        pd.DataFrame([
            {"target": "xrp_real_demand", "name_ja": "XRP総合実需スコア", "layer": "crypto_xrp",
             "extended_score": 57.3, "confidence_pct": 0.5, "outlook": "", "action": "",
             "signal_note": ""},
        ]).to_csv(signals_path, index=False)

        records = decide(
            as_of=date(2026, 7, 4),
            signals_path=signals_path,
            theme_scores_path=tmp_path / "nonexistent.csv",
            scenarios_dir=tmp_path / "scenarios",
            processed_dir=tmp_path,
        )
        assert records == []


# ---------------------------------------------------------------------------
# store / diff
# ---------------------------------------------------------------------------


class TestStoreAndDiff:
    def _make_record(self, target: str, action: str, scenario: str) -> DecisionRecord:
        return DecisionRecord(
            decision_id=f"dec_2026-07-04_{target}", as_of="2026-07-04", target=target,
            theme="ai_datacenter", action=action, active_scenario=scenario,
        )

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        records = [self._make_record("fujikura", "保有継続", "neutral")]
        save_decisions(records, date(2026, 7, 4), tmp_path)
        loaded = load_decisions(date(2026, 7, 4), tmp_path)
        assert len(loaded) == 1
        assert loaded[0].target == "fujikura"
        assert loaded[0].action == "保有継続"

    def test_load_previous_finds_earlier_date(self, tmp_path: Path) -> None:
        records = [self._make_record("fujikura", "保有継続", "neutral")]
        save_decisions(records, date(2026, 7, 3), tmp_path)
        prev = load_previous(date(2026, 7, 4), tmp_path)
        assert prev is not None
        assert len(prev) == 1

    def test_load_previous_no_history_returns_none(self, tmp_path: Path) -> None:
        assert load_previous(date(2026, 7, 4), tmp_path) is None

    def test_diff_detects_action_change(self) -> None:
        prev = [self._make_record("fujikura", "保有継続", "neutral")]
        curr = [self._make_record("fujikura", "売却", "bear")]
        changes = diff(prev, curr)
        fields = {c.field for c in changes}
        assert "action" in fields
        assert "active_scenario" in fields

    def test_diff_no_change_when_identical(self) -> None:
        prev = [self._make_record("fujikura", "保有継続", "neutral")]
        curr = [self._make_record("fujikura", "保有継続", "neutral")]
        assert diff(prev, curr) == []

    def test_attach_change_context_first_run_has_no_change_reason(self) -> None:
        curr = [self._make_record("fujikura", "保有継続", "neutral")]
        result = attach_change_context(curr, None)
        assert result[0].change_reason is None
        assert result[0].prev_decision_id is None

    def test_attach_change_context_sets_reason_on_change(self) -> None:
        prev = [self._make_record("fujikura", "保有継続", "neutral")]
        curr = [self._make_record("fujikura", "売却", "bear")]
        result = attach_change_context(curr, prev)
        assert result[0].change_reason is not None
        assert result[0].prev_decision_id == "dec_2026-07-04_fujikura"

    def test_attach_change_context_no_reason_when_unchanged(self) -> None:
        prev = [self._make_record("fujikura", "保有継続", "neutral")]
        curr = [self._make_record("fujikura", "保有継続", "neutral")]
        result = attach_change_context(curr, prev)
        assert result[0].change_reason is None
