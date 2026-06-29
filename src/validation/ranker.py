"""有効性ランク付与 — methodology.md §4 の実装。

A+ / A / B / C / D を付与し、業界指標 vs 個別指標の採否も記録する。
walk-forward (前半/後半) での再現性チェックを含む。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.config import RANK_THRESHOLDS

logger = logging.getLogger(__name__)


def assign_rank(
    corr: float,
    hit_rate: float,
    q_spearman: float,
    n_periods_consistent: int = 0,
    low_n_flag: bool = False,
) -> str:
    """単一観測値からランクを付与。

    Args:
        corr: スピアマン相関の絶対値
        hit_rate: 的中率 (0-1)
        q_spearman: BH補正後q値
        n_periods_consistent: 複数期間で同符号・有意な数（A+要件）
        low_n_flag: イベント数 < 10 なら最大B止まり

    Returns:
        "A+" | "A" | "B" | "C" | "D"
    """
    abs_corr = abs(corr)
    significant = q_spearman < 0.05

    if abs_corr >= RANK_THRESHOLDS["A+"]["corr"] and hit_rate >= RANK_THRESHOLDS["A+"]["hit"]:
        if significant and n_periods_consistent >= 2 and not low_n_flag:
            return "A+"
        elif not low_n_flag:
            return "A"

    if abs_corr >= RANK_THRESHOLDS["A"]["corr"] and hit_rate >= RANK_THRESHOLDS["A"]["hit"]:
        if significant and not low_n_flag:
            return "A"
        return "B"

    if abs_corr >= RANK_THRESHOLDS["B"]["corr"] and hit_rate >= RANK_THRESHOLDS["B"]["hit"]:
        return "B"

    if abs_corr >= 0.15 and hit_rate >= 0.50:
        return "C"

    return "D"


class IndicatorRanker:
    """ラグ相関・イベントスタディ結果から指標の有効性スコアカードを生成。"""

    def build_scorecard(
        self,
        lag_corr_df: pd.DataFrame,
        event_study_df: pd.DataFrame,
        indicator_key: str,
        target_key: str,
        data_quality: str,
        confidence_weight: float,
    ) -> pd.DataFrame:
        """指標ごとの有効性スコアカードを生成。

        Returns:
            DataFrame (1行=最良ラグ, 各統計量 + ランク)
        """
        if lag_corr_df.empty and event_study_df.empty:
            return pd.DataFrame()

        rows: list[dict[str, object]] = []

        # 見出しランクは「定常特徴量(_dz/mom21/yoy252)」で判定する。
        # レベルZスコア(_zscore)はトレンド相場で見せかけ相関を生むため、診断用に別途記録。
        level_corr = 0.0  # レベル特徴量(_zscore)の最大|相関|
        if not lag_corr_df.empty:
            stationary_mask = lag_corr_df["feature"].str.contains(
                "_dz|mom21|yoy252", regex=True
            )
            level_mask = lag_corr_df["feature"].str.contains("_zscore", regex=True)

            stationary_rows = lag_corr_df[stationary_mask]
            level_rows = lag_corr_df[level_mask]

            if not level_rows.empty:
                level_idx = level_rows["spearman_r"].abs().idxmax()
                level_corr = float(level_rows.loc[level_idx, "spearman_r"])

            # 見出し: 定常特徴量の最大|相関|。なければ全体から。
            headline_rows = stationary_rows if not stationary_rows.empty else lag_corr_df
            best_idx = headline_rows["spearman_r"].abs().idxmax()
            best_corr_row = headline_rows.loc[best_idx]
            best_lag = int(best_corr_row["lag_days"])
            best_horizon = int(best_corr_row["horizon_days"])
            best_corr = float(best_corr_row["spearman_r"])
            best_q = float(best_corr_row["q_spearman"])
            best_n = int(best_corr_row["n_obs"])

            n_consistent = self._count_consistent_periods(
                headline_rows, best_lag, best_horizon
            )
        else:
            best_lag = best_horizon = 0
            best_corr = best_q = 0.0
            best_n = 0
            n_consistent = 0

        # トレンド共通交絡フラグ: レベル相関は高いが定常(変化)相関がほぼゼロ
        #   → 両者が同じトレンドで動いただけの「見せかけ」。本物の先行関係ではない。
        trend_confound = abs(level_corr) >= 0.40 and abs(best_corr) < 0.20

        # イベントスタディから的中率・最大DD・ブートストラップCIを取得
        hit_rate = 0.5
        max_dd = 0.0
        sharpe = None
        bs_significant = False
        low_n_flag = True

        if not event_study_df.empty:
            # best_horizon に最も近いhorizonを探す
            es_row = self._match_horizon(event_study_df, best_horizon)
            if es_row is not None:
                hit_rate = float(es_row.get("hit_rate", 0.5))
                max_dd = float(es_row.get("max_dd", 0.0))
                sharpe = es_row.get("sharpe")
                bs_significant = bool(es_row.get("bs_ci_significant", False))
                low_n_flag = bool(es_row.get("low_n_flag", True))

        rank = assign_rank(
            corr=best_corr,
            hit_rate=hit_rate,
            q_spearman=best_q,
            n_periods_consistent=n_consistent,
            low_n_flag=low_n_flag,
        )

        # 実効独立サンプル数 ≈ 観測数 / (ラグ+ホライゾン)。
        # 重複する長期窓は実効サンプルを水増しする。120-180日ラグを1-2年の履歴で見ると
        # 実効サンプルは数個しかなく、相関値が高くても統計的に信頼できない。
        span = max(best_lag + best_horizon, 1)
        effective_n = best_n / span if best_n else 0.0
        insufficient_history = effective_n < 10.0

        # 降格ルール(見せかけ相関・履歴不足は採用しない)
        if trend_confound and rank in ("A+", "A", "B"):
            rank = "C"
        if insufficient_history and rank in ("A+", "A", "B"):
            rank = "C"

        adopted = rank in ("A+", "A", "B")

        rows.append({
            "indicator": indicator_key,
            "target": target_key,
            "data_quality": data_quality,
            "confidence_weight": confidence_weight,
            "best_lag_days": best_lag,
            "best_horizon_days": best_horizon,
            "spearman_r_stationary": round(best_corr, 4),
            "spearman_r_level": round(level_corr, 4),
            "trend_confound": trend_confound,
            "q_spearman": round(best_q, 4),
            "hit_rate": round(hit_rate, 3),
            "max_dd": round(max_dd, 4),
            "sharpe": sharpe,
            "bs_significant": bs_significant,
            "n_obs": best_n,
            "effective_n": round(effective_n, 1),
            "insufficient_history": insufficient_history,
            "low_n_flag": low_n_flag,
            "n_consistent_periods": n_consistent,
            "rank": rank,
            "adopted": adopted,
            "confidence_note": self._confidence_note(
                rank, low_n_flag, data_quality, n_consistent, trend_confound,
                insufficient_history, effective_n,
            ),
        })

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------

    @staticmethod
    def _count_consistent_periods(
        corr_df: pd.DataFrame,
        best_lag: int,
        best_horizon: int,
    ) -> int:
        """近傍ラグ（±1段階）で符号と有意性が一致する期間数を数える（再現性チェック）。"""
        target = corr_df[
            (corr_df["lag_days"] == best_lag) & (corr_df["horizon_days"] == best_horizon)
        ]
        if target.empty:
            return 0
        sign = float(target["spearman_r"].iloc[0])

        # 近傍ラグを調べる
        all_lags = sorted(corr_df["lag_days"].unique())
        idx_in_all = all_lags.index(best_lag) if best_lag in all_lags else -1
        neighbor_lags = [
            all_lags[i]
            for i in range(max(0, idx_in_all - 1), min(len(all_lags), idx_in_all + 2))
            if all_lags[i] != best_lag
        ]

        consistent = 0
        for lag in neighbor_lags:
            rows = corr_df[
                (corr_df["lag_days"] == lag) & (corr_df["horizon_days"] == best_horizon)
            ]
            if rows.empty:
                continue
            r = float(rows["spearman_r"].iloc[0])
            q = float(rows["q_spearman"].iloc[0])
            if np.sign(r) == np.sign(sign) and q < 0.05:
                consistent += 1

        return consistent

    @staticmethod
    def _match_horizon(
        event_study_df: pd.DataFrame,
        target_horizon: int,
    ) -> dict[str, Any] | None:
        """target_horizon に最も近い行を返す。"""
        if event_study_df.empty:
            return None
        diffs = (event_study_df["horizon_days"] - target_horizon).abs()
        best = diffs.idxmin()
        return event_study_df.loc[best].to_dict()

    @staticmethod
    def _confidence_note(
        rank: str,
        low_n_flag: bool,
        data_quality: str,
        n_consistent: int,
        trend_confound: bool = False,
        insufficient_history: bool = False,
        effective_n: float = 0.0,
    ) -> str:
        notes: list[str] = []
        if insufficient_history:
            notes.append(f"履歴不足: 実効独立サンプル≈{effective_n:.0f}(<10)→信頼不可")
        if trend_confound:
            notes.append("見せかけ相関(共通トレンド): 変化率相関ゼロ→先行関係なし")
        if low_n_flag:
            notes.append("非重複イベント数 < 10 → 統計的信頼度低")
        if data_quality == "proxy":
            notes.append("proxyデータ → 直接の因果性未確認")
        if data_quality == "estimated":
            notes.append("推定データ → 信頼度低")
        if rank in ("A+", "A") and n_consistent < 2:
            notes.append("複数期間での再現性未確認")
        if not notes:
            return "OK"
        return "; ".join(notes)
