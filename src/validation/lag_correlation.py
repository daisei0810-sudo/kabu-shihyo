"""ラグ相関計算 — methodology.md §2 の実装。

多重検定補正(BH/FDR)を適用し、q値を出力する。
"""

from __future__ import annotations

import logging
from itertools import product

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def _bh_correction(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg FDR 補正。p値リストを受け取り q値リストを返す。"""
    n = len(p_values)
    if n == 0:
        return []
    sorted_idx = np.argsort(p_values)
    sorted_p = np.array(p_values)[sorted_idx]
    q = np.zeros(n)
    cummin = np.inf
    for i in range(n - 1, -1, -1):
        cummin = min(cummin, sorted_p[i] * n / (i + 1))
        q[sorted_idx[i]] = min(cummin, 1.0)
    return q.tolist()


class LagCorrelationAnalyzer:
    """指標特徴量 × 将来リターン のラグ相関を計算する。

    Args:
        lag_days: 検証するリードタイム (日)
        min_obs: 相関計算に必要な最小観測数
    """

    def __init__(
        self,
        lag_days: list[int] | None = None,
        min_obs: int = 30,
    ) -> None:
        from src.config import LAG_DAYS
        self.lag_days = lag_days or LAG_DAYS
        self.min_obs = min_obs

    def compute(
        self,
        features: pd.DataFrame,
        forward_returns: pd.DataFrame,
        indicator_key: str,
        target_key: str,
    ) -> pd.DataFrame:
        """全ラグ × 全特徴量 × 全将来リターン水平のスピアマン/ピアソン相関を計算。

        Returns:
            DataFrame with columns:
              indicator, target, feature, lag_days, horizon_days,
              spearman_r, spearman_p, pearson_r, pearson_p, n_obs, q_spearman
        """
        rows: list[dict[str, object]] = []

        feat_cols = [c for c in features.columns if "low_conf" not in c]
        ret_cols = list(forward_returns.columns)

        for lag, feat_col, ret_col in product(self.lag_days, feat_cols, ret_cols):
            # lag シフト後のアライン
            shifted_feat = features[[feat_col]].shift(lag)
            common = shifted_feat.index.intersection(forward_returns.index)
            x = shifted_feat.loc[common, feat_col].dropna()
            y = forward_returns.loc[x.index, ret_col].dropna()
            common2 = x.index.intersection(y.index)
            x = x.loc[common2]
            y = y.loc[common2]

            if len(x) < self.min_obs:
                continue

            sp_r, sp_p = stats.spearmanr(x, y)
            pe_r, pe_p = stats.pearsonr(x, y)

            rows.append({
                "indicator": indicator_key,
                "target": target_key,
                "feature": feat_col,
                "lag_days": lag,
                "horizon_days": int(ret_col.split("fwd")[1].replace("d", "")),
                "spearman_r": round(float(sp_r), 4),
                "spearman_p": float(sp_p),
                "pearson_r": round(float(pe_r), 4),
                "pearson_p": float(pe_p),
                "n_obs": len(x),
            })

        if not rows:
            logger.warning(
                "lag_correlation: no rows for indicator=%s target=%s (insufficient data?)",
                indicator_key,
                target_key,
            )
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # BH補正（スピアマンp値に対して）
        q_vals = _bh_correction(df["spearman_p"].tolist())
        df["q_spearman"] = [round(q, 4) for q in q_vals]

        df = df.sort_values(["lag_days", "horizon_days", "feature"]).reset_index(drop=True)
        logger.info(
            "lag_correlation: indicator=%s target=%s rows=%d",
            indicator_key,
            target_key,
            len(df),
        )
        return df
