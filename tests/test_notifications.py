"""Phase9(通知システム・事後検証DB)のテスト。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from src.notifications.backtest_eval import (
    create_pending_backtests,
    evaluate_due_backtests,
    summarize_backtests,
)
from src.notifications.confidence import compute_change_confidence, material_rank_base_confidence
from src.notifications.decision_history import (
    build_current_snapshot,
    diff_decisions,
    load_previous_decisions,
    snapshot_decisions,
)
from src.notifications.detectors import (
    build_snapshot_context,
    detect_capex,
    detect_collapse,
    detect_decision_changes,
    detect_demand_bubble,
    detect_dip_sell,
    make_notification_id,
)
from src.notifications.models import Backtest, DecisionChange, Notification
from src.notifications.store import (
    load_backtests,
    load_notifications,
    save_backtests,
    save_notifications,
    upsert_backtests,
    upsert_notifications,
)
from src.notifications.suppressor import (
    is_notification_due,
    should_suppress_material_notification,
    should_suppress_score_notification,
)
from src.notifications.taxonomy import TriggerType

# ---------------------------------------------------------------------------
# confidence
# ---------------------------------------------------------------------------


class TestChangeConfidence:
    def test_returns_value_in_0_100(self) -> None:
        v = compute_change_confidence(TriggerType.DIP, 0.8, 20.0)
        assert 0.0 <= v <= 100.0

    def test_higher_data_confidence_increases_value(self) -> None:
        low = compute_change_confidence(TriggerType.DIP, 0.2, 20.0)
        high = compute_change_confidence(TriggerType.DIP, 0.9, 20.0)
        assert high > low

    def test_none_confidence_uses_default(self) -> None:
        v = compute_change_confidence(TriggerType.DIP, None, 20.0)
        assert v > 0.0

    def test_material_rank_ordering(self) -> None:
        assert material_rank_base_confidence("A") > material_rank_base_confidence("B")
        assert material_rank_base_confidence("B") > material_rank_base_confidence("C")
        assert material_rank_base_confidence("C") > material_rank_base_confidence("D")


# ---------------------------------------------------------------------------
# suppressor
# ---------------------------------------------------------------------------


class TestSuppressor:
    def test_score_delta_below_threshold_suppressed(self) -> None:
        suppress, _ = should_suppress_score_notification(3.0)
        assert suppress is True

    def test_score_delta_above_threshold_not_suppressed(self) -> None:
        suppress, _ = should_suppress_score_notification(12.0)
        assert suppress is False

    def test_none_delta_suppressed(self) -> None:
        suppress, _ = should_suppress_score_notification(None)
        assert suppress is True

    def test_notification_due_when_no_prior(self) -> None:
        assert is_notification_due("k1", 80.0, []) is True

    def test_notification_not_due_within_cooldown_no_change(self) -> None:
        prior = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01", confirmed_at="2026-07-01T00:00:00",
            notified_at=datetime.now(UTC).isoformat(), score_current=80.0, status="active",
        )
        assert is_notification_due("k1", 81.0, [prior], today=date.today()) is False

    def test_notification_due_after_cooldown_expires(self) -> None:
        prior = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-06-01",
            confirmed_at="2026-06-01T00:00:00",
            notified_at="2026-06-01T00:00:00", score_current=80.0, status="active",
        )
        assert is_notification_due(
            "k1", 81.0, [prior], cooldown_days=7, today=date(2026, 7, 1)
        ) is True

    def test_notification_due_with_large_delta_even_within_cooldown(self) -> None:
        prior = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01",
            confirmed_at="2026-07-01T00:00:00",
            notified_at=datetime.now(UTC).isoformat(), score_current=80.0, status="active",
        )
        assert is_notification_due("k1", 90.0, [prior], today=date.today()) is True

    def test_material_suppressed_rank_d(self) -> None:
        from src.materials.models import Material

        m = Material(
            material_id="m1", title="x", summary="", source_id="s", source_rank="D",
            published_at=None, first_detected_at="2026-07-01T00:00:00Z",
            created_at="2026-07-01T00:00:00Z", updated_at="2026-07-01T00:00:00Z",
        )
        suppress, reason = should_suppress_material_notification(m)
        assert suppress is True

    def test_material_suppressed_rank_c_alone(self) -> None:
        from src.materials.models import Material

        m = Material(
            material_id="m1", title="x", summary="", source_id="s", source_rank="C",
            published_at=None, first_detected_at="2026-07-01T00:00:00Z",
            created_at="2026-07-01T00:00:00Z", updated_at="2026-07-01T00:00:00Z",
        )
        suppress, _ = should_suppress_material_notification(m)
        assert suppress is True

    def test_material_rank_a_fresh_not_suppressed(self) -> None:
        from src.materials.models import Material

        now = datetime.now(UTC)
        m = Material(
            material_id="m1", title="x", summary="", source_id="s", source_rank="A",
            published_at=now.isoformat(), first_detected_at=now.isoformat(),
            new_fact_flag=True,
            created_at=now.isoformat(), updated_at=now.isoformat(),
        )
        suppress, _ = should_suppress_material_notification(m)
        assert suppress is False


# ---------------------------------------------------------------------------
# decision_history
# ---------------------------------------------------------------------------


class TestDecisionHistory:
    def test_no_prior_history_returns_none(self, tmp_path: Path) -> None:
        assert load_previous_decisions(as_of=date(2026, 7, 2), history_dir=tmp_path) is None

    def test_snapshot_and_load_roundtrip(self, tmp_path: Path) -> None:
        signals = pd.DataFrame([
            {"target": "fujikura", "name_ja": "フジクラ", "outlook": "強気", "action": "追加",
             "extended_score": 80.0, "confidence_pct": 0.9},
        ])
        dipsell = pd.DataFrame([
            {"target": "fujikura", "decision": "押し目候補", "dip_score": 60.0, "sell_score": 10.0},
        ])
        snapshot_decisions(signals, dipsell, as_of=date(2026, 7, 1), history_dir=tmp_path)
        prev = load_previous_decisions(as_of=date(2026, 7, 2), history_dir=tmp_path)
        assert prev is not None
        assert prev.iloc[0]["outlook"] == "強気"

    def test_diff_decisions_empty_when_no_prev(self) -> None:
        curr = build_current_snapshot(
            pd.DataFrame([{"target": "a", "name_ja": "A", "outlook": "強気", "action": "追加",
                           "extended_score": 80.0, "confidence_pct": 0.9}]),
            pd.DataFrame(),
        )
        assert diff_decisions(None, curr) == []

    def test_diff_decisions_detects_outlook_change(self) -> None:
        prev = pd.DataFrame([
            {"target": "a", "name_ja": "A", "outlook": "強気", "action": "追加",
             "extended_score": 80.0, "confidence_pct": 0.9,
             "dip_decision": None, "dip_score": None, "sell_score": None},
        ])
        curr = pd.DataFrame([
            {"target": "a", "name_ja": "A", "outlook": "弱気", "action": "利確検討",
             "extended_score": 40.0, "confidence_pct": 0.9,
             "dip_decision": None, "dip_score": None, "sell_score": None},
        ])
        changes = diff_decisions(prev, curr)
        fields = {c.field for c in changes}
        assert "outlook" in fields
        assert "action" in fields

    def test_diff_decisions_no_change_returns_empty(self) -> None:
        row = {"target": "a", "name_ja": "A", "outlook": "強気", "action": "追加",
               "extended_score": 80.0, "confidence_pct": 0.9,
               "dip_decision": None, "dip_score": None, "sell_score": None}
        prev = pd.DataFrame([row])
        curr = pd.DataFrame([row])
        assert diff_decisions(prev, curr) == []


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------


class TestMakeNotificationId:
    def test_deterministic(self) -> None:
        id1 = make_notification_id("dip", "fujikura", "c17_02_dip", "2026-07-01")
        id2 = make_notification_id("dip", "fujikura", "c17_02_dip", "2026-07-01")
        assert id1 == id2

    def test_different_inputs_differ(self) -> None:
        id1 = make_notification_id("dip", "fujikura", "c17_02_dip", "2026-07-01")
        id2 = make_notification_id("dip", "rorze", "c17_02_dip", "2026-07-01")
        assert id1 != id2


class TestDetectDipSell:
    def _ctx(self) -> object:
        return build_snapshot_context(None, None, None, "2026-07-01")

    def test_no_data_returns_empty(self) -> None:
        result = detect_dip_sell(pd.DataFrame(), None, self._ctx(), [])
        assert result == []

    def test_dip_above_threshold_triggers(self) -> None:
        df = pd.DataFrame([
            {"target": "fujikura", "name_ja": "フジクラ", "dip_score": 80.0, "sell_score": 10.0,
             "hold_score": 20.0, "decision": "強い押し目", "recommended_action": "買い増し",
             "provisional": True},
        ])
        result = detect_dip_sell(df, None, self._ctx(), [])
        assert len(result) == 1
        assert result[0].trigger_type == "dip"
        assert result[0].target == "fujikura"

    def test_below_threshold_no_trigger(self) -> None:
        df = pd.DataFrame([
            {"target": "fujikura", "name_ja": "フジクラ", "dip_score": 40.0, "sell_score": 10.0,
             "hold_score": 50.0, "decision": "保有継続", "recommended_action": "様子見",
             "provisional": True},
        ])
        result = detect_dip_sell(df, None, self._ctx(), [])
        assert result == []

    def test_cooldown_suppresses_repeat(self) -> None:
        df = pd.DataFrame([
            {"target": "fujikura", "name_ja": "フジクラ", "dip_score": 80.0, "sell_score": 10.0,
             "hold_score": 20.0, "decision": "強い押し目", "recommended_action": "買い増し",
             "provisional": True},
        ])
        ctx = self._ctx()
        first = detect_dip_sell(df, None, ctx, [])
        assert len(first) == 1
        first[0].notified_at = datetime.now(UTC).isoformat()
        second = detect_dip_sell(df, None, ctx, first)
        assert second == []


class TestDetectDemandBubble:
    def test_small_change_not_triggered(self) -> None:
        df = pd.DataFrame([
            {"label": "real_demand_index", "score": 60.0, "change_1d": 2.0,
             "confidence_pct": 0.7},
        ])
        ctx = build_snapshot_context(df, None, None, "2026-07-01")
        result = detect_demand_bubble(df, ctx)
        assert result == []

    def test_large_change_triggers(self) -> None:
        df = pd.DataFrame([
            {"label": "real_demand_index", "score": 70.0, "change_1d": 15.0,
             "confidence_pct": 0.7},
        ])
        ctx = build_snapshot_context(df, None, None, "2026-07-01")
        result = detect_demand_bubble(df, ctx)
        assert len(result) == 1
        assert result[0].trigger_type == "demand_index"

    def test_no_history_no_change_1d_not_triggered(self) -> None:
        df = pd.DataFrame([
            {"label": "ai_bubble_score", "score": 70.0, "change_1d": None, "confidence_pct": 0.7},
        ])
        ctx = build_snapshot_context(df, None, None, "2026-07-01")
        assert detect_demand_bubble(df, ctx) == []


class TestDetectCollapse:
    def test_no_prior_level_no_notification(self) -> None:
        ctx = build_snapshot_context(None, None, 2, "2026-07-01")
        assert detect_collapse(2, None, ctx) == []

    def test_level_increase_triggers(self) -> None:
        ctx = build_snapshot_context(None, None, 3, "2026-07-01")
        result = detect_collapse(3, 1.0, ctx)
        assert len(result) == 1
        assert result[0].trigger_type == "collapse"

    def test_level_same_or_decrease_no_trigger(self) -> None:
        ctx = build_snapshot_context(None, None, 1, "2026-07-01")
        assert detect_collapse(1, 2.0, ctx) == []
        assert detect_collapse(1, 1.0, ctx) == []


class TestDetectDecisionChanges:
    def test_changes_become_notifications(self) -> None:
        changes = [DecisionChange(
            target="fujikura", name_ja="フジクラ", field="outlook",
            prev_value="強気", curr_value="弱気", prev_score=80.0, curr_score=40.0,
        )]
        ctx = build_snapshot_context(None, None, None, "2026-07-01")
        result = detect_decision_changes(changes, ctx)
        assert len(result) == 1
        assert result[0].target == "fujikura"
        assert result[0].prev_judgment == "強気"

    def test_empty_changes_no_notifications(self) -> None:
        ctx = build_snapshot_context(None, None, None, "2026-07-01")
        assert detect_decision_changes([], ctx) == []


class TestDetectCapex:
    def test_no_data_returns_empty(self, tmp_path: Path) -> None:
        ctx = build_snapshot_context(None, None, None, "2026-07-01")
        assert detect_capex(tmp_path, ctx, []) == []

    def test_large_qoq_change_triggers(self, tmp_path: Path) -> None:
        dates = pd.date_range("2025-01-01", periods=5, freq="QS")
        df = pd.DataFrame({"hyperscaler_capex_total": [100, 110, 120, 100, 150]}, index=dates)
        df.to_parquet(tmp_path / "capex_hyperscaler_total.parquet")
        ctx = build_snapshot_context(None, None, None, "2026-07-01")
        result = detect_capex(tmp_path, ctx, [])
        assert len(result) == 1
        assert result[0].trigger_type == "capex"


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class TestStore:
    def test_notifications_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "notifications.jsonl"
        n = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
        )
        save_notifications([n], str(path))
        loaded = load_notifications(str(path))
        assert len(loaded) == 1
        assert loaded[0].notification_id == "n1"

    def test_upsert_merges_without_duplicating(self, tmp_path: Path) -> None:
        path = tmp_path / "notifications.jsonl"
        n1 = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
        )
        upsert_notifications([n1], str(path))
        n2 = Notification(
            notification_id="n2", trigger_type="sell", condition_id="c2",
            dedup_key="k2", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
        )
        merged = upsert_notifications([n2], str(path))
        assert len(merged) == 2

    def test_jsonl_sorted_deterministically(self, tmp_path: Path) -> None:
        path = tmp_path / "notifications.jsonl"
        n_z = Notification(
            notification_id="ntf_z", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
        )
        n_a = Notification(
            notification_id="ntf_a", trigger_type="dip", condition_id="c1",
            dedup_key="k2", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
        )
        save_notifications([n_z, n_a], str(path))
        lines = path.read_text(encoding="utf-8").splitlines()
        ids = [
            line.split('"notification_id":"')[1].split('"')[0] for line in lines
        ]
        assert ids == sorted(ids)

    def test_backtests_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "backtests.jsonl"
        b = Backtest(
            backtest_id="b1", notification_id="n1", horizon="1w",
            baseline_date="2026-07-01", eval_due_date="2026-07-08",
        )
        save_backtests([b], str(path))
        loaded = load_backtests(str(path))
        assert len(loaded) == 1
        assert loaded[0].status == "pending"

    def test_upsert_backtests_updates_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "backtests.jsonl"
        b = Backtest(
            backtest_id="b1", notification_id="n1", horizon="1w",
            baseline_date="2026-07-01", eval_due_date="2026-07-08",
        )
        upsert_backtests([b], str(path))
        b.status = "evaluated"
        b.actual_return = 0.05
        merged = upsert_backtests([b], str(path))
        assert len(merged) == 1
        assert merged[0].status == "evaluated"


# ---------------------------------------------------------------------------
# backtest_eval
# ---------------------------------------------------------------------------


def _write_price(processed_dir: Path, key: str, dates: pd.DatetimeIndex, closes: list) -> None:
    df = pd.DataFrame({"Close": closes}, index=dates)
    df.to_parquet(processed_dir / f"price_{key}.parquet")


class TestBacktestEval:
    def test_create_pending_backtests_skips_targetless(self, tmp_path: Path) -> None:
        n = Notification(
            notification_id="n1", trigger_type="demand_index", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
            target=None,
        )
        result = create_pending_backtests([n], [], tmp_path)
        assert result == []

    def test_create_pending_backtests_creates_three_horizons(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=32, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 32)
        n = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-07-01", confirmed_at="now", notified_at="now",
            target="fujikura",
        )
        result = create_pending_backtests([n], [], tmp_path)
        assert len(result) == 3
        horizons = {b.horizon for b in result}
        assert horizons == {"1w", "1m", "3m"}

    def test_evaluate_due_backtest_computes_return(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=40, freq="D")
        closes = [100.0] * 5 + [110.0] * 35  # eval_due_date(day7)までに10%上昇済み
        _write_price(tmp_path, "fujikura", dates, closes)

        bt = Backtest(
            backtest_id="n1_1w", notification_id="n1", ticker="fujikura", horizon="1w",
            baseline_date="2026-06-01", baseline_price=100.0, eval_due_date="2026-06-08",
            status="pending",
        )
        n = Notification(
            notification_id="n1", trigger_type="dip", condition_id="c1",
            dedup_key="k1", info_as_of="2026-06-01", confirmed_at="now", notified_at="now",
            target="fujikura",
        )
        updated = evaluate_due_backtests(
            [bt], {"n1": n}, today=date(2026, 7, 15), processed_dir=tmp_path
        )
        assert len(updated) == 1
        assert updated[0].status == "evaluated"
        assert updated[0].actual_return == pytest.approx(0.10, abs=0.01)

    def test_evaluate_not_due_yet_stays_pending(self, tmp_path: Path) -> None:
        dates = pd.date_range("2026-06-01", periods=5, freq="D")
        _write_price(tmp_path, "fujikura", dates, [100.0] * 5)
        bt = Backtest(
            backtest_id="n1_1w", notification_id="n1", ticker="fujikura", horizon="1w",
            baseline_date="2026-06-01", baseline_price=100.0, eval_due_date="2026-06-08",
            status="pending",
        )
        updated = evaluate_due_backtests([bt], {}, today=date(2026, 6, 2), processed_dir=tmp_path)
        assert updated == []

    def test_no_price_data_marks_skipped(self, tmp_path: Path) -> None:
        bt = Backtest(
            backtest_id="n1_1w", notification_id="n1", ticker="nonexistent", horizon="1w",
            baseline_date="2026-06-01", baseline_price=100.0, eval_due_date="2026-06-08",
            status="pending",
        )
        updated = evaluate_due_backtests([bt], {}, today=date(2026, 7, 1), processed_dir=tmp_path)
        assert len(updated) == 1
        assert updated[0].status == "skipped_no_data"

    def test_summarize_counts_by_status(self) -> None:
        backtests = [
            Backtest("b1", "n1", "1w", "2026-06-01", "2026-06-08", status="pending"),
            Backtest("b2", "n1", "1m", "2026-06-01", "2026-07-01", status="evaluated",
                      excess_return=0.02, false_positive_flag=False),
            Backtest("b3", "n2", "1w", "2026-06-01", "2026-06-08", status="skipped_no_data"),
        ]
        summary = summarize_backtests(backtests)
        assert summary.n_pending == 1
        assert summary.n_evaluated == 1
        assert summary.n_skipped == 1
        assert summary.avg_excess_return == pytest.approx(0.02)
