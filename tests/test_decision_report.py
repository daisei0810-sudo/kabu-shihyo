"""Investment OS Layer10(非公開の投資判断レポート)のテスト。"""

from __future__ import annotations

import pandas as pd

from src.decision.models import ConditionStatus, DecisionRecord, ScenarioAssessment
from src.notifications.models import Notification
from src.reporting.decision_report import (
    _detect_signal_divergence,
    _fmt_axis,
    _fmt_pct,
    _section_allocation,
    _section_change_log,
    _section_conclusion,
    _section_discovery,
    _section_holdings_detail,
    _section_prediction_accuracy,
    _section_risk,
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


def _make_notification(target: str, change_reason: str) -> Notification:
    return Notification(
        notification_id="n1", trigger_type="decision_change", condition_id="c1",
        dedup_key="d1", info_as_of="2026-07-04", confirmed_at="2026-07-04T00:00:00",
        notified_at="2026-07-04T00:00:00", target=target, change_reason=change_reason,
    )


class TestSectionHoldingsDetail:
    """保有銘柄ごとに根拠を集約する章(P3再設計)。旧_section_decisions/
    _section_portfolio_signals/_section_technicals/_section_dip_sellを統合。"""

    def test_no_records_shows_placeholder(self) -> None:
        lines = _section_holdings_detail(
            [], pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), [],
        )
        assert any("なし" in line for line in lines)

    def test_renders_action_and_scenario_without_aux_data(self) -> None:
        rec = _make_record("fujikura", "追加買い", "bull")
        lines = _section_holdings_detail(
            [rec], pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), [],
        )
        joined = "\n".join(lines)
        assert "fujikura" in joined
        assert "追加買い" in joined
        assert "現在地:強気" in joined
        assert "検知なし" in joined  # リスクデータなしは「検知なし」と明示

    def test_includes_change_reason_when_set(self) -> None:
        rec = _make_record("fujikura", "売却", "bear")
        rec.change_reason = "投資判断: 保有継続→売却"
        lines = _section_holdings_detail(
            [rec], pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(), [],
        )
        assert any("投資判断: 保有継続→売却" in line for line in lines)

    def test_consolidates_all_sources_with_condition_text_and_divergence(self) -> None:
        active_assessment = ScenarioAssessment(
            theme="ai_datacenter", scenario_type="bear", fulfillment_rate=0.33,
            conditions=[
                ConditionStatus(
                    condition_id="c1", desc="risk:capex_cut悪化", indicator_key="risk:capex_cut",
                    measured_value=65.0, threshold=60.0, met=True,
                    data_quality="verified", as_of="2026-07-04",
                ),
                ConditionStatus(
                    condition_id="c2", desc="nvidia_revenue勢い<0", indicator_key="nvidia_revenue",
                    measured_value=None, threshold=0.0, met=None,
                    data_quality="unavailable", as_of="2026-07-04",
                ),
            ],
            unmet=[],
            unobservable=[
                ConditionStatus(
                    condition_id="c2", desc="nvidia_revenue勢い<0", indicator_key="nvidia_revenue",
                    measured_value=None, threshold=0.0, met=None,
                    data_quality="unavailable", as_of="2026-07-04",
                ),
            ],
        )
        rec = DecisionRecord(
            decision_id="dec_2026-07-04_fujikura", as_of="2026-07-04", target="fujikura",
            theme="ai_datacenter", action="一部利確", active_scenario="bear",
            scenario_assessments=[active_assessment], reason="テスト理由", confidence=0.8,
        )
        theme_df = pd.DataFrame([{
            "theme": "ai_datacenter", "name_ja": "AIデータセンター",
            "structural_change": None, "supply_demand": 99.0, "earnings": 100.0,
            "valuation": None, "fund_flow": 79.2, "policy_tailwind": None,
            "total": 96.0, "confidence_pct": 0.55,
        }])
        signals_df = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "hard_score": None,
            "extended_score": 97.0, "confidence_pct": 1.0, "outlook": "中立-強気",
        }])
        tech_df = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "rsi": 49.0,
            "ma25_dev": 3.9, "ma200_dev": 36.9, "tech_outlook": "過熱警戒",
        }])
        ds_df = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "dip_score": 20.0,
            "sell_score": 75.0, "hold_score": 25.0, "decision": "売り時候補",
        }])
        risk_df = pd.DataFrame([{
            "theme": "ai_datacenter", "target": "fujikura", "category": "capex_cut",
            "risk_score": 65.0, "deteriorated": True, "evidence": "CAPEX前年比-12%",
            "data_quality": "verified", "as_of": "2026-07-04",
        }])
        notifs = [_make_notification("fujikura", "sell_score=75到達")]

        lines = _section_holdings_detail(
            [rec], theme_df, signals_df, tech_df, ds_df, risk_df, notifs,
        )
        joined = "\n".join(lines)

        assert "フジクラ" in joined  # signals_dfのname_jaで名寄せされる
        assert "96" in joined  # テーマスコア
        assert "97" in joined  # Extendedスコア
        assert "RSI49" in joined
        assert "sell75" in joined
        assert "CAPEX前年比-12%" in joined  # リスク根拠がインラインで見える
        assert "逆方向" in joined  # 中立-強気 x 売り時候補のシグナル相違警告
        assert "⭐成立" in joined  # 成立条件の中身(従来は件数のみで非表示だった)
        assert "risk:capex_cut悪化" in joined
        assert "❓観測不能" in joined
        assert "sell_score=75到達" in joined  # 銘柄別通知


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


# ---------------------------------------------------------------------------
# ポートフォリオシグナル(daily_report.pyから移設。①B: 非公開化に伴う移設)
# ---------------------------------------------------------------------------


class TestDetectSignalDivergence:
    def test_bullish_outlook_with_bearish_dip_flags_divergence(self) -> None:
        warning = _detect_signal_divergence("中立-強気", "売り時候補")
        assert warning is not None
        assert "中立-強気" in warning
        assert "売り時候補" in warning

    def test_strong_bullish_with_overheat_flags_divergence(self) -> None:
        warning = _detect_signal_divergence("強気", "過熱警戒")
        assert warning is not None

    def test_bearish_outlook_with_bullish_dip_flags_divergence(self) -> None:
        warning = _detect_signal_divergence("弱気", "強い押し目")
        assert warning is not None

    def test_aligned_bullish_no_divergence(self) -> None:
        assert _detect_signal_divergence("強気", "押し目候補") is None

    def test_neutral_outlook_no_divergence(self) -> None:
        assert _detect_signal_divergence("中立", "売り時候補") is None

    def test_no_dip_decision_no_divergence(self) -> None:
        assert _detect_signal_divergence("強気", None) is None
        assert _detect_signal_divergence("強気", "") is None

    def test_hold_decision_no_divergence(self) -> None:
        assert _detect_signal_divergence("中立-強気", "保有継続") is None


# ---------------------------------------------------------------------------
# risk (Layer6)
# ---------------------------------------------------------------------------


class TestSectionRisk:
    def test_empty_df_shows_placeholder(self) -> None:
        lines = _section_risk(pd.DataFrame())
        assert any("なし" in line for line in lines)

    def test_no_deterioration_shows_placeholder(self) -> None:
        df = pd.DataFrame([{
            "theme": "ai_datacenter", "target": "fujikura", "category": "capex_cut",
            "risk_score": 0.0, "deteriorated": False, "evidence": "e",
            "data_quality": "verified", "as_of": "2026-07-06",
        }])
        lines = _section_risk(df)
        assert any("悪化しているカテゴリはありません" in line for line in lines)

    def test_deteriorated_row_rendered(self) -> None:
        df = pd.DataFrame([{
            "theme": "robotics_fa", "target": "fanuc", "category": "competition_loss",
            "risk_score": 75.0, "deteriorated": True, "evidence": "対ピア劣後",
            "data_quality": "proxy", "as_of": "2026-07-06",
        }])
        lines = _section_risk(df)
        joined = "\n".join(lines)
        assert "fanuc" in joined
        assert "競合劣後" in joined


# ---------------------------------------------------------------------------
# allocation (Layer9) / discovery (Layer7-8)
# ---------------------------------------------------------------------------


class TestSectionAllocation:
    def test_empty_df_shows_placeholder(self) -> None:
        lines = _section_allocation(pd.DataFrame())
        assert any("なし" in line for line in lines)

    def test_renders_row_with_missing_current_pct(self) -> None:
        df = pd.DataFrame([{
            "theme": "ai_datacenter", "theme_score": 90.0, "risk_haircut": 0.0,
            "recommended_pct": 25.0, "current_pct": None, "diff_pct": None,
            "rationale": "テーマスコア90", "confidence": 0.8, "as_of": "2026-07-06",
        }])
        lines = _section_allocation(df)
        joined = "\n".join(lines)
        assert "ai_datacenter" in joined
        assert "未入力" in joined


class TestSectionDiscovery:
    def test_empty_dfs_show_placeholder(self) -> None:
        lines = _section_discovery(pd.DataFrame(), pd.DataFrame())
        assert any("--step 12 未実行" in line for line in lines)

    def test_renders_company_and_theme_rows(self) -> None:
        companies_df = pd.DataFrame([{
            "rank": 1, "company": "nvidia", "name_ja": "NVIDIA", "theme": "ai_datacenter",
            "expected_value": 96.0, "relative_momentum": 3.2, "thesis": "テーマスコア96",
        }])
        themes_df = pd.DataFrame([{
            "theme": "bio", "name_ja": "バイオ", "materials_trend_note": "関連材料0件",
            "data_quality": "unavailable",
        }])
        lines = _section_discovery(companies_df, themes_df)
        joined = "\n".join(lines)
        assert "NVIDIA" in joined
        assert "バイオ" in joined
