"""validation モジュールのユニットテスト。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.validation.event_study import EventStudy
from src.validation.lag_correlation import LagCorrelationAnalyzer, _bh_correction
from src.validation.ranker import IndicatorRanker, assign_rank

# ------------------------------------------------------------------ fixtures

@pytest.fixture
def dates() -> pd.DatetimeIndex:
    return pd.date_range("2020-01-01", periods=300, freq="B")


@pytest.fixture
def synthetic_leading_indicator(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """XRP価格を30日先行する合成指標(Zスコア列のみ)。"""
    rng = np.random.default_rng(42)
    signal = np.sin(np.linspace(0, 6 * np.pi, 300)) + rng.normal(0, 0.3, 300)
    return pd.DataFrame({"ind_zscore": signal}, index=dates)


@pytest.fixture
def synthetic_forward_returns(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """30日後の合成リターン（指標と正相関）。"""
    rng = np.random.default_rng(42)
    signal = np.sin(np.linspace(0, 6 * np.pi, 300)) + rng.normal(0, 0.3, 300)
    # 30日後のリターンは信号に比例
    fwd30 = pd.Series(np.roll(signal, -30), index=dates, name="asset_fwd30d")
    fwd30.iloc[-30:] = np.nan
    fwd7 = pd.Series(np.roll(signal, -7), index=dates, name="asset_fwd7d")
    fwd7.iloc[-7:] = np.nan
    return pd.DataFrame({"asset_fwd30d": fwd30, "asset_fwd7d": fwd7})


# ------------------------------------------------------------------ BH correction

class TestBHCorrection:
    def test_length_preserved(self) -> None:
        p = [0.01, 0.05, 0.1, 0.5]
        q = _bh_correction(p)
        assert len(q) == len(p)

    def test_all_q_between_0_and_1(self) -> None:
        p = [0.001, 0.01, 0.05, 0.1, 0.5, 0.9]
        q = _bh_correction(p)
        assert all(0.0 <= qi <= 1.0 for qi in q)

    def test_small_p_gets_small_q(self) -> None:
        p = [0.0001, 0.5, 0.8, 0.9]
        q = _bh_correction(p)
        # 最小p値のq値は小さいはず
        assert q[0] < q[-1]

    def test_empty_input(self) -> None:
        assert _bh_correction([]) == []


# ------------------------------------------------------------------ LagCorrelation

class TestLagCorrelationAnalyzer:
    def test_returns_dataframe(
        self,
        synthetic_leading_indicator: pd.DataFrame,
        synthetic_forward_returns: pd.DataFrame,
    ) -> None:
        analyzer = LagCorrelationAnalyzer(lag_days=[7, 30], min_obs=20)
        df = analyzer.compute(
            synthetic_leading_indicator,
            synthetic_forward_returns,
            "test_ind",
            "test_asset",
        )
        assert not df.empty
        assert "spearman_r" in df.columns
        assert "q_spearman" in df.columns

    def test_has_correct_lag_values(
        self,
        synthetic_leading_indicator: pd.DataFrame,
        synthetic_forward_returns: pd.DataFrame,
    ) -> None:
        analyzer = LagCorrelationAnalyzer(lag_days=[7, 30], min_obs=20)
        df = analyzer.compute(
            synthetic_leading_indicator,
            synthetic_forward_returns,
            "test_ind",
            "test_asset",
        )
        assert set(df["lag_days"].unique()).issubset({7, 30})

    def test_correlation_range(
        self,
        synthetic_leading_indicator: pd.DataFrame,
        synthetic_forward_returns: pd.DataFrame,
    ) -> None:
        analyzer = LagCorrelationAnalyzer(lag_days=[30], min_obs=20)
        df = analyzer.compute(
            synthetic_leading_indicator,
            synthetic_forward_returns,
            "test_ind",
            "test_asset",
        )
        assert df["spearman_r"].between(-1.0, 1.0).all()


# ------------------------------------------------------------------ EventStudy

class TestEventStudy:
    def test_basic_event_study(self, dates: pd.DatetimeIndex) -> None:
        rng = np.random.default_rng(0)
        # 非重複窓(horizon=30)で残るよう、40営業日間隔でイベントを配置
        events = dates[::40]
        fwd = pd.DataFrame(
            {"asset_fwd30d": rng.normal(0.05, 0.1, 300)},
            index=dates,
        )
        es = EventStudy(n_bootstrap=100, min_events=5)
        result = es.analyze(events, fwd, "ind", "asset")
        assert not result.empty
        assert "hit_rate" in result.columns
        assert "bs_ci_lo" in result.columns
        assert "n_events_raw" in result.columns

    def test_nonoverlapping_reduces_events(self, dates: pd.DatetimeIndex) -> None:
        # 連続する日付は非重複化で大幅に減るはず
        events = dates[:60]  # 連続60営業日
        selected = EventStudy._select_nonoverlapping(events, horizon=30)
        # 30日間隔なので 60営業日(約84暦日) 中 数イベントに圧縮される
        assert len(selected) < len(events)
        assert len(selected) <= 5

    def test_skips_when_too_few_events(self, dates: pd.DatetimeIndex) -> None:
        events = dates[:3]  # 3イベントのみ
        fwd = pd.DataFrame({"asset_fwd30d": np.ones(300) * 0.01}, index=dates)
        es = EventStudy(min_events=5)
        result = es.analyze(events, fwd, "ind", "asset")
        assert result.empty

    def test_hit_rate_in_range(self, dates: pd.DatetimeIndex) -> None:
        events = dates[:30]
        fwd = pd.DataFrame(
            {"asset_fwd7d": np.where(np.arange(300) < 30, 0.02, -0.01)},
            index=dates,
        )
        es = EventStudy(n_bootstrap=100, min_events=5)
        result = es.analyze(events, fwd, "ind", "asset")
        if not result.empty:
            assert result["hit_rate"].between(0.0, 1.0).all()


# ------------------------------------------------------------------ assign_rank

class TestAssignRank:
    def test_a_plus_conditions(self) -> None:
        rank = assign_rank(corr=0.70, hit_rate=0.75, q_spearman=0.01, n_periods_consistent=3)
        assert rank == "A+"

    def test_a_conditions(self) -> None:
        rank = assign_rank(corr=0.55, hit_rate=0.67, q_spearman=0.02, n_periods_consistent=0)
        assert rank == "A"

    def test_b_conditions(self) -> None:
        rank = assign_rank(corr=0.40, hit_rate=0.62, q_spearman=0.1)
        assert rank == "B"

    def test_c_conditions(self) -> None:
        rank = assign_rank(corr=0.20, hit_rate=0.52, q_spearman=0.5)
        assert rank == "C"

    def test_d_conditions(self) -> None:
        rank = assign_rank(corr=0.05, hit_rate=0.45, q_spearman=0.9)
        assert rank == "D"

    def test_low_n_flag_caps_at_b(self) -> None:
        # A+条件でも low_n_flag=True ならA止まり
        rank = assign_rank(
            corr=0.70, hit_rate=0.75, q_spearman=0.01,
            n_periods_consistent=3, low_n_flag=True
        )
        assert rank in ("A", "B", "C", "D")
        assert rank != "A+"


# ------------------------------------------------------------------ IndicatorRanker

class TestIndicatorRanker:
    def _make_lag_df(
        self, stationary_corr: float, level_corr: float, n_obs: int, lag: int, horizon: int
    ) -> pd.DataFrame:
        """定常(_dz)とレベル(_zscore)の2行を持つ最小lag_corr_dfを作る。"""
        return pd.DataFrame([
            {"indicator": "ind", "target": "asset", "feature": "ind_dz",
             "lag_days": lag, "horizon_days": horizon,
             "spearman_r": stationary_corr, "spearman_p": 0.01,
             "pearson_r": stationary_corr, "pearson_p": 0.01,
             "n_obs": n_obs, "q_spearman": 0.01},
            {"indicator": "ind", "target": "asset", "feature": "ind_zscore",
             "lag_days": lag, "horizon_days": horizon,
             "spearman_r": level_corr, "spearman_p": 0.01,
             "pearson_r": level_corr, "pearson_p": 0.01,
             "n_obs": n_obs, "q_spearman": 0.01},
        ])

    def _make_event_df(self, hit_rate: float, n_events: int, horizon: int) -> pd.DataFrame:
        return pd.DataFrame([{
            "indicator": "ind", "target": "asset", "direction": "above",
            "horizon_days": horizon, "n_events": n_events, "n_events_raw": n_events,
            "low_n_flag": n_events < 10, "mean_return": 0.1, "median_return": 0.1,
            "hit_rate": hit_rate, "max_dd": -0.2, "sharpe": 1.0,
            "t_stat": 2.0, "t_p": 0.03, "bs_ci_lo": 0.01, "bs_ci_hi": 0.2,
            "bs_ci_significant": True,
        }])

    def test_insufficient_history_caps_at_c(self) -> None:
        # 強い相関だが実効N小(120日ホライゾン, n_obs=200 → 実効≈0.9)
        ranker = IndicatorRanker()
        lag_df = self._make_lag_df(0.7, 0.7, n_obs=200, lag=120, horizon=120)
        event_df = self._make_event_df(hit_rate=0.75, n_events=15, horizon=120)
        sc = ranker.build_scorecard(lag_df, event_df, "ind", "asset", "verified", 1.0)
        assert sc.iloc[0]["insufficient_history"]
        assert sc.iloc[0]["rank"] == "C"
        assert not sc.iloc[0]["adopted"]

    def test_trend_confound_flagged_and_not_adopted(self) -> None:
        # レベル相関高・変化相関ゼロ → 見せかけ。見出しは定常相関(低)なので非採用になる。
        ranker = IndicatorRanker()
        lag_df = self._make_lag_df(0.05, 0.8, n_obs=2000, lag=7, horizon=30)
        event_df = self._make_event_df(hit_rate=0.75, n_events=30, horizon=30)
        sc = ranker.build_scorecard(lag_df, event_df, "ind", "asset", "verified", 1.0)
        assert sc.iloc[0]["trend_confound"]
        assert sc.iloc[0]["rank"] in ("C", "D")
        assert not sc.iloc[0]["adopted"]

    def test_legit_signal_survives(self) -> None:
        # 変化相関も高く・実効N十分・的中率高 → B以上で採用
        ranker = IndicatorRanker()
        lag_df = self._make_lag_df(0.45, 0.45, n_obs=2000, lag=7, horizon=30)
        event_df = self._make_event_df(hit_rate=0.66, n_events=30, horizon=30)
        sc = ranker.build_scorecard(lag_df, event_df, "ind", "asset", "verified", 1.0)
        assert not sc.iloc[0]["insufficient_history"]
        assert not sc.iloc[0]["trend_confound"]
        assert sc.iloc[0]["rank"] in ("A+", "A", "B")
        assert sc.iloc[0]["adopted"]
