"""シナリオ条件の評価 — 指標の特徴量を実データから読み、閾値と比較する。

`indicator`列が"risk:<category>"形式の場合はLayer6(risk_scores)のルックアップを
参照する(design§4.7「L2のbearシナリオ条件にrisk.categoryを参照する条件型を追加」)。
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, INDICATORS
from src.decision.models import ConditionDef, ConditionStatus
from src.features.engineer import FeatureEngineer
from src.indicator_loader import load_indicator_series

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)
_INDICATORS_BY_KEY = {ind.key: ind for ind in INDICATORS}
_ENGINEER = FeatureEngineer()

# feature名 → FeatureEngineer.build_indicator_features() が生成する列サフィックス。
# "level" は特殊扱い(生値そのもの、特徴量エンジニアリング不要)。
_FEATURE_SUFFIX: dict[str, str] = {
    "zscore": "zscore",
    "yoy": "yoy252",
    "mom": "mom21",
    "dz": "dz",
}

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
    "abs_lt": lambda v, t: abs(v) < t,   # |value| < threshold (「強い方向感なし」の判定用)
}


def latest_feature_value(series: pd.Series, feature: str) -> float | None:
    """系列から指定特徴量の最新値を取り出す。既存FeatureEngineerの計算をそのまま使う。"""
    if feature == "level":
        s = series.dropna()
        return float(s.iloc[-1]) if not s.empty else None

    suffix = _FEATURE_SUFFIX.get(feature)
    if suffix is None:
        logger.warning("未知のfeature: %s", feature)
        return None

    feats = _ENGINEER.build_indicator_features(series, "x")
    col = f"x_{suffix}"
    if col not in feats.columns:
        return None
    val = feats[col].dropna()
    return float(val.iloc[-1]) if not val.empty else None


RISK_INDICATOR_PREFIX = "risk:"


def evaluate_condition(
    cond: ConditionDef,
    target_key: str,
    as_of: date,
    processed_dir: Path = PROCESSED_DIR,
    risk_scores: dict[tuple[str, str], float] | None = None,
) -> ConditionStatus:
    """1条件を実データで評価する。指標が無い/データが無い場合はmet=None(観測不能)。

    cond.indicatorが"risk:<category>"形式の場合はLayer6のrisk_scoresルックアップ
    (target_key, category) -> risk_score(0-100) を参照する(risk_scores未指定/該当
    無しの場合は観測不能)。
    """
    if cond.indicator.startswith(RISK_INDICATOR_PREFIX):
        category = cond.indicator.removeprefix(RISK_INDICATOR_PREFIX)
        value = risk_scores.get((target_key, category)) if risk_scores else None
        if value is None:
            return ConditionStatus(
                condition_id=cond.condition_id, desc=cond.desc, indicator_key=cond.indicator,
                measured_value=None, threshold=cond.threshold, met=None,
                data_quality="unavailable", as_of=as_of.isoformat(),
            )
        op_fn = _OPS.get(cond.op)
        met = op_fn(value, cond.threshold) if op_fn is not None else None
        return ConditionStatus(
            condition_id=cond.condition_id, desc=cond.desc, indicator_key=cond.indicator,
            measured_value=value, threshold=cond.threshold, met=met,
            data_quality="estimated", as_of=as_of.isoformat(),
        )

    ind = _INDICATORS_BY_KEY.get(cond.indicator)
    if ind is None:
        return ConditionStatus(
            condition_id=cond.condition_id, desc=cond.desc, indicator_key=cond.indicator,
            measured_value=None, threshold=cond.threshold, met=None,
            data_quality="unavailable", as_of=as_of.isoformat(),
        )

    series = load_indicator_series(ind, target_key, processed_dir, respect_step2_flag=False)
    if series is None or series.empty:
        return ConditionStatus(
            condition_id=cond.condition_id, desc=cond.desc, indicator_key=cond.indicator,
            measured_value=None, threshold=cond.threshold, met=None,
            data_quality=ind.data_quality.value, as_of=as_of.isoformat(),
        )

    value = latest_feature_value(series, cond.feature)
    op_fn = _OPS.get(cond.op)
    met = op_fn(value, cond.threshold) if (value is not None and op_fn is not None) else None

    return ConditionStatus(
        condition_id=cond.condition_id, desc=cond.desc, indicator_key=cond.indicator,
        measured_value=value, threshold=cond.threshold, met=met,
        data_quality=ind.data_quality.value, as_of=as_of.isoformat(),
    )
