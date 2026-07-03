"""Investment OS Layer5(予測台帳)のテスト。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.prediction.evaluator import evaluate_due, summarize
from src.prediction.ledger import build_pending_evaluations, build_predictions
from src.prediction.models import Evaluation, Prediction
from src.prediction.store import (
    load_evaluations,
    load_predictions,
    save_evaluations,
    save_predictions,
    upsert_evaluations,
    upsert_predictions,
)
from src.prediction.taxonomy import ACTION_DIRECTION, PREDICTION_HORIZONS


def _write_price(processed_dir: Path, key: str, dates: pd.DatetimeIndex, closes: list) -> None:
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


# ---------------------------------------------------------------------------
# taxonomy
# ---------------------------------------------------------------------------


class TestTaxonomy:
    def test_prediction_horizons_are_3_6_12_months(self) -> None:
        assert set(PREDICTION_HORIZONS.keys()) == {"3m", "6m", "12m"}
        assert PREDICTION_HORIZONS["3m"] < PREDICTION_HORIZONS["6m"] < PREDICTION_HORIZONS["12m"]

    def test_action_direction_covers_known_actions(self) -> None:
        assert ACTION_DIRECTION["追加"] == 1
        assert ACTION_DIRECTION["保有継続"] == 0
        assert ACTION_DIRECTION["利確検討"] == -1
        assert ACTION_DIRECTION["撤退候補"] == -1


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class TestStore:
    def test_predictions_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "predictions.jsonl"
        p = Prediction(
            prediction_id="pred_2026-07-04_fujikura", created_at="now", as_of="2026-07-04",
            source_layer="portfolio_snapshot", theme="ai_datacenter", target="fujikura",
            judgment="保有継続", expected_direction=0, score_at_prediction=95.0,
            confidence_at_prediction=1.0, baseline_date="2026-07-04", baseline_price=100.0,
        )
        save_predictions([p], str(path))
        loaded = load_predictions(str(path))
        assert len(loaded) == 1
        assert loaded[0].target == "fujikura"

    def test_upsert_predictions_updates_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "predictions.jsonl"
        p = Prediction(
            prediction_id="pred_1", created_at="now", as_of="2026-07-04",
            source_layer="portfolio_snapshot", theme="ai_datacenter", target="fujikura",
            judgment="保有継続", expected_direction=0, score_at_prediction=95.0,
            confidence_at_prediction=1.0, baseline_date="2026-07-04", baseline_price=100.0,
        )
        upsert_predictions([p], str(path))
        p.judgment = "追加"
        merged = upsert_predictions([p], str(path))
        assert len(merged) == 1
        assert merged[0].judgment == "追加"

    def test_evaluations_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.jsonl"
        e = Evaluation(
            evaluation_id="pred_1_3m", prediction_id="pred_1", horizon="3m",
            due_date="2026-10-02",
        )
        save_evaluations([e], str(path))
        loaded = load_evaluations(str(path))
        assert len(loaded) == 1
        assert loaded[0].status == "pending"

    def test_upsert_evaluations_updates_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "evaluations.jsonl"
        e = Evaluation(
            evaluation_id="pred_1_3m", prediction_id="pred_1", horizon="3m",
            due_date="2026-10-02",
        )
        upsert_evaluations([e], str(path))
        e.status = "evaluated"
        e.actual_return = 0.05
        merged = upsert_evaluations([e], str(path))
        assert len(merged) == 1
        assert merged[0].status == "evaluated"


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


class TestLedger:
    def test_build_predictions_excludes_aggregate_rows(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 40)
        df = pd.DataFrame([
            {"target": "fujikura", "action": "保有継続", "extended_score": 95.0,
             "confidence_pct": 1.0},
            {"target": "xrp_real_demand", "action": "", "extended_score": None,
             "confidence_pct": None},
        ])
        preds = build_predictions(df, date(2026, 7, 10), tmp_path)
        assert len(preds) == 1
        assert preds[0].target == "fujikura"

    def test_build_predictions_maps_expected_direction(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 40)
        df = pd.DataFrame([
            {"target": "fujikura", "action": "追加", "extended_score": 95.0,
             "confidence_pct": 1.0},
        ])
        preds = build_predictions(df, date(2026, 7, 10), tmp_path)
        assert preds[0].expected_direction == 1
        assert preds[0].baseline_price == pytest.approx(100.0)

    def test_build_predictions_no_price_leaves_baseline_none(self, tmp_path: Path) -> None:
        df = pd.DataFrame([
            {"target": "spacex", "action": "保有継続", "extended_score": None,
             "confidence_pct": None},
        ])
        preds = build_predictions(df, date(2026, 7, 10), tmp_path)
        assert len(preds) == 1
        assert preds[0].baseline_price is None

    def test_build_pending_evaluations_creates_three_horizons(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 40)
        df = pd.DataFrame([
            {"target": "fujikura", "action": "保有継続", "extended_score": 95.0,
             "confidence_pct": 1.0},
        ])
        preds = build_predictions(df, date(2026, 7, 10), tmp_path)
        evals = build_pending_evaluations(preds, set())
        assert len(evals) == 3
        assert {e.horizon for e in evals} == {"3m", "6m", "12m"}
        assert all(e.status == "pending" for e in evals)

    def test_build_pending_evaluations_skips_when_no_baseline_price(self) -> None:
        pred = Prediction(
            prediction_id="pred_2026-07-10_spacex", created_at="now", as_of="2026-07-10",
            source_layer="portfolio_snapshot", theme="ev_physical_ai", target="spacex",
            judgment="保有継続", expected_direction=0, score_at_prediction=None,
            confidence_at_prediction=None, baseline_date="2026-07-10", baseline_price=None,
        )
        evals = build_pending_evaluations([pred], set())
        assert len(evals) == 3
        assert all(e.status == "skipped_no_data" for e in evals)

    def test_build_pending_evaluations_does_not_duplicate_existing(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 40)
        df = pd.DataFrame([
            {"target": "fujikura", "action": "保有継続", "extended_score": 95.0,
             "confidence_pct": 1.0},
        ])
        preds = build_predictions(df, date(2026, 7, 10), tmp_path)
        existing_ids = {f"{preds[0].prediction_id}_3m"}
        evals = build_pending_evaluations(preds, existing_ids)
        assert len(evals) == 2
        assert "3m" not in {e.horizon for e in evals}


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------


class TestEvaluator:
    def test_evaluate_due_computes_return_and_direction_hit(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=200, freq="D")
        closes = [100.0] * 90 + [110.0] * 110  # due日までに+10%
        _write_price(tmp_path, "fujikura", dates, closes)

        pred = Prediction(
            prediction_id="pred_1", created_at="now", as_of="2026-01-01",
            source_layer="portfolio_snapshot", theme="ai_datacenter", target="fujikura",
            judgment="追加", expected_direction=1, score_at_prediction=95.0,
            confidence_at_prediction=1.0, baseline_date="2026-01-01", baseline_price=100.0,
            benchmark_key=None,
        )
        ev = Evaluation(
            evaluation_id="pred_1_3m", prediction_id="pred_1", horizon="3m",
            due_date="2026-04-01", status="pending",
        )
        updated = evaluate_due(
            [ev], {"pred_1": pred}, today=date(2026, 7, 1), processed_dir=tmp_path
        )
        assert len(updated) == 1
        assert updated[0].status == "evaluated"
        assert updated[0].actual_return == pytest.approx(0.10, abs=0.01)
        assert updated[0].direction_hit is True

    def test_evaluate_due_neutral_judgment_has_no_direction_hit(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=200, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 200)

        pred = Prediction(
            prediction_id="pred_1", created_at="now", as_of="2026-01-01",
            source_layer="portfolio_snapshot", theme="ai_datacenter", target="fujikura",
            judgment="保有継続", expected_direction=0, score_at_prediction=60.0,
            confidence_at_prediction=1.0, baseline_date="2026-01-01", baseline_price=100.0,
        )
        ev = Evaluation(
            evaluation_id="pred_1_3m", prediction_id="pred_1", horizon="3m",
            due_date="2026-04-01", status="pending",
        )
        updated = evaluate_due(
            [ev], {"pred_1": pred}, today=date(2026, 7, 1), processed_dir=tmp_path
        )
        assert updated[0].direction_hit is None

    def test_evaluate_not_due_yet_stays_pending(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 10)
        pred = Prediction(
            prediction_id="pred_1", created_at="now", as_of="2026-01-01",
            source_layer="portfolio_snapshot", theme="ai_datacenter", target="fujikura",
            judgment="保有継続", expected_direction=0, score_at_prediction=60.0,
            confidence_at_prediction=1.0, baseline_date="2026-01-01", baseline_price=100.0,
        )
        ev = Evaluation(
            evaluation_id="pred_1_3m", prediction_id="pred_1", horizon="3m",
            due_date="2026-04-01", status="pending",
        )
        updated = evaluate_due(
            [ev], {"pred_1": pred}, today=date(2026, 1, 5), processed_dir=tmp_path
        )
        assert updated == []

    def test_no_price_data_marks_skipped(self, tmp_path: Path) -> None:
        pred = Prediction(
            prediction_id="pred_1", created_at="now", as_of="2026-01-01",
            source_layer="portfolio_snapshot", theme="ev_physical_ai", target="nonexistent",
            judgment="保有継続", expected_direction=0, score_at_prediction=None,
            confidence_at_prediction=None, baseline_date="2026-01-01", baseline_price=100.0,
        )
        ev = Evaluation(
            evaluation_id="pred_1_3m", prediction_id="pred_1", horizon="3m",
            due_date="2026-04-01", status="pending",
        )
        updated = evaluate_due(
            [ev], {"pred_1": pred}, today=date(2026, 7, 1), processed_dir=tmp_path
        )
        assert len(updated) == 1
        assert updated[0].status == "skipped_no_data"

    def test_summarize_counts_by_status(self) -> None:
        evals = [
            Evaluation(evaluation_id="e1", prediction_id="p1", horizon="3m",
                       due_date="2026-04-01", status="pending"),
            Evaluation(evaluation_id="e2", prediction_id="p1", horizon="6m",
                       due_date="2026-07-01", status="evaluated",
                       excess_return=0.05, direction_hit=True),
            Evaluation(evaluation_id="e3", prediction_id="p1", horizon="12m",
                       due_date="2027-01-01", status="skipped_no_data"),
        ]
        summary = summarize(evals, n_predictions=1)
        assert summary.n_pending_evaluations == 1
        assert summary.n_evaluated == 1
        assert summary.n_skipped == 1
        assert summary.hit_rate == 1.0
        assert summary.avg_excess_return == pytest.approx(0.05)
