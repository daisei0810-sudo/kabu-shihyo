"""特徴量エンジニアリング — YoY・MoM・移動平均・Zスコア・将来リターンを生成。

methodology.md §1 の仕様に従う:
  - 特徴量は t 以前の情報のみ使用（ルックアヘッド禁止）
  - Zスコアの窓は最低252営業日。短い系列は信頼度低フラグ
  - 将来リターン h = [1, 7, 30, 60, 90, 120] 日は t より後を使用
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.config import FORWARD_RETURN_DAYS

logger = logging.getLogger(__name__)

# Zスコアで信頼度低フラグを付ける最小ウィンドウ（営業日）
MIN_ZSCORE_WINDOW = 252
SHORT_SERIES_MIN_ROWS = 100  # これ未満は全体的に「短期系列」として警告


class FeatureEngineer:
    """価格・指標の時系列から先行指標検証用特徴量を生成。"""

    def __init__(self, zscore_window: int = 252, ma_windows: list[int] | None = None) -> None:
        self.zscore_window = zscore_window
        self.ma_windows = ma_windows or [30, 90]

    def build_indicator_features(
        self,
        series: pd.Series,
        name: str,
    ) -> pd.DataFrame:
        """先行指標1系列から特徴量セットを生成。

        Args:
            series: 日次時系列 (DatetimeIndex, 値は float)
            name: 指標キー（カラム接頭辞に使用）

        Returns:
            特徴量DataFrame (同じインデックス)
        """
        s = series.copy().sort_index()
        s.name = name
        feats: dict[str, pd.Series] = {}

        # --- 移動平均 ---
        for w in self.ma_windows:
            feats[f"{name}_ma{w}"] = s.rolling(w, min_periods=max(1, w // 2)).mean()

        # --- MoM (前月比): 21営業日 ---
        feats[f"{name}_mom21"] = s.pct_change(21)

        # --- YoY (前年比): 252営業日 ---
        feats[f"{name}_yoy252"] = s.pct_change(252)

        # --- Zスコア (レベル) ---
        # 注意: トレンドする系列(TVL/価格/供給量)のレベルZスコアは、トレンド相場では
        #       将来リターンと見せかけの相関を生む。ランクの見出しには定常特徴量(_dz)を使う。
        zscore, low_conf = self._zscore(s, self.zscore_window)
        feats[f"{name}_zscore"] = zscore

        # --- 定常化Zスコア (21日変化率のZスコア) ---
        # 共通トレンドを除去した「勢いの異常」。見せかけ相関を避けるための主特徴量。
        change = s.pct_change(21)
        dz, _ = self._zscore(change, self.zscore_window)
        feats[f"{name}_dz"] = dz

        if low_conf:
            logger.warning(
                "zscore_window=%d but series '%s' has only %d rows → low_confidence_flag=True",
                self.zscore_window,
                name,
                len(s.dropna()),
            )

        # --- フラグ ---
        feats[f"{name}_low_conf"] = pd.Series(
            float(low_conf), index=s.index, dtype=float
        )

        df = pd.DataFrame(feats)
        df.index.name = "date"
        return df

    def build_forward_returns(
        self,
        price: pd.Series,
        name: str,
        horizons: list[int] | None = None,
    ) -> pd.DataFrame:
        """対象資産価格から将来リターンを生成（ルックアヘッド: t 以降）。

        Args:
            price: 終値時系列
            name: 資産キー
            horizons: 将来リターン日数リスト (default: config.FORWARD_RETURN_DAYS)

        Returns:
            将来リターンDataFrame
        """
        hs = horizons or FORWARD_RETURN_DAYS
        p = price.copy().sort_index()
        cols: dict[str, pd.Series] = {}
        for h in hs:
            # r_{t→t+h} = p_{t+h}/p_t - 1 (shift(-h) で h先を引き寄せる)
            cols[f"{name}_fwd{h}d"] = p.shift(-h) / p - 1.0
        df = pd.DataFrame(cols)
        df.index.name = "date"
        return df

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _zscore(s: pd.Series, window: int) -> tuple[pd.Series, bool]:
        """rolling Zスコアを計算。ウィンドウに対してデータが短いとき low_conf=True。"""
        roll = s.rolling(window, min_periods=max(30, window // 4))
        mu = roll.mean()
        sigma = roll.std(ddof=1)
        z = (s - mu) / sigma.replace(0, np.nan)
        low_conf = len(s.dropna()) < MIN_ZSCORE_WINDOW
        return z, low_conf

    @staticmethod
    def align(
        indicator_features: pd.DataFrame,
        forward_returns: pd.DataFrame,
        lag: int,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """指標特徴量を lag 日シフトして将来リターンと整合させる。

        ルックアヘッド回避:
          - 指標特徴量を lag 日後ろへシフト（indicator[t-lag] を t 時点に配置）
          - つまり「lag 日前の指標値で t 時点のリターンを予測できるか」を検証

        Returns:
            (shifted_features, aligned_returns) — dropna後に共通インデックスを持つ
        """
        shifted = indicator_features.shift(lag)
        common_idx = shifted.index.intersection(forward_returns.index)
        X = shifted.loc[common_idx].dropna(how="all")
        y = forward_returns.loc[X.index].dropna(how="all")
        common = X.index.intersection(y.index)
        return X.loc[common], y.loc[common]
