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

from src.config import DATA_PROCESSED, INDICATORS, OUTPUTS, DataQuality, Indicator
from src.indicator_loader import load_indicator_series
from src.scoring.normalizer import score_from_series

logger = logging.getLogger(__name__)

# indicator_key → Indicator の逆引き(config.INDICATORSから1回だけ構築)。
# 以前は scoring/engine.py と validation/run_validation.py が別々に
# indicator_key→(parquet_stem, column) のmapping辞書をハードコードしていたが、
# 共通ローダー(indicator_loader.py)に一本化した。
_INDICATORS_BY_KEY: dict[str, Indicator] = {ind.key: ind for ind in INDICATORS}

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
        weights_path: str | None = None,
    ) -> None:
        self.processed_dir = Path(processed_dir)
        sc_path = (
            Path(scorecard_path) if scorecard_path
            else OUTPUT_DIR / "indicator_scorecard.csv"
        )
        self.scorecard: pd.DataFrame = self._load_scorecard(sc_path)
        # Layer5(prediction/weight_updater.py)が学習した指標重みの上書き。
        # weights_path未指定(既定)の場合は読み込まない(挙動不変。既存の
        # config.py docstring「validationの結果で後段が上書きできる」の実装箇所)。
        self.learned_multipliers: dict[str, float] = (
            self._load_learned_multipliers(Path(weights_path)) if weights_path else {}
        )

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
            learned_multiplier = self.learned_multipliers.get(ind_key, 1.0)
            eff_weight = rank_weight * quality_weight * learned_multiplier

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
        """指標キーから現在値とスコアを取得。

        共通ローダー(indicator_loader.py)経由でconfig.Indicatorのメタデータに
        基づき時系列を読み込む(respect_step2_flag=False: 四半期capex等の
        step2_verifiable=False指標もExtendedスコアでは引き続き使う。
        score_from_series自身が短い系列を自然にNone扱いする)。
        """
        ind = _INDICATORS_BY_KEY.get(ind_key)
        if ind is None:
            return None, None
        series = load_indicator_series(
            ind, target, processed_dir=self.processed_dir, respect_step2_flag=False
        )
        if series is None or series.empty:
            return None, None
        latest = float(series.iloc[-1])
        score, _ = score_from_series(series, latest)
        return latest, score

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
    def _load_learned_multipliers(path: Path) -> dict[str, float]:
        """prediction/weight_updater.pyが出力するindicator_weights.csvを読み込む。

        ファイルが無い/読み込み失敗時は空辞書(全指標multiplier=1.0=既存挙動不変)。
        """
        if not path.exists():
            return {}
        try:
            df = pd.read_csv(path)
            return {
                str(row["indicator_key"]): float(row["learned_multiplier"])
                for _, row in df.iterrows()
                if pd.notna(row.get("learned_multiplier"))
            }
        except Exception as exc:
            logger.warning("indicator_weights.csv load failed: %s", exc)
            return {}

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
