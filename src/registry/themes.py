"""config/themes.csv → テーマ(サイクル)マスタの読み込み。

Investment OS Layer1(5大サイクル: AI/Physical AI/量子/電力/XRP)のテーマ一覧と、
Layer5(予測検証)が超過リターン算出に使うベンチマーク指数の対応を保持する。

既存の `src.config.Layer` enum・`src.notifications.taxonomy.LAYER_BENCHMARK` は
このモジュールへの置き換えは行わない(影響範囲を広げないため)。本モジュールは
Layer5以降の新規コードが「テーマ→ベンチマーク」を引く際の正本として使う。
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel

DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "themes.csv"


class Theme(BaseModel):
    """投資テーマ(サイクル)。"""

    key: str                        # src.config.Layer の値と対応する
    name_ja: str
    status: str = "active"          # "active" | "watch"(バイオ等、優先度低で監視のみ)
    benchmark_key: str | None = None  # 予測評価の超過リターン算出に使うベンチマーク指数
    note: str = ""


def load_themes(csv_path: str | Path = DEFAULT_CSV_PATH) -> list[Theme]:
    """config/themes.csv を読み込み Theme のリストを返す。"""
    path = Path(csv_path)
    themes: list[Theme] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            themes.append(Theme(
                key=row["key"],
                name_ja=row["name_ja"],
                status=row["status"] or "active",
                benchmark_key=row["benchmark_key"] or None,
                note=row["note"],
            ))
    return themes


def themes_by_key(csv_path: str | Path = DEFAULT_CSV_PATH) -> dict[str, Theme]:
    """key → Theme の辞書を返す。"""
    return {t.key: t for t in load_themes(csv_path)}


def benchmark_for(theme_key: str, csv_path: str | Path = DEFAULT_CSV_PATH) -> str | None:
    """テーマのベンチマーク指数キーを返す。未定義または該当なしなら None。"""
    theme = themes_by_key(csv_path).get(theme_key)
    return theme.benchmark_key if theme else None
