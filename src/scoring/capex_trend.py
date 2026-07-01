"""四半期CAPEXの専用トレンドスコアラー。

`normalizer.score_from_series` は `len(clean) < 30` で None を返す設計だが、
四半期CAPEXデータ(capex_*.parquet)は現状5行程度しかなく、percentile/zscoreベースの
既存スコアリングでは常に None になってしまう。そのため四半期データ専用の
YoY(前年同期比)トレンドスコアラーを用意する。

設計:
  直近四半期 vs 4期(1年)前の成長率を 0-100 にマップする。
  YoY >= +40% → 100 / YoY = 0% → 50 / YoY <= -40% → 0 (線形クリップ)
  4期分データが無ければ直近 vs 前期(QoQ)で代替し、その旨を note に明示する。
  2期未満は None。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_GROWTH_SATURATION = 0.40  # ±40%成長でスコア0/100に到達


def growth_rate_to_score(growth: float, saturation: float = _GROWTH_SATURATION) -> float:
    """成長率(-1.0=-100% ~ +1.0=+100%等)を 0-100 スコアに線形マップする。

    growth=0(横ばい)→50、+saturation以上→100、-saturation以下→0。
    demand_index.py のモメンタム系コンポーネントでも再利用する汎用関数。
    """
    return float(np.clip(50 + growth / saturation * 50, 0, 100))


def capex_trend_score(series: pd.Series) -> tuple[float | None, str]:
    """四半期CAPEX系列を 0-100 スコアに変換する。

    直近値 vs 4期前(YoY)の成長率を優先し、データが4期未満ならQoQで代替する。
    """
    clean = series.dropna()
    if len(clean) < 2:
        return None, "四半期データ不足(2期未満)"

    if len(clean) >= 5:
        prev = float(clean.iloc[-5])
        curr = float(clean.iloc[-1])
        if prev == 0:
            return None, "前年同期値が0のためYoY算出不可"
        yoy = (curr - prev) / abs(prev)
        return growth_rate_to_score(yoy), f"YoY={yoy:+.0%}"

    prev = float(clean.iloc[-2])
    curr = float(clean.iloc[-1])
    if prev == 0:
        return None, "前期値が0のためQoQ算出不可"
    qoq = (curr - prev) / abs(prev)
    return growth_rate_to_score(qoq), f"QoQ={qoq:+.0%}(4期未満のためYoY代替不可)"
