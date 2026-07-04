"""Layer2 意思決定パイプライン(Step8) — 判定→前回差分→非公開保存→L5記帳を統括する。

実行順序(冪等性のため厳守):
  ①当日のDecisionRecordを生成(engine.decide) → ②前回スナップショットを読込 →
  ③差分を付加(diff.attach_change_context) → ④private/decisions/へ保存 →
  ⑤prediction台帳へpush型記帳(L2→L5、§8確定事項)
"""

from __future__ import annotations

import logging
from datetime import date

from src.decision.diff import attach_change_context
from src.decision.engine import decide
from src.decision.models import DecisionRecord
from src.decision.store import PRIVATE_DECISIONS_DIR, load_previous, save_decisions

logger = logging.getLogger(__name__)


def run_decisions(as_of: date | None = None) -> list[DecisionRecord]:
    """Layer2パイプラインを1回実行する。失敗しても例外は上げず、可能な範囲で継続する。"""
    d = as_of or date.today()

    records = decide(d)
    if not records:
        logger.info("decision: 対象レコードなし(portfolio_signal_scores.csv未生成の可能性)")
        return records

    prev = load_previous(d)
    records = attach_change_context(records, prev)
    save_decisions(records, d)

    n_changed = sum(1 for r in records if r.change_reason)
    logger.info(
        "decision: %d件記帳(%s) / 前回比変化%d件",
        len(records), PRIVATE_DECISIONS_DIR, n_changed,
    )

    try:
        from src.prediction.ledger import record_from_decisions
        record_from_decisions(records, d)
    except Exception as exc:
        logger.warning("prediction台帳への記帳失敗(decisionは保存済み): %s", exc)

    return records
