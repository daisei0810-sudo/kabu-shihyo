"""Layer9資金配分エンジン — ルールベース+相関ペナルティ(平均分散最適化は不採用)。

手順(docs/investment_os_design.md §4.9):
  raw_i = theme_score_i × (1 − risk_haircut_i)
  → 相関の高いテーマペアに集中ペナルティ → min/max クリップ → 正規化(現金枠を残す)
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.allocation.models import AllocationPolicy, AllocationResult

# risk_level(0-3) → ヘアカット率の上限。risk_level=3で最大50%減衰。
MAX_RISK_HAIRCUT = 0.5
MAX_RISK_LEVEL = 3


def _risk_haircut(risk_level: int) -> float:
    return min(risk_level, MAX_RISK_LEVEL) / MAX_RISK_LEVEL * MAX_RISK_HAIRCUT


def _apply_correlation_penalty(
    raw: dict[str, float],
    correlation_matrix: pd.DataFrame,
    threshold: float,
    penalty_factor: float,
) -> dict[str, float]:
    """相関が高いテーマペアについて、rawが小さい方にのみペナルティを掛ける。

    1テーマが複数ペアで対象になった場合は最も厳しい(最小の)ペナルティ乗数を採用する
    (重複適用による過度な減衰を避ける)。
    """
    if correlation_matrix.empty:
        return dict(raw)

    penalty_multiplier: dict[str, float] = dict.fromkeys(raw, 1.0)
    themes = [t for t in correlation_matrix.columns if t in raw]
    for i, a in enumerate(themes):
        for b in themes[i + 1:]:
            corr = correlation_matrix.loc[a, b]
            if pd.isna(corr) or abs(corr) < threshold:
                continue
            weaker = a if raw[a] <= raw[b] else b
            penalty_multiplier[weaker] = min(penalty_multiplier[weaker], penalty_factor)

    return {theme: raw[theme] * penalty_multiplier[theme] for theme in raw}


def _normalize_and_clip(
    raw: dict[str, float], policy: AllocationPolicy,
) -> dict[str, float]:
    """(100 - cash_floor_pct)へ正規化した後、min/max_pctへ収まるまで反復的にクリップ+
    再分配する(ウォーターフィル方式)。

    1回のクリップ+再分配だけでは、再分配後に別のテーマが新たに上限を超える
    (連鎖的なクリップ)ケースを取りこぼす。全テーマが上限/下限に固定されるまで
    反復することで、常に budget 以内かつ各テーマがmin/max_pctを満たす解へ収束する
    (全テーマが上限に達した場合、余剰は現金に回る=意図的な仕様)。
    """
    total_raw = sum(raw.values())
    budget = 100.0 - policy.cash_floor_pct
    if total_raw <= 0 or budget <= 0:
        return dict.fromkeys(raw, 0.0)

    result = {theme: v / total_raw * budget for theme, v in raw.items()}
    fixed: set[str] = set()

    for _ in range(len(result) + 1):
        changed = False
        for theme, w in list(result.items()):
            if theme in fixed:
                continue
            if w > policy.max_pct:
                result[theme] = policy.max_pct
                fixed.add(theme)
                changed = True
            elif w < policy.min_pct:
                result[theme] = policy.min_pct
                fixed.add(theme)
                changed = True
        if not changed:
            break

        remaining_budget = budget - sum(result[t] for t in fixed)
        free_themes = [t for t in result if t not in fixed]
        free_raw_total = sum(raw[t] for t in free_themes)
        if not free_themes or free_raw_total <= 0:
            break
        for t in free_themes:
            result[t] = raw[t] / free_raw_total * remaining_budget

    return result


def compute_allocations(
    theme_scores_df: pd.DataFrame,
    risk_by_theme_df: pd.DataFrame,
    correlation_matrix: pd.DataFrame,
    policy: AllocationPolicy,
    holdings_df: pd.DataFrame | None = None,
    as_of: date | None = None,
) -> list[AllocationResult]:
    """テーマごとの推奨配分を計算する。theme_scoreが無いテーマは配分対象外(None)。"""
    d = as_of or date.today()

    risk_level_by_theme: dict[str, int] = {}
    if risk_by_theme_df is not None and not risk_by_theme_df.empty:
        for _, row in risk_by_theme_df.iterrows():
            risk_level_by_theme[str(row["theme"])] = int(row.get("risk_level", 0))

    current_pct_by_theme: dict[str, float] = {}
    if holdings_df is not None and not holdings_df.empty:
        for _, row in holdings_df.iterrows():
            pct = row.get("current_pct")
            if pd.notna(pct):
                current_pct_by_theme[str(row["theme"])] = float(pct)

    raw: dict[str, float] = {}
    theme_score_by_theme: dict[str, float | None] = {}
    confidence_by_theme: dict[str, float] = {}
    haircut_by_theme: dict[str, float] = {}

    for _, row in theme_scores_df.iterrows():
        theme = str(row["theme"])
        score = row.get("total")
        if pd.isna(score):
            theme_score_by_theme[theme] = None
            continue
        risk_level = risk_level_by_theme.get(theme, 0)
        haircut = _risk_haircut(risk_level)
        raw[theme] = float(score) * (1 - haircut)
        theme_score_by_theme[theme] = float(score)
        haircut_by_theme[theme] = haircut
        confidence_by_theme[theme] = float(row.get("confidence_pct") or 0.0)

    penalized = _apply_correlation_penalty(
        raw, correlation_matrix,
        policy.correlation_penalty_threshold, policy.correlation_penalty_factor,
    )
    recommended = _normalize_and_clip(penalized, policy)

    results: list[AllocationResult] = []
    all_themes = sorted(set(theme_score_by_theme) | set(current_pct_by_theme))
    for theme in all_themes:
        score = theme_score_by_theme.get(theme)
        rec_pct = round(recommended[theme], 1) if theme in recommended else None
        cur_pct = current_pct_by_theme.get(theme)
        diff_pct = (
            round(rec_pct - cur_pct, 1) if rec_pct is not None and cur_pct is not None else None
        )

        penalty_applied = (
            theme in raw and theme in penalized and abs(raw[theme] - penalized[theme]) > 1e-9
        )
        rationale_parts = []
        if score is not None:
            rationale_parts.append(f"テーマスコア{score:.0f}")
            haircut = haircut_by_theme.get(theme, 0.0)
            if haircut > 0:
                rationale_parts.append(f"リスクヘアカット-{haircut:.0%}")
            if penalty_applied:
                rationale_parts.append("相関集中ペナルティ適用")
        else:
            rationale_parts.append("テーマスコア未算出のため配分対象外")

        results.append(AllocationResult(
            theme=theme,
            theme_score=score,
            risk_haircut=haircut_by_theme.get(theme, 0.0),
            recommended_pct=rec_pct,
            current_pct=cur_pct,
            diff_pct=diff_pct,
            rationale=" / ".join(rationale_parts),
            confidence=confidence_by_theme.get(theme, 0.0),
            as_of=d.isoformat(),
        ))

    return results
