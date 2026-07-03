"""config/instruments.csv → Instrument一覧の読み込み(Layer1銘柄マスタの外部化)。

正本は config/instruments.csv。`src.config.INSTRUMENTS` はこの load_instruments() の
戻り値をモジュール読み込み時に1回束縛するだけであり、既存の
`from src.config import INSTRUMENTS` を参照する全コードは変更不要。

銘柄追加はこのCSVへ1行追加するだけで、価格取得(yfinance)・スコアリング・
判断・レポート表示まで自動的に伝播する設計(Investment OS Layer1)。
"""

from __future__ import annotations

import csv
from pathlib import Path

from src.config import DataQuality, Instrument, Layer

DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "instruments.csv"


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "yes")


def load_instruments(csv_path: str | Path = DEFAULT_CSV_PATH) -> list[Instrument]:
    """config/instruments.csv を読み込み Instrument のリストを返す。"""
    path = Path(csv_path)
    instruments: list[Instrument] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            instruments.append(Instrument(
                key=row["key"],
                name_ja=row["name_ja"],
                layer=Layer(row["layer"]),
                ticker=row["ticker"] or None,
                coingecko_id=row["coingecko_id"] or None,
                held=_parse_bool(row["held"]),
                data_quality=DataQuality(row["data_quality"]),
                note=row["note"],
            ))
    return instruments


def price_proxy_map(csv_path: str | Path = DEFAULT_CSV_PATH) -> dict[str, str]:
    """非上場銘柄の価格代理マッピング(proxy_key列が設定された行のみ)を返す。

    例: {"quantinuum": "honeywell"} (Quantinuumは非上場のためHoneywell株価で代理検証する)。
    """
    path = Path(csv_path)
    mapping: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("proxy_key"):
                mapping[row["key"]] = row["proxy_key"]
    return mapping
