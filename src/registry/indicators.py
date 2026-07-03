"""config/indicators.csv → Indicator一覧の読み込み(Layer3指標辞書の外部化)。

正本は config/indicators.csv。`src.config.INDICATORS` はこの load_indicators() の
戻り値をモジュール読み込み時に1回束縛するだけであり、既存の
`from src.config import INDICATORS` を参照する全コードは変更不要。

Investment OS Layer3 が要求する重要度・観測性・鮮度SLA・代替指標の属性は、
per-indicatorの手動評価による恣意的な数値付けを避けるため、既存の data_quality/
freq から**プログラム的に導出する**(config.py冒頭の「推測で断定しない」思想を踏襲)。
CSV側に列を増やして手動上書きしたくなった場合はこのモジュールの導出ロジックを
差し替えればよい(CSVスキーマ自体は変更不要)。
"""

from __future__ import annotations

import csv
from pathlib import Path

from src.config import DataQuality, DataSource, Indicator, Layer

DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "indicators.csv"

# data_quality → 観測性(observability)。Layer3属性: direct/proxy/manual/none。
# verified=一次データを直接観測、proxy=代理指標、estimated=ニュース/イベント頻度からの
# 定性推定(=手動判断寄り)、unavailable=無料では観測不可。
OBSERVABILITY_BY_QUALITY: dict[DataQuality, str] = {
    DataQuality.VERIFIED: "direct",
    DataQuality.PROXY: "proxy",
    DataQuality.ESTIMATED: "manual",
    DataQuality.UNAVAILABLE: "none",
}

# data_quality → 投資重要度(importance, 1-5)の既定値。個別指標ごとの恣意的な
# 手動採点は行わず、confidence_weightと同じ「取得品質が高いほど重要」という
# 一貫した基準で機械的に導出する。手動での重み付けはLayer5(予測検証)の
# 指標重み自動更新に委ねる。
IMPORTANCE_BY_QUALITY: dict[DataQuality, int] = {
    DataQuality.VERIFIED: 5,
    DataQuality.PROXY: 3,
    DataQuality.ESTIMATED: 2,
    DataQuality.UNAVAILABLE: 1,
}

# freq → 鮮度SLA(日数)。日次データは3日、月次データは(月次発表ラグを見込み)45日。
FRESHNESS_SLA_DAYS_BY_FREQ: dict[str, int] = {
    "daily": 3,
    "monthly": 45,
}


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "yes")


def load_indicators(csv_path: str | Path = DEFAULT_CSV_PATH) -> list[Indicator]:
    """config/indicators.csv を読み込み Indicator のリストを返す。"""
    path = Path(csv_path)
    indicators: list[Indicator] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            targets = [t for t in row["targets"].split(";") if t]
            indicators.append(Indicator(
                key=row["key"],
                name_ja=row["name_ja"],
                layer=Layer(row["layer"]),
                source=DataSource(row["source"]),
                data_quality=DataQuality(row["data_quality"]),
                targets=targets,
                note=row["note"],
                parquet_stem=row["parquet_stem"] or None,
                column=row["column"] or None,
                loader=row["loader"] or None,
                step2_verifiable=_parse_bool(row["step2_verifiable"]),
                freq=row["freq"] or "daily",
            ))
    return indicators


def importance(indicator: Indicator) -> int:
    """投資重要度(1-5)。data_quality から導出する既定値。"""
    return IMPORTANCE_BY_QUALITY[indicator.data_quality]


def observability(indicator: Indicator) -> str:
    """観測性(direct/proxy/manual/none)。data_quality から導出する既定値。"""
    return OBSERVABILITY_BY_QUALITY[indicator.data_quality]


def freshness_sla_days(indicator: Indicator) -> int:
    """鮮度SLA(日数)。freq から導出する既定値。この日数を超えたら鮮度低下とみなす。"""
    return FRESHNESS_SLA_DAYS_BY_FREQ.get(indicator.freq, 3)
