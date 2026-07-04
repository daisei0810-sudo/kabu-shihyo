"""予測台帳への記帳(Step7前半)。

record_from_snapshot(): Step3出力(private/portfolio_signal_scores.csv)のoutlook/
actionをそのまま記帳する最小版(Investment OS Layer5、docs/investment_os_design.md
§5フェーズP1)。Layer2が未稼働のテーマ・銘柄のフォールバックとして引き続き有効。

record_from_decisions(): Layer2(src.decision)のDecisionRecord確定時に呼ばれる
push型の記帳(§8確定事項「L2→L5はpush型」、design§4.3で唯一許可されたレイヤー間
直接呼び出し)。prediction_idの採番方式(pred_{as_of}_{target})はsnapshot版と
共通のため、同日にLayer2が実行されればより根拠情報の濃いdecisionレコードが
upsertで自然に上書きする。

いずれもprediction_idが同日同targetで一定なので、同日複数回実行しても
上書きされるだけで重複しない(冪等)。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.config import INSTRUMENTS, PRIVATE_OUTPUTS
from src.decision.models import DecisionRecord
from src.prediction.models import Evaluation, Prediction
from src.prediction.prices import PROCESSED_DIR, price_at_or_before, resolve_price_series
from src.prediction.store import load_evaluations, upsert_evaluations, upsert_predictions
from src.prediction.taxonomy import ACTION_DIRECTION, L2_ACTION_DIRECTION, PREDICTION_HORIZONS
from src.registry.themes import benchmark_for

logger = logging.getLogger(__name__)

PRIVATE_DIR = Path(PRIVATE_OUTPUTS)
SIGNALS_CSV = PRIVATE_DIR / "portfolio_signal_scores.csv"

# 保有・非保有を問わずconfig.INSTRUMENTSに登録された銘柄keyのみ予測対象とする。
# xrp_real_demand/xrp_lock_demand等の集計行(銘柄ではない)を除外するため。
_INSTRUMENT_KEYS: frozenset[str] = frozenset(i.key for i in INSTRUMENTS)
_LAYER_BY_KEY: dict[str, str] = {i.key: i.layer.value for i in INSTRUMENTS}


def _load_signals(path: Path = SIGNALS_CSV) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("portfolio_signal_scores.csv load failed: %s", exc)
        return pd.DataFrame()


def build_predictions(
    signals_df: pd.DataFrame, as_of: date, processed_dir: Path = PROCESSED_DIR
) -> list[Prediction]:
    """当日の銘柄シグナルからPredictionを構築する。

    outlook/actionが空(集計行)や config.INSTRUMENTS に無い target は除外する。
    """
    if signals_df.empty:
        return []

    now_iso = datetime.now().isoformat()
    baseline_ts = pd.Timestamp(as_of)
    predictions: list[Prediction] = []

    for _, row in signals_df.iterrows():
        target = str(row.get("target", ""))
        if target not in _INSTRUMENT_KEYS:
            continue
        action = row.get("action")
        if pd.isna(action) or not str(action):
            continue

        judgment = str(action)
        theme = _LAYER_BY_KEY.get(target)
        series, is_proxy = resolve_price_series(target, processed_dir)
        baseline_price = price_at_or_before(series, baseline_ts) if series is not None else None

        score = row.get("extended_score")
        score = None if pd.isna(score) else float(score)
        confidence = row.get("confidence_pct")
        confidence = None if pd.isna(confidence) else float(confidence)

        predictions.append(Prediction(
            prediction_id=f"pred_{as_of.isoformat()}_{target}",
            created_at=now_iso,
            as_of=as_of.isoformat(),
            source_layer="portfolio_snapshot",
            theme=theme,
            target=target,
            judgment=judgment,
            expected_direction=ACTION_DIRECTION.get(judgment, 0),
            score_at_prediction=score,
            confidence_at_prediction=confidence,
            baseline_date=as_of.isoformat(),
            baseline_price=baseline_price,
            benchmark_key=benchmark_for(theme) if theme else None,
            benchmark_is_approximate=is_proxy,
        ))

    return predictions


def build_pending_evaluations(
    predictions: list[Prediction], existing_ids: set[str]
) -> list[Evaluation]:
    """新規Predictionに対しhorizonごとのpending Evaluationを生成する(既存分は上書きしない)。

    baseline_priceが取れなかった予測(非上場・proxy無し)は最初からskipped_no_dataとし、
    「評価待ち」に偽装しない。
    """
    new_evals: list[Evaluation] = []
    for pred in predictions:
        baseline_ts = pd.Timestamp(pred.baseline_date)
        for horizon, days in PREDICTION_HORIZONS.items():
            eval_id = f"{pred.prediction_id}_{horizon}"
            if eval_id in existing_ids:
                continue
            due = baseline_ts + timedelta(days=days)
            new_evals.append(Evaluation(
                evaluation_id=eval_id,
                prediction_id=pred.prediction_id,
                horizon=horizon,
                due_date=due.date().isoformat(),
                status="pending" if pred.baseline_price is not None else "skipped_no_data",
            ))
    return new_evals


def build_predictions_from_decisions(
    decisions: list[DecisionRecord], as_of: date, processed_dir: Path = PROCESSED_DIR
) -> list[Prediction]:
    """Layer2のDecisionRecordからPredictionを構築する(push型、§4.3の唯一の例外)。"""
    now_iso = datetime.now().isoformat()
    baseline_ts = pd.Timestamp(as_of)
    predictions: list[Prediction] = []

    for rec in decisions:
        series, is_proxy = resolve_price_series(rec.target, processed_dir)
        baseline_price = price_at_or_before(series, baseline_ts) if series is not None else None

        predictions.append(Prediction(
            prediction_id=f"pred_{as_of.isoformat()}_{rec.target}",
            created_at=now_iso,
            as_of=as_of.isoformat(),
            source_layer="decision",
            theme=rec.theme or None,
            target=rec.target,
            judgment=rec.action,
            expected_direction=L2_ACTION_DIRECTION.get(rec.action, 0),
            score_at_prediction=rec.theme_score,
            confidence_at_prediction=rec.confidence,
            baseline_date=as_of.isoformat(),
            baseline_price=baseline_price,
            benchmark_key=benchmark_for(rec.theme) if rec.theme else None,
            benchmark_is_approximate=is_proxy,
            evidence_json=json.dumps(rec.evidence_indicators, ensure_ascii=False),
        ))

    return predictions


def record_from_decisions(
    decisions: list[DecisionRecord], as_of: date | None = None,
    processed_dir: Path = PROCESSED_DIR,
) -> tuple[list[Prediction], list[Evaluation]]:
    """Layer2のDecisionRecordから当日分の予測を記帳し、pending評価を生成する。冪等。"""
    d = as_of or date.today()
    new_predictions = build_predictions_from_decisions(decisions, d, processed_dir)

    all_predictions = upsert_predictions(new_predictions)

    existing_ids = {e.evaluation_id for e in load_evaluations()}
    new_evals = build_pending_evaluations(new_predictions, existing_ids)
    all_evals = upsert_evaluations(new_evals)

    logger.info(
        "prediction ledger(decision push): 記帳%d件(累計%d件) / 新規評価待ち%d件(累計%d件)",
        len(new_predictions), len(all_predictions), len(new_evals), len(all_evals),
    )
    return all_predictions, all_evals


def record_from_snapshot(
    as_of: date | None = None, processed_dir: Path = PROCESSED_DIR
) -> tuple[list[Prediction], list[Evaluation]]:
    """Step3出力から当日分の予測を記帳し、pending評価を生成する。冪等。"""
    d = as_of or date.today()
    signals_df = _load_signals()
    new_predictions = build_predictions(signals_df, d, processed_dir)

    all_predictions = upsert_predictions(new_predictions)

    existing_ids = {e.evaluation_id for e in load_evaluations()}
    new_evals = build_pending_evaluations(new_predictions, existing_ids)
    all_evals = upsert_evaluations(new_evals)

    logger.info(
        "prediction ledger: 記帳%d件(累計%d件) / 新規評価待ち%d件(累計%d件)",
        len(new_predictions), len(all_predictions), len(new_evals), len(all_evals),
    )
    return all_predictions, all_evals
