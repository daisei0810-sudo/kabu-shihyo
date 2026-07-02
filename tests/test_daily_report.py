"""src/reporting/daily_report.py のテスト(主にシグナル相違検知ロジック)。"""

from __future__ import annotations

import pandas as pd

from src.reporting.daily_report import _detect_signal_divergence, _section_portfolio


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


class TestSectionPortfolioDivergenceIntegration:
    def test_divergence_note_appears_when_signals_conflict(self) -> None:
        signals = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "hard_score": None,
            "extended_score": 97.1, "confidence_pct": 1.0, "outlook": "中立-強気",
            "action": "保有継続(監視)",
        }])
        dipsell = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "decision": "売り時候補",
        }])
        lines = _section_portfolio(signals, dipsell)
        text = "\n".join(lines)
        assert "シグナル相違" in text
        assert "フジクラ" in text

    def test_no_divergence_note_when_aligned(self) -> None:
        signals = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "hard_score": None,
            "extended_score": 97.1, "confidence_pct": 1.0, "outlook": "強気",
            "action": "追加",
        }])
        dipsell = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "decision": "押し目候補",
        }])
        lines = _section_portfolio(signals, dipsell)
        text = "\n".join(lines)
        assert "シグナル相違" not in text

    def test_missing_dipsell_df_does_not_crash(self) -> None:
        signals = pd.DataFrame([{
            "target": "fujikura", "name_ja": "フジクラ", "hard_score": None,
            "extended_score": 97.1, "confidence_pct": 1.0, "outlook": "中立-強気",
            "action": "保有継続(監視)",
        }])
        lines = _section_portfolio(signals, None)
        assert len(lines) > 0
