"""スコア正規化ユーティリティ — Zスコア/パーセンタイルを 0–100 に変換。

methodology.md §5:
  各 Early Signal を Zスコア化し 0–100 にマップする。
  データが少ない場合はパーセンタイル順位に落とし、信頼度を下げる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def zscore_to_score(z: float | None, clip: float = 3.0) -> float | None:
    """Zスコア → 0–100 スコア。[-clip, +clip] を [0, 100] に線形マップ。

    z=0 (平均) → 50、z=+clip → 100、z=-clip → 0。
    """
    if z is None or np.isnan(float(z)):
        return None
    z_clipped = float(np.clip(z, -clip, clip))
    return float((z_clipped + clip) / (2.0 * clip) * 100.0)


def percentile_rank_score(series: pd.Series, current: float) -> float:
    """系列内でのパーセンタイル順位を 0–100 スコアとして返す。"""
    clean = series.dropna()
    if len(clean) < 2:
        return 50.0
    rank = float((clean <= current).mean()) * 100.0
    return float(np.clip(rank, 0.0, 100.0))


def score_from_series(
    series: pd.Series,
    current_value: float | None = None,
    method: str = "percentile",
    zscore_window: int = 252,
    clip: float = 3.0,
) -> tuple[float | None, str]:
    """系列の最新値をスコア化して (score 0-100 or None, method_note) を返す。

    データ数 < 30 の場合はスコア算出不可として None を返す。
    データ数 < zscore_window の場合はパーセンタイル方式にフォールバックする。
    """
    clean = series.dropna()
    if len(clean) == 0 or current_value is None:
        return None, "データなし"

    if len(clean) < 30:
        return None, f"データ不足(n={len(clean)}<30)"

    if method == "percentile" or len(clean) < zscore_window:
        score = percentile_rank_score(clean, current_value)
        return score, f"パーセンタイル(n={len(clean)})"

    roll = clean.rolling(zscore_window, min_periods=zscore_window // 4)
    mu = roll.mean().iloc[-1]
    sigma = roll.std(ddof=1).iloc[-1]
    if not np.isfinite(sigma) or sigma == 0:
        return 50.0, "σ=0(変動なし)"
    z = (current_value - float(mu)) / float(sigma)
    score_val = zscore_to_score(z, clip=clip)
    return score_val, f"Zscore(window={zscore_window})"
