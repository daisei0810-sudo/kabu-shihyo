"""ソース信頼性ランクの付与・判定ロジック(§8)。

DataQuality(指標の取得品質)とは別軸。source_rankは「材料の発信元がどれだけ
信頼できるか」を表し、既存の confidence_pct 計算(engine.py)には合流させない。
"""

from __future__ import annotations

import re

from src.materials.taxonomy import SOURCE_TYPE_TO_RANK, SourceRank, SourceType


def assign_source_rank(
    source_type: SourceType | str,
    is_customer_official: bool = False,
) -> SourceRank:
    """情報源種別 → ソース信頼性ランク(§8)。顧客公式発表は常にA。"""
    if is_customer_official:
        return SourceRank.A
    st = SourceType(source_type) if not isinstance(source_type, SourceType) else source_type
    return SOURCE_TYPE_TO_RANK.get(st, SourceRank.D)


# ドメイン → source_type の簡易推定パターン(初期セット。運用しながら拡充する)
_DOMAIN_PATTERNS: list[tuple[str, SourceType]] = [
    (r"sec\.gov", SourceType.SEC),
    (r"(^|\.)reuters\.com$", SourceType.WIRE),
    (r"(^|\.)bloomberg\.com$", SourceType.WIRE),
    (r"(^|\.)nikkei\.com$", SourceType.WIRE),
    (r"(^|\.)go\.jp$", SourceType.GOV),
    (r"(^|\.)meti\.go\.jp$", SourceType.GOV),
    (r"(^|\.)release\.tdnet\.info$", SourceType.EXCHANGE),
    (r"(^|\.)prtimes\.jp$", SourceType.TRADE_PRESS),
]


def infer_source_type(domain: str) -> SourceType:
    """ドメイン文字列から source_type を推定。未登録なら unknown。"""
    d = domain.lower().strip()
    for pattern, stype in _DOMAIN_PATTERNS:
        if re.search(pattern, d):
            return stype
    return SourceType.UNKNOWN


def can_affect_decision(
    rank: SourceRank | str,
    corroborating_ranks: list[SourceRank | str] | None = None,
    is_customer_official: bool = False,
) -> bool:
    """材料が投資判断変更に使用できるか(§8運用ルール)。

    D単独は不可。C単独は不可(A/Bの裏付け、または顧客側確認があれば可)。
    """
    r = SourceRank(rank) if not isinstance(rank, SourceRank) else rank
    if r == SourceRank.D:
        return False
    if r == SourceRank.C:
        if is_customer_official:
            return True
        corroborating = corroborating_ranks or []
        normalized = [
            SourceRank(x) if not isinstance(x, SourceRank) else x for x in corroborating
        ]
        return any(x in (SourceRank.A, SourceRank.B) for x in normalized)
    return True
