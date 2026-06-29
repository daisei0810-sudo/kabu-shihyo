"""Hard/Extended スコアエンジン — indicator_scorecard.csv に基づき指標を集約。

methodology.md §5:
  Hardスコア   = verified 指標のみ (rank A+/A/B) を (rank重み × quality重み) で加重平均。
  Extendedスコア = Hard + proxy/estimated + C ランク を同様に加重。
  confidence(%) = Σ採用指標の有効重み / Σ理論最大重み。
  data_coverage(%) = verified 指標数 / 対象全指標数。

スコアカードがない場合 (Step2 未実行) は None を返す。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, INDICATORS, OUTPUTS, DataQuality
from src.scoring.normalizer import score_from_series

logger = logging.getLogger(__name__)

PROCESSED_DIR_DEFAULT = Path(DATA_PROCESSED)
OUTPUT_DIR = Path(OUTPUTS)

# ランク → スコアリング重み
RANK_WEIGHTS: dict[str, float] = {
    "A+": 1.0,
    "A": 0.8,
    "B": 0.6,
    "C": 0.3,   # 補助指標。Extended のみに算入、重みを下げる
    "D": 0.0,   # 不採用
}

# Hard スコアに算入するランク (verified のみ)
HARD_RANKS: frozenset[str] = frozenset({"A+", "A", "B"})
# Extended スコアに算入するランク
EXTENDED_RANKS: frozenset[str] = frozenset({"A+", "A", "B", "C"})


@dataclass
class IndicatorContribution:
    """1指標のスコア寄与情報。"""

    key: str
    data_quality: str
    rank: str
    adopted: bool
    raw_value: float | None
    score_0_100: float | None
    rank_weight: float
    quality_weight: float
    effective_weight: float    # rank_weight × quality_weight
    in_hard: bool
    in_extended: bool
    confidence_note: str = ""


@dataclass
class AssetScore:
    """1資産の Hard / Extended スコア結果。"""

    target: str
    hard_score: float | None
    extended_score: float | None
    confidence_pct: float
    data_coverage_pct: float
    n_hard_indicators: int
    n_extended_indicators: int
    contributions: list[IndicatorContribution] = field(default_factory=list)
    note: str = ""


class ScoreEngine:
    """indicator_scorecard.csv と処理済みデータから Hard/Extended スコアを計算。"""

    def __init__(
        self,
        scorecard_path: str | None = None,
        processed_dir: str = DATA_PROCESSED,
    ) -> None:
        self.processed_dir = Path(processed_dir)
        sc_path = (
            Path(scorecard_path) if scorecard_path
            else OUTPUT_DIR / "indicator_scorecard.csv"
        )
        self.scorecard: pd.DataFrame = self._load_scorecard(sc_path)

    def compute(self, target: str) -> AssetScore:
        """指定資産の Hard/Extended スコアを計算。"""
        sc = (
            self.scorecard[self.scorecard["target"] == target]
            if not self.scorecard.empty
            else pd.DataFrame()
        )

        if sc.empty:
            logger.warning("scorecard: no rows for target=%s", target)
            return AssetScore(
                target=target,
                hard_score=None,
                extended_score=None,
                confidence_pct=0.0,
                data_coverage_pct=0.0,
                n_hard_indicators=0,
                n_extended_indicators=0,
                note="スコアカードなし(Step2未実行)",
            )

        contributions: list[IndicatorContribution] = []

        for _, row in sc.iterrows():
            ind_key = str(row["indicator"])
            rank = str(row.get("rank", "D"))
            adopted = bool(row.get("adopted", False))
            data_quality = str(row.get("data_quality", "verified"))
            quality_weight = float(row.get("confidence_weight", 1.0))
            confidence_note = str(row.get("confidence_note", ""))

            rank_weight = RANK_WEIGHTS.get(rank, 0.0)

            # Hard: verified かつ A+/A/B のみ
            in_hard = (
                rank in HARD_RANKS
                and data_quality == DataQuality.VERIFIED.value
            )
            # Extended: unavailable を除いた全 A+〜C
            in_extended = (
                rank in EXTENDED_RANKS
                and data_quality in {
                    DataQuality.VERIFIED.value,
                    DataQuality.PROXY.value,
                    DataQuality.ESTIMATED.value,
                }
            )

            raw_val, score_0_100 = self._get_current_score(ind_key, target)
            eff_weight = rank_weight * quality_weight

            contributions.append(IndicatorContribution(
                key=ind_key,
                data_quality=data_quality,
                rank=rank,
                adopted=adopted,
                raw_value=raw_val,
                score_0_100=score_0_100,
                rank_weight=rank_weight,
                quality_weight=quality_weight,
                effective_weight=eff_weight,
                in_hard=in_hard,
                in_extended=in_extended,
                confidence_note=confidence_note,
            ))

        hard_score, hard_conf = self._weighted_avg(
            [c for c in contributions if c.in_hard]
        )
        ext_score, ext_conf = self._weighted_avg(
            [c for c in contributions if c.in_extended]
        )

        # data_coverage = verified & スコア算出済み / 対象全指標(unavailable除く)
        total_inds = [
            i for i in INDICATORS
            if target in i.targets and i.data_quality != DataQuality.UNAVAILABLE
        ]
        verified_count = sum(
            1 for c in contributions
            if c.data_quality == DataQuality.VERIFIED.value and c.score_0_100 is not None
        )
        data_coverage = verified_count / len(total_inds) if total_inds else 0.0

        return AssetScore(
            target=target,
            hard_score=hard_score,
            extended_score=ext_score,
            confidence_pct=ext_conf,
            data_coverage_pct=round(data_coverage, 3),
            n_hard_indicators=sum(
                1 for c in contributions if c.in_hard and c.score_0_100 is not None
            ),
            n_extended_indicators=sum(
                1 for c in contributions if c.in_extended and c.score_0_100 is not None
            ),
            contributions=contributions,
            note=self._build_note(contributions),
        )

    # ------------------------------------------------------------------

    def _get_current_score(
        self, ind_key: str, target: str
    ) -> tuple[float | None, float | None]:
        """指標キーから現在値とスコアを取得。"""
        series, latest = self._load_indicator_latest(ind_key, target)
        if series is None or latest is None:
            return None, None
        score, _ = score_from_series(series, latest)
        return latest, score

    def _load_indicator_latest(
        self, ind_key: str, target: str
    ) -> tuple[pd.Series | None, float | None]:
        """指標の歴史系列と最新値を取得。"""
        if ind_key == "optical_module_demand":
            return self._load_peer_basket(target)

        mapping: dict[str, tuple[str, str]] = {
            "xrp_price":          ("price_xrp", "Close"),
            "stablecoin_tvl":     ("defillama_stablecoin_tvl", "stablecoin_tvl_usd"),
            "amm_tvl":            ("defillama_xrpl_tvl", "tvl_usd"),
            "amm_xrp_balance":    ("xrpl_amm_XRP_RLUSD", "xrp_balance"),
            "rlusd_supply":       ("xrpl_rlusd_supply", "rlusd_supply"),
            "sox_index":          ("price_index_sox", "Close"),
            "tsmc_capex":         ("capex_tsm", "capex"),
            "nvidia_revenue":     ("capex_nvda", "capex"),
            "hyperscaler_capex":  ("capex_hyperscaler_total", "hyperscaler_capex_total"),
            "xrpl_tx_count":      ("xrpl_network_stats", "txn_count"),
        }

        if ind_key not in mapping:
            return None, None

        stem, col = mapping[ind_key]
        path = self.processed_dir / f"{stem}.parquet"
        if not path.exists():
            return None, None
        try:
            df = pd.read_parquet(path)
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_convert(None)
            df = df.sort_index()
            if col not in df.columns:
                return None, None
            s = df[col].dropna()
            if s.empty:
                return None, None
            return s, float(s.iloc[-1])
        except Exception as exc:
            logger.warning("load failed: %s: %s", stem, exc)
            return None, None

    def _load_peer_basket(
        self, target: str
    ) -> tuple[pd.Series | None, float | None]:
        """光モジュール需要 = 対象を除いたピアバスケット(自己proxy回避)。"""
        peers = ["fujikura", "sumitomo_electric", "furukawa_electric", "murata"]
        series_list: list[pd.Series] = []
        for p in peers:
            if p == target:
                continue
            path = self.processed_dir / f"price_{p}.parquet"
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path)
                if hasattr(df.index, "tz") and df.index.tz is not None:
                    df.index = df.index.tz_convert(None)
                if "Close" in df.columns:
                    s = df["Close"].dropna()
                    if len(s) > 0:
                        series_list.append(s / float(s.iloc[0]))
            except Exception:
                continue
        if not series_list:
            return None, None
        basket = pd.concat(series_list, axis=1).mean(axis=1).dropna()
        if basket.empty:
            return None, None
        return basket, float(basket.iloc[-1])

    @staticmethod
    def _weighted_avg(
        contribs: list[IndicatorContribution],
    ) -> tuple[float | None, float]:
        """有効な寄与の加重平均スコアと信頼度(0-1)を返す。"""
        total_eff = sum(c.effective_weight for c in contribs if c.effective_weight > 0)
        available = [
            c for c in contribs
            if c.score_0_100 is not None and c.effective_weight > 0
        ]
        if not available or total_eff == 0:
            return None, 0.0

        avail_eff = sum(c.effective_weight for c in available)
        weighted_score = sum(
            c.score_0_100 * c.effective_weight
            for c in available
            if c.score_0_100 is not None
        )
        score = weighted_score / avail_eff if avail_eff > 0 else None
        confidence = avail_eff / total_eff

        return (round(score, 1) if score is not None else None), round(confidence, 3)

    @staticmethod
    def _load_scorecard(path: Path) -> pd.DataFrame:
        if not path.exists():
            logger.warning("scorecard not found: %s (Step2 未実行)", path)
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except Exception as exc:
            logger.warning("scorecard load failed: %s", exc)
            return pd.DataFrame()

    @staticmethod
    def _build_note(contributions: list[IndicatorContribution]) -> str:
        hard = [c.key for c in contributions if c.in_hard and c.adopted]
        ext_only = [c.key for c in contributions if c.in_extended and not c.in_hard]
        excluded = [c.key for c in contributions if not c.in_extended]
        parts = []
        if hard:
            parts.append(f"Hard採用: {hard}")
        if ext_only:
            parts.append(f"Extended追加: {ext_only}")
        if excluded:
            parts.append(f"除外(D/unavailable): {excluded}")
        return " | ".join(parts) if parts else "指標なし"
