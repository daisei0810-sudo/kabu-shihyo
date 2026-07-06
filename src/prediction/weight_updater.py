"""Layer5指標重み自動更新(docs/investment_os_design.md §4.6(c))。

評価済み予測(prediction.evidence_json = 根拠指標keyのリスト)を指標ごとに集計し、
的中率に応じてlearned_multiplierを指数移動・有界(0.25-2.0)で更新する。
評価サンプルn<10の指標は更新しない(実効サンプルガード、methodology.mdの思想を踏襲)。

設計上の簡略化: design§4.6(c)は「contribution_i = weight_i × sign(zscore_i) ×
sign(excess_return)」という指標固有のzscore符号まで使う定式化だが、現状
Prediction.evidence_jsonは根拠指標keyの単純なリスト(§4.3のDecisionRecord.
evidence_indicators由来)であり、指標ごとのzscore/weightまでは記録していない。
そのため本実装では「その予測が方向的中したか(direction_hit)」を、その予測が
引用した全指標に等しく帰属させる簡略版とする(データとして存在しないzscore精度を
捏造しない)。将来evidence_jsonが構造化されれば元の定式化へ移行できる。

ゲート: 有効重み = base_weight(data_quality) × rank_weight(scorecard) × multiplier。
scorecardでDランク(rank_weight=0)の指標はmultiplierに関わらず0になる
(統計検証が門番、実績学習は微調整という二段構え)。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import DEFAULT_CONFIDENCE_WEIGHT, INDICATORS, OUTPUTS, Indicator
from src.prediction.models import Evaluation, Prediction
from src.prediction.store import load_evaluations, load_predictions

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)
INDICATOR_WEIGHTS_CSV = OUTPUT_DIR / "indicator_weights.csv"

MIN_EVALUATIONS_FOR_UPDATE = 10
LEARNING_RATE = 0.1
MULTIPLIER_MIN = 0.25
MULTIPLIER_MAX = 2.0
MULTIPLIER_DEFAULT = 1.0

# scoring/engine.py の RANK_WEIGHTS と同一の値(indicator_scorecard.csvのrank係数)。
# 循環importを避けるためここに複製する(値は必ず一致させること)。
RANK_WEIGHTS: dict[str, float] = {"A+": 1.0, "A": 0.8, "B": 0.6, "C": 0.3, "D": 0.0}


@dataclass
class IndicatorWeightResult:
    """1指標の学習済み重み。"""

    indicator_key: str
    base_weight: float
    rank_weight: float
    learned_multiplier: float
    effective_weight: float
    n_evaluations: int
    hit_rate: float | None
    avg_excess_when_cited: float | None
    updated_at: str


def compute_attributions(
    evaluations: list[Evaluation], predictions_by_id: dict[str, Prediction],
) -> dict[str, list[tuple[bool, float | None]]]:
    """indicator_key -> [(direction_hit, excess_return), ...] を構築する。

    評価済み(status=="evaluated")かつdirection_hitが判定可能な予測のみ対象。
    """
    attributions: dict[str, list[tuple[bool, float | None]]] = {}
    for ev in evaluations:
        if ev.status != "evaluated" or ev.direction_hit is None:
            continue
        pred = predictions_by_id.get(ev.prediction_id)
        if pred is None:
            continue
        try:
            indicator_keys = json.loads(pred.evidence_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(indicator_keys, list):
            continue
        for key in indicator_keys:
            attributions.setdefault(str(key), []).append((ev.direction_hit, ev.excess_return))
    return attributions


def _rank_weight_for_indicator(indicator_key: str, scorecard_df: pd.DataFrame) -> float:
    """scorecardのrank係数(見つからなければ1.0=検証結果不明として中立に扱う)。

    同一指標が複数targetに対して異なるrankを持つ場合、最も低い(悪い)rank係数を
    採用する(統計的に弱い方の評価を優先する保守的な扱い)。
    """
    if scorecard_df.empty or "indicator" not in scorecard_df.columns:
        return 1.0
    rows = scorecard_df[scorecard_df["indicator"] == indicator_key]
    if rows.empty:
        return 1.0
    weights = [RANK_WEIGHTS.get(str(r), 0.0) for r in rows["rank"]]
    return min(weights) if weights else 1.0


def _clip_multiplier(m: float) -> float:
    return max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, m))


def update_weights(
    attributions: dict[str, list[tuple[bool, float | None]]],
    scorecard_df: pd.DataFrame,
    previous_multipliers: dict[str, float] | None = None,
    indicators: list[Indicator] | None = None,
) -> list[IndicatorWeightResult]:
    """全指標(indicators.csv登録分)についてlearned_multiplierを更新する。"""
    previous_multipliers = previous_multipliers or {}
    now_iso = datetime.now().isoformat()
    inds = indicators if indicators is not None else INDICATORS

    results: list[IndicatorWeightResult] = []
    for ind in inds:
        records = attributions.get(ind.key, [])
        n = len(records)
        hit_rate = (sum(1 for h, _ in records if h) / n) if n > 0 else None
        excess_values = [e for _, e in records if e is not None]
        avg_excess = (sum(excess_values) / len(excess_values)) if excess_values else None

        prev_multiplier = previous_multipliers.get(ind.key, MULTIPLIER_DEFAULT)
        if n >= MIN_EVALUATIONS_FOR_UPDATE and hit_rate is not None:
            multiplier = _clip_multiplier(
                prev_multiplier * (1 + LEARNING_RATE * 2 * (hit_rate - 0.5))
            )
        else:
            multiplier = prev_multiplier

        base_weight = DEFAULT_CONFIDENCE_WEIGHT[ind.data_quality]
        rank_weight = _rank_weight_for_indicator(ind.key, scorecard_df)
        effective_weight = base_weight * rank_weight * multiplier

        results.append(IndicatorWeightResult(
            indicator_key=ind.key,
            base_weight=base_weight,
            rank_weight=rank_weight,
            learned_multiplier=round(multiplier, 4),
            effective_weight=round(effective_weight, 4),
            n_evaluations=n,
            hit_rate=round(hit_rate, 3) if hit_rate is not None else None,
            avg_excess_when_cited=round(avg_excess, 4) if avg_excess is not None else None,
            updated_at=now_iso,
        ))

    return results


def _load_previous_multipliers(path: Path = INDICATOR_WEIGHTS_CSV) -> dict[str, float]:
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


def _save_weights_csv(
    results: list[IndicatorWeightResult], path: Path = INDICATOR_WEIGHTS_CSV,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [{
        "indicator_key": r.indicator_key, "base_weight": r.base_weight,
        "rank_weight": r.rank_weight, "learned_multiplier": r.learned_multiplier,
        "effective_weight": r.effective_weight, "n_evaluations": r.n_evaluations,
        "hit_rate": r.hit_rate, "avg_excess_when_cited": r.avg_excess_when_cited,
        "updated_at": r.updated_at,
    } for r in results]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved: %s (%d rows)", path, len(rows))


def run_weight_update(scorecard_path: Path | None = None) -> list[IndicatorWeightResult]:
    """Layer5指標重み自動更新を1回実行する。

    outputs/indicator_weights.csv・outputs/indicator_scorecard.csvはいずれも
    公開(保有銘柄の判断を含まない、指標検証の集計結果のみ)。
    """
    sc_path = scorecard_path or (OUTPUT_DIR / "indicator_scorecard.csv")
    scorecard_df = pd.read_csv(sc_path) if sc_path.exists() else pd.DataFrame()

    predictions = load_predictions()
    predictions_by_id = {p.prediction_id: p for p in predictions}
    evaluations = load_evaluations()

    attributions = compute_attributions(evaluations, predictions_by_id)
    previous_multipliers = _load_previous_multipliers()
    results = update_weights(attributions, scorecard_df, previous_multipliers)

    n_updated = sum(1 for r in results if r.n_evaluations >= MIN_EVALUATIONS_FOR_UPDATE)
    logger.info(
        "weight_updater: %d指標中%d指標が評価サンプル閾値(n>=%d)到達、multiplier更新",
        len(results), n_updated, MIN_EVALUATIONS_FOR_UPDATE,
    )

    try:
        _save_weights_csv(results)
    except Exception as exc:
        logger.warning("indicator_weights.csv save failed: %s", exc)

    return results
