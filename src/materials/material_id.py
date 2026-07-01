"""material_id の決定的生成アルゴリズム(§6)。

例: MICRON_HBM_LTA_20260624 / TSMC_COWOS_EXPANSION_20260701

生成規則: f"{COMPANY}_{TOPIC}_{YYYYMMDD}" (衝突時は _2, _3 ... サフィックス)。
人間の解釈に頼らず、辞書 + 正規化で決定的に生成する。
"""

from __future__ import annotations

import re
from datetime import date

from src.config import INSTRUMENTS

_MAX_TOKEN_LEN = 16

# 別名/表記ゆれ → 正規トークン。config.INSTRUMENTS から自動シードし、
# 手動エントリで上書き・補完する(手動優先)。
_MANUAL_COMPANY_ALIASES: dict[str, str] = {
    "micron technology": "MICRON",
    "micron": "MICRON",
    "openai": "OPENAI",
    "tsmc": "TSMC",
    "taiwan semiconductor": "TSMC",
    "nvidia": "NVIDIA",
    "エヌビディア": "NVIDIA",
    "microsoft": "MICROSOFT",
    "meta": "META",
    "amazon": "AMAZON",
    "google": "GOOGLE",
    "alphabet": "GOOGLE",
    "oracle": "ORACLE",
    "xai": "XAI",
    "coreweave": "COREWEAVE",
}


def _seed_company_aliases() -> dict[str, str]:
    """config.INSTRUMENTS の name_ja / ticker から企業別名辞書を自動生成。"""
    aliases: dict[str, str] = {}
    for inst in INSTRUMENTS:
        token = re.sub(r"[^A-Z0-9]", "", inst.key.upper())[:_MAX_TOKEN_LEN] or "UNKNOWNCO"
        aliases[inst.name_ja.lower()] = token
        if inst.ticker:
            ticker_key = inst.ticker.split(".")[0].lower()
            aliases[ticker_key] = token
    # 手動辞書で上書き(手動優先)
    aliases.update(_MANUAL_COMPANY_ALIASES)
    return aliases


COMPANY_ALIASES: dict[str, str] = _seed_company_aliases()

# タイトル/サマリー内キーワード → トピックトークン。定義順=優先順位(先勝ち)。
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "HBM_LTA": ["hbm long-term", "hbm lta", "hbm長期契約", "hbm 長期契約"],
    "HBM": ["hbm"],
    "CAPEX": ["capex", "capital expenditure", "設備投資"],
    "COWOS_EXPANSION": ["cowos", "advanced packaging", "先端パッケージ"],
    "STARGATE_CAPEX": ["stargate"],
    "GUIDANCE": ["guidance", "outlook", "業績予想", "ガイダンス"],
    "ORDER_BACKLOG": ["backlog", "受注残", "order backlog"],
    "SUBSIDY": ["subsidy", "chips act", "補助金", "grant"],
    "EXPORT_CONTROL": ["export control", "export restriction", "輸出規制"],
    "EARNINGS": ["earnings", "quarterly results", "決算"],
    "IPO": ["ipo", "listing", "上場"],
}

_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "with",
    "is", "are", "at", "by", "from", "as",
})
_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _normalize_company(company: str) -> str:
    key = company.strip().lower()
    if key in COMPANY_ALIASES:
        return COMPANY_ALIASES[key]
    fallback = re.sub(r"[^A-Za-z0-9]", "", company.upper())[:_MAX_TOKEN_LEN]
    return fallback or "UNKNOWNCO"


def _classify_topic(title: str, summary: str = "") -> str:
    text = f"{title} {summary}".lower()
    best_topic: str | None = None
    best_hits = 0
    for topic, keywords in TOPIC_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best_hits = hits
            best_topic = topic
    if best_topic is not None:
        return best_topic

    # フォールバック: タイトル内で最初に現れる非ストップワードの英単語
    for word in _WORD_RE.findall(title):
        if word.lower() not in _STOPWORDS:
            return word.upper()[:_MAX_TOKEN_LEN]
    return "MISC"


def generate_material_id(
    company: str,
    title: str,
    event_date: date,
    existing_ids: set[str],
    summary: str = "",
) -> str:
    """決定的に material_id を生成する。

    同じ (company, title/summary の分類結果, event_date, existing_ids) からは
    常に同じIDが得られる。衝突(同社・同トピック・同日の別事実)時はサフィックスを付与。

    注意: この関数は「新規材料と確定した後」に呼ぶこと。重複検知(dedup.py)を
    先に通し、同一事実なら既存IDを再利用する運用を前提とする。
    """
    company_tok = _normalize_company(company)
    topic_tok = _classify_topic(title, summary)
    date_str = event_date.strftime("%Y%m%d")

    base = f"{company_tok}_{topic_tok}_{date_str}"
    if base not in existing_ids:
        return base
    for n in range(2, 100):
        candidate = f"{base}_{n}"
        if candidate not in existing_ids:
            return candidate
    msg = f"material_id 衝突が異常に多い: {base}"
    raise RuntimeError(msg)
