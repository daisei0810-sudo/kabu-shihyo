"""Layer5 予測台帳パイプライン(Step7) — 記帳→評価→サマリー保存を統括する。

実行順序:
  ①当日分の予測を記帳(record_from_snapshot) → ②期日到来分を評価(evaluate_due) →
  ③サマリーを outputs/prediction_accuracy.csv へ保存
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS
from src.prediction.evaluator import evaluate_due, summarize
from src.prediction.ledger import record_from_snapshot
from src.prediction.models import PredictionAccuracySummary
from src.prediction.store import load_predictions, upsert_evaluations

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)


def _save_summary_csv(summary: PredictionAccuracySummary) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "prediction_accuracy.csv"
    pd.DataFrame([{
        "n_predictions": summary.n_predictions,
        "n_pending_evaluations": summary.n_pending_evaluations,
        "n_evaluated": summary.n_evaluated,
        "n_skipped": summary.n_skipped,
        "hit_rate": summary.hit_rate,
        "avg_excess_return": summary.avg_excess_return,
        "next_due_date": summary.next_due_date,
        "generated_at": datetime.now().isoformat(),
    }]).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved: %s", path)


def run_predictions(today: date | None = None) -> PredictionAccuracySummary:
    """予測台帳パイプラインを1回実行する。失敗しても例外は上げず、可能な範囲で継続する。"""
    d = today or date.today()

    all_predictions, all_evals = record_from_snapshot(as_of=d)

    predictions_by_id = {p.prediction_id: p for p in all_predictions}
    due_updates = evaluate_due(all_evals, predictions_by_id, today=d)
    all_evals = upsert_evaluations(due_updates)

    n_predictions = len(load_predictions())
    summary = summarize(all_evals, n_predictions)
    logger.info(
        "prediction evaluations: 評価待ち%d件 / 評価済み%d件(的中率=%s) / データ無し%d件",
        summary.n_pending_evaluations, summary.n_evaluated, summary.hit_rate,
        summary.n_skipped,
    )

    try:
        _save_summary_csv(summary)
    except Exception as exc:
        logger.warning("prediction_accuracy.csv save failed: %s", exc)

    return summary
