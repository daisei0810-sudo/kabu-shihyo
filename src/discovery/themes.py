"""Layer8 New Theme Discovery — 新テーマ候補(`themes.csv` の `status=watch`)を追跡する。

無料データソースでTAM(市場規模)・成長率を検証できないため、これらは
「未整備」と正直に返し捏造しない(既存方針)。現状唯一の定量シグナルは
materials(取込済み材料)のキーワード出現件数トレンドのみ。

未登録テーマの自動発掘(クラスタリング等によるゼロからのテーマ発見)は、
現状のmaterials蓄積量(2026-07時点で一桁件)では統計的意味を持たないため
実装しない。新テーマを監視対象にしたい場合は `config/themes.csv` へ
`status=watch` で手動登録することで、本モジュールが自動的に追跡し始める。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from src.config import MATERIALS_DB, MATERIALS_DUMP_DIR
from src.registry.themes import Theme, load_themes
from src.scoring.theme_score import load_materials_conn

logger = logging.getLogger(__name__)

MATERIALS_LOOKBACK_DAYS = 90

# テーマ別キーワード(themes.csvにキーワード列が無いため当面ここで手動管理)。
# status=watch のテーマのみ対象(activeテーマは既にtheme_score.pyで追跡済み)。
WATCH_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bio": ("バイオ", "創薬", "治験", "FDA承認", "再生医療", "遺伝子治療", "抗体医薬", "臨床試験"),
}


@dataclass
class DiscoveryTheme:
    """新テーマ候補1件の追跡結果。"""

    theme: str
    name_ja: str
    materials_count: int
    materials_trend_note: str
    tam_estimate: str
    growth_rate: str
    feasibility: str
    candidates: str
    time_horizon: str
    data_quality: str
    as_of: str


def _count_keyword_materials(
    conn: sqlite3.Connection, keywords: tuple[str, ...], lookback_days: int, as_of: date,
) -> int:
    since = (as_of - timedelta(days=lookback_days)).isoformat()
    cur = conn.execute(
        "SELECT title, summary FROM materials WHERE published_at >= ?", (since,)
    )
    count = 0
    for title, summary in cur.fetchall():
        text = f"{title or ''} {summary or ''}"
        if any(kw in text for kw in keywords):
            count += 1
    return count


def compute_discovery_themes(
    as_of: date | None = None,
    materials_conn: sqlite3.Connection | None = None,
    themes: list[Theme] | None = None,
) -> list[DiscoveryTheme]:
    """`status=watch` のテーマを材料出現件数トレンド付きで追跡する。"""
    d = as_of or date.today()
    watch_themes = [t for t in (themes or load_themes()) if t.status == "watch"]

    results: list[DiscoveryTheme] = []
    for t in watch_themes:
        keywords = WATCH_THEME_KEYWORDS.get(t.key, ())
        if materials_conn is not None and keywords:
            count = _count_keyword_materials(materials_conn, keywords, MATERIALS_LOOKBACK_DAYS, d)
            trend_note = f"直近{MATERIALS_LOOKBACK_DAYS}日で関連材料{count}件(キーワード一致)"
            data_quality = "estimated" if count > 0 else "unavailable"
        elif not keywords:
            count = 0
            trend_note = "キーワード未定義のため集計不可(WATCH_THEME_KEYWORDSへ追加が必要)"
            data_quality = "unavailable"
        else:
            count = 0
            trend_note = "materials.db未取得(--step 5未実行)"
            data_quality = "unavailable"

        results.append(DiscoveryTheme(
            theme=t.key,
            name_ja=t.name_ja,
            materials_count=count,
            materials_trend_note=trend_note,
            tam_estimate="未整備(無料データソースなし、estimated化は捏造回避のため見送り)",
            growth_rate="未整備",
            feasibility="未整備",
            candidates=t.note or "(手動入力なし)",
            time_horizon="未定",
            data_quality=data_quality,
            as_of=d.isoformat(),
        ))

    return results


def load_and_compute_discovery_themes(as_of: date | None = None) -> list[DiscoveryTheme]:
    """materials.db をJSONLから再構築して接続した上で計算する(パイプライン用)。"""
    conn = load_materials_conn(MATERIALS_DB, MATERIALS_DUMP_DIR)
    try:
        return compute_discovery_themes(as_of=as_of, materials_conn=conn)
    finally:
        if conn is not None:
            conn.close()
