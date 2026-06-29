"""イベントスタディ — methodology.md §3 の実装。

ブートストラップ95%CI・t検定・最大DD・シャープレシオを算出する。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

RNG_SEED = 42
N_BOOTSTRAP = 10_000


class EventStudy:
    """閾値イベント後の将来リターン分布を統計的に評価。"""

    def __init__(
        self,
        n_bootstrap: int = N_BOOTSTRAP,
        ci: float = 0.95,
        min_events: int = 5,
    ) -> None:
        self.n_bootstrap = n_bootstrap
        self.ci = ci
        self.min_events = min_events

    @staticmethod
    def _select_nonoverlapping(
        dates: pd.DatetimeIndex, horizon: int
    ) -> pd.DatetimeIndex:
        """将来horizon日の窓が重ならないようイベントを貪欲に間引く。

        ソート済みのdatesから、前回採用日から horizon 日以上空いたものだけ採用。
        これにより各イベントの将来リターン窓が独立し、ブートストラップのiid仮定が成立する。
        """
        selected: list[pd.Timestamp] = []
        last: pd.Timestamp | None = None
        for d in sorted(dates):
            if last is None or (d - last).days >= horizon:
                selected.append(d)
                last = d
        return pd.DatetimeIndex(selected)

    def analyze(
        self,
        event_dates: pd.DatetimeIndex,
        forward_returns: pd.DataFrame,
        indicator_key: str,
        target_key: str,
        direction: str = "above",
    ) -> pd.DataFrame:
        """イベント日ごとの将来リターンを集計し統計量を計算。

        Returns:
            DataFrame (行=horizon, 列=各統計量) with low_n_flag if n < 10
        """
        ret_cols = [c for c in forward_returns.columns if target_key in c]
        if not ret_cols:
            logger.warning("event_study: no return columns for target=%s", target_key)
            return pd.DataFrame()

        valid_dates = pd.DatetimeIndex(
            sorted(event_dates[event_dates.isin(forward_returns.index)])
        )
        n_events = len(valid_dates)

        if n_events < self.min_events:
            logger.warning(
                "event_study: indicator=%s target=%s n_events=%d < min=%d → skip",
                indicator_key,
                target_key,
                n_events,
                self.min_events,
            )
            return pd.DataFrame()

        rows: list[dict[str, object]] = []
        rng = np.random.default_rng(RNG_SEED)

        for col in ret_cols:
            horizon = int(col.split("fwd")[1].replace("d", ""))

            # 非重複窓: 将来horizon日リターンが重ならないようイベントを間引く。
            # これを怠るとブートストラップのiid仮定が崩れ、CIが過度に狭くなり有意性を過大評価する。
            nonoverlap_dates = self._select_nonoverlapping(valid_dates, horizon)
            rets = forward_returns.loc[nonoverlap_dates, col].dropna().values.astype(float)
            n = len(rets)
            n_raw = len(valid_dates)

            if n < self.min_events:
                continue

            # 非重複後のサンプルが小さい(<10)、または間引きで半減以上したら信頼度低
            low_n_flag = n < 10

            mean_ret = float(np.mean(rets))
            median_ret = float(np.median(rets))
            hit_rate = float(np.mean(rets > 0))

            # 最大ドローダウン（累積リターンから）
            cum = np.cumprod(1 + np.sort(rets)[::-1])
            running_max = np.maximum.accumulate(cum)
            dd = (cum - running_max) / running_max
            max_dd = float(dd.min())

            # シャープレシオ（年率化: horizon日で√252/horizon 近似）
            std_ret = float(np.std(rets, ddof=1))
            sharpe = (mean_ret / std_ret * np.sqrt(252 / horizon)) if std_ret > 0 else np.nan

            # t検定 (H0: mean=0)
            t_stat, t_p = stats.ttest_1samp(rets, 0.0)

            # ブートストラップ95%CI（再現性のため seed 固定）
            bs_means = np.array([
                np.mean(rng.choice(rets, size=n, replace=True))
                for _ in range(self.n_bootstrap)
            ])
            alpha = 1 - self.ci
            ci_lo = float(np.percentile(bs_means, 100 * alpha / 2))
            ci_hi = float(np.percentile(bs_means, 100 * (1 - alpha / 2)))

            rows.append({
                "indicator": indicator_key,
                "target": target_key,
                "direction": direction,
                "horizon_days": horizon,
                "n_events": n,
                "n_events_raw": n_raw,
                "low_n_flag": low_n_flag,
                "mean_return": round(mean_ret, 4),
                "median_return": round(median_ret, 4),
                "hit_rate": round(hit_rate, 3),
                "max_dd": round(max_dd, 4),
                "sharpe": round(sharpe, 3) if not np.isnan(sharpe) else None,
                "t_stat": round(float(t_stat), 3),
                "t_p": round(float(t_p), 4),
                "bs_ci_lo": round(ci_lo, 4),
                "bs_ci_hi": round(ci_hi, 4),
                "bs_ci_significant": (ci_lo > 0) if direction == "above" else (ci_hi < 0),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).sort_values("horizon_days").reset_index(drop=True)
        logger.info(
            "event_study: indicator=%s target=%s n_events=%d horizons=%d",
            indicator_key,
            target_key,
            n_events,
            len(df),
        )
        return df
