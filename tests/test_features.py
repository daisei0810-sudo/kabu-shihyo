"""特徴量エンジニアリングのユニットテスト。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.engineer import FeatureEngineer
from src.features.events import ThresholdEventDetector


@pytest.fixture
def price_series() -> pd.Series:
    dates = pd.date_range("2018-01-01", periods=500, freq="B")
    rng = np.random.default_rng(0)
    prices = 100 * np.exp(rng.normal(0, 0.01, 500).cumsum())
    return pd.Series(prices, index=dates, name="test_asset")


@pytest.fixture
def eng() -> FeatureEngineer:
    return FeatureEngineer(zscore_window=60, ma_windows=[10, 30])


class TestFeatureEngineer:
    def test_indicator_features_has_expected_columns(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        feats = eng.build_indicator_features(price_series, "test")
        assert "test_zscore" in feats.columns
        assert "test_mom21" in feats.columns
        assert "test_yoy252" in feats.columns
        assert "test_ma10" in feats.columns
        assert "test_ma30" in feats.columns
        assert "test_low_conf" in feats.columns

    def test_zscore_mean_zero_long_series(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        feats = eng.build_indicator_features(price_series, "test")
        z = feats["test_zscore"].dropna()
        # rolling Zスコアは平均がほぼ0に近い
        assert abs(z.mean()) < 0.5

    def test_zscore_std_approx_one(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        feats = eng.build_indicator_features(price_series, "test")
        z = feats["test_zscore"].dropna()
        assert 0.5 < z.std() < 2.0

    def test_mom21_first_values_nan(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        feats = eng.build_indicator_features(price_series, "test")
        assert feats["test_mom21"].iloc[:21].isna().all()

    def test_forward_returns_shape(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        fwd = eng.build_forward_returns(price_series, "test", horizons=[7, 30])
        assert "test_fwd7d" in fwd.columns
        assert "test_fwd30d" in fwd.columns
        assert len(fwd) == len(price_series)

    def test_forward_returns_last_rows_nan(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        fwd = eng.build_forward_returns(price_series, "test", horizons=[30])
        # 末尾30行はNaNのはず
        assert fwd["test_fwd30d"].iloc[-30:].isna().all()

    def test_align_reduces_rows(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        feats = eng.build_indicator_features(price_series, "test")
        fwd = eng.build_forward_returns(price_series, "test", horizons=[30])
        X, y = eng.align(feats, fwd, lag=14)
        assert len(X) > 0
        assert len(X) == len(y)
        assert len(X) < len(price_series)

    def test_no_lookahead_in_forward_returns(
        self, eng: FeatureEngineer, price_series: pd.Series
    ) -> None:
        """将来リターンは必ず t 以降の情報を使うことを確認。"""
        fwd = eng.build_forward_returns(price_series, "test", horizons=[1])
        # fwd[t] = price[t+1]/price[t] - 1 → price[t+1]はt+1日目
        # 最後の行はNaNのはず
        assert pd.isna(fwd["test_fwd1d"].iloc[-1])


class TestThresholdEventDetector:
    def test_detect_above(self) -> None:
        dates = pd.date_range("2020-01-01", periods=100, freq="D")
        z = pd.Series(np.linspace(-3, 3, 100), index=dates)
        det = ThresholdEventDetector(threshold=1.5, direction="above", cooldown_days=1)
        events = det.detect(z)
        assert len(events) > 0
        assert all(z.loc[e] >= 1.5 for e in events)

    def test_detect_below(self) -> None:
        dates = pd.date_range("2020-01-01", periods=100, freq="D")
        z = pd.Series(np.linspace(3, -3, 100), index=dates)
        det = ThresholdEventDetector(threshold=1.5, direction="below", cooldown_days=1)
        events = det.detect(z)
        assert len(events) > 0

    def test_cooldown_prevents_clustering(self) -> None:
        dates = pd.date_range("2020-01-01", periods=50, freq="D")
        # 常に閾値以上
        z = pd.Series(2.0, index=dates)
        det = ThresholdEventDetector(threshold=1.5, direction="above", cooldown_days=7)
        events = det.detect(z)
        # クールダウン7日なら50日中は最大8イベント
        assert len(events) <= 8

    def test_no_events_when_below_threshold(self) -> None:
        dates = pd.date_range("2020-01-01", periods=50, freq="D")
        z = pd.Series(0.5, index=dates)
        det = ThresholdEventDetector(threshold=1.5, direction="above", cooldown_days=1)
        events = det.detect(z)
        assert len(events) == 0
