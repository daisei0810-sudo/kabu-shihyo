"""重複検知ロジック(§6)。ML不使用・ローカル完結の3段階(完全一致→粗バケット→類似度)。

「表現は違うが同じ事実」の判定は、機械学習ではなく決定的なルール
(タイトルのトークンJaccard類似度 + 金額/数量の一致ボーナス)で行う。
閾値は誤って重複と判定して材料を握り潰す(=情報欠落)より、
見逃して新規扱いする(=多少の重複通知)方が実害が小さいという判断で、
保守的な既定値(0.72)を採用している。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from src.materials.models import Material, MaterialDraft, MaterialMatch
from src.materials.taxonomy import (
    DEDUP_SIMILARITY_THRESHOLD,
    RANK_ORDER,
    RENOTIFY_ALLOWED_RELATIONS,
    MaterialRelation,
)

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_NUMBER_RE = re.compile(r"\$?\d[\d,.]*\s?(b|m|億|兆|%)?", re.IGNORECASE)

# 新事実を示唆するキーワード(§6再通知条件のヒューリスティック検出)。
# NLPではなくキーワード一致による粗い判定。Phase6でニュース取得器実装時に
# より精密な検出ロジックへ拡張する前提。
_NEW_FACT_KEYWORDS: tuple[str, ...] = (
    "confirmed", "確定", "契約金額", "受注残", "ガイダンス修正", "guidance revised",
    "capex change", "補助金確定", "顧客側確認", "revised guidance", "backlog change",
)


def normalize_title(title: str) -> str:
    """タイトル正規化: 小文字化・記号除去・空白圧縮。"""
    t = title.lower()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def content_hash(title: str) -> str:
    """正規化タイトルのSHA1ハッシュ(完全一致検知用)。"""
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()


def dedup_bucket(company_token: str, topic_token: str, event_date: date) -> str:
    """company+topic+ISO週 の粗いバケットキー(近接日を同一バケットへ丸める)。"""
    iso = event_date.isocalendar()
    return f"{company_token}|{topic_token}|{iso[0]}{iso[1]:02d}"


def _tokens(title: str) -> set[str]:
    return set(normalize_title(title).split())


def _token_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _extract_numbers(text: str) -> set[str]:
    return {m.group(0).strip() for m in _NUMBER_RE.finditer(text)}


def _shared_key_numbers(a_text: str, b_text: str) -> bool:
    return bool(_extract_numbers(a_text) & _extract_numbers(b_text))


def has_new_fact_keywords(text: str) -> bool:
    """新事実を示唆するキーワードを含むか(§6/§7の new_fact_flag 判定に共用)。"""
    t = text.lower()
    return any(kw.lower() in t for kw in _NEW_FACT_KEYWORDS)


def classify_relation(new: MaterialDraft, old: Material) -> MaterialRelation:
    """新規材料が既存材料に対してどの関係かを分類する(§6)。"""
    if has_new_fact_keywords(new.summary):
        return MaterialRelation.UPDATE
    old_rank_order = RANK_ORDER.get(old.source_rank, RANK_ORDER["D"])
    new_rank_order = RANK_ORDER.get(new.source_rank.value, RANK_ORDER["D"])
    if new_rank_order < old_rank_order:
        # 上位ソースが後から同事実を報じた
        return MaterialRelation.SUPERSEDE
    return MaterialRelation.DUPLICATE


def detect_duplicate_material(
    new: MaterialDraft,
    new_bucket: str,
    existing: list[Material],
) -> MaterialMatch | None:
    """新規材料下書きと既存材料集合を照合する。重複/類似がなければ None。"""
    new_hash = content_hash(new.title)

    # STAGE 0: 完全一致(正規化タイトルのハッシュ一致)
    for m in existing:
        if m.content_hash == new_hash:
            return MaterialMatch(
                matched_id=m.material_id,
                relation=MaterialRelation.DUPLICATE,
                similarity=1.0,
                reason="正規化タイトル完全一致",
            )

    # STAGE 1: 粗バケット絞り込み(company+topic+同週)
    candidates = [m for m in existing if m.dedup_bucket == new_bucket]
    if not candidates:
        return None

    # STAGE 2: タイトル類似度 + 数値一致ボーナス
    new_tokens = _tokens(new.title)
    new_text = f"{new.title} {new.summary}"

    best: tuple[Material, float] | None = None
    for m in candidates:
        sim = _token_jaccard(new_tokens, _tokens(m.title))
        if _shared_key_numbers(new_text, f"{m.title} {m.summary or ''}"):
            sim = min(sim + 0.15, 1.0)
        if best is None or sim > best[1]:
            best = (m, sim)

    if best is not None and best[1] >= DEDUP_SIMILARITY_THRESHOLD:
        matched, sim = best
        relation = classify_relation(new, matched)
        return MaterialMatch(
            matched_id=matched.material_id,
            relation=relation,
            similarity=sim,
            reason=f"タイトル類似度 {sim:.2f}(同一company/topic/週バケット)",
        )
    return None


def should_renotify(match: MaterialMatch) -> bool:
    """再通知が許されるか(§6)。update/supersedeのみ許可、duplicate/confirmは禁止。"""
    return match.relation in RENOTIFY_ALLOWED_RELATIONS
