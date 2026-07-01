"""コンポーネント加重平均 + confidence の汎用パターン。

xrp_scores.py で実証済みの設計思想を汎用化したもの:
  confidence = (利用可能コンポーネントの重み合計) / (全コンポーネントの重み合計)
  unavailable コンポーネントは score=None だが weight は分母に含めるため、
  取得不可な部分が多いほど自動的に confidence が下がる。推測でスコアを埋めない。

Phase7(demand_index.py, cycle_scores.py)がこのパターンを再利用する。
xrp_scores.py は既存の公開API(XrpComponentScore, _aggregate_components, _make_component)
を維持したまま、内部実装をここへ委譲する(挙動不変、破壊的変更なし)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.scoring.normalizer import score_from_series


@dataclass
class ComponentScore:
    """1コンポーネントのスコア・重み・可用性。"""

    name: str
    score: float | None       # 0-100, None = 取得不可
    weight: float             # 理論重み(全コンポーネント合計が分母)
    available: bool
    data_quality: str         # "verified" / "proxy" / "estimated" / "unavailable"
    note: str = ""


@dataclass
class AggregateResult:
    """コンポーネント群を集約した結果。"""

    score: float | None
    confidence_pct: float
    data_coverage_pct: float
    components: list[ComponentScore] = field(default_factory=list)
    note: str = ""


def make_component_from_series(
    name: str,
    series: pd.Series | None,
    latest: float | None,
    weight: float,
    data_quality: str = "verified",
    note: str = "",
) -> ComponentScore:
    """時系列と最新値からコンポーネントを作る。データなしは available=False。"""
    if series is None or latest is None:
        return ComponentScore(
            name=name, score=None, weight=weight,
            available=False, data_quality=data_quality,
            note=note or "データファイルなし",
        )
    score_val, method_note = score_from_series(series, latest)
    full_note = f"{note} [{method_note}]" if note else method_note
    return ComponentScore(
        name=name,
        score=score_val,
        weight=weight,
        available=score_val is not None,
        data_quality=data_quality,
        note=full_note,
    )


def aggregate_components(
    components: list[ComponentScore],
    label: str,
) -> AggregateResult:
    """コンポーネントリストから総合スコアと confidence を計算。

    confidence = 利用可能重み / 全重み合計 (unavailable が分母に入ることで自然に低下)。
    """
    available = [c for c in components if c.available and c.score is not None]
    total_weight = sum(c.weight for c in components)
    avail_weight = sum(c.weight for c in available)

    if not available or avail_weight == 0:
        return AggregateResult(
            score=None,
            confidence_pct=0.0,
            data_coverage_pct=0.0,
            components=components,
            note=f"{label}: 利用可能データなし",
        )

    weighted_sum = sum(
        c.score * c.weight for c in available if c.score is not None
    )
    score = weighted_sum / avail_weight
    confidence = avail_weight / total_weight if total_weight > 0 else 0.0

    verified_avail = sum(
        1 for c in components if c.available and c.data_quality == "verified"
    )
    data_coverage = verified_avail / len(components) if components else 0.0

    return AggregateResult(
        score=round(score, 1),
        confidence_pct=round(confidence, 3),
        data_coverage_pct=round(data_coverage, 3),
        components=components,
    )
