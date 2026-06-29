"""FRED (Federal Reserve Economic Data) fetcher — マクロ指標を取得。

認証: 無料APIキーが必要。https://fred.stlouisfed.org/docs/api/api_key.html で取得。
環境変数 FRED_API_KEY にセットするか、.env ファイルに記述する。

取得系列:
  ISM_MFG_PMI  : ISM製造業PMI (ISMMAN)
  INDPRO        : 鉱工業生産指数 (月次)
  DGORDER       : 耐久財受注 (月次)
  T10Y2Y        : 米国長短スプレッド (日次) - 景気先行サイン
  FEDFUNDS      : FFレート (月次)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

from src.data_sources.base import BaseFetcher, FetchResult

logger = logging.getLogger(__name__)

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

# 取得系列: (series_id, key, 説明)
FRED_SERIES: list[tuple[str, str, str]] = [
    ("ISMMAN", "ism_mfg_pmi", "ISM製造業PMI (月次)"),
    ("INDPRO", "industrial_production", "鉱工業生産指数 (月次)"),
    ("DGORDER", "durable_goods_orders", "耐久財受注 (月次)"),
    ("T10Y2Y", "yield_spread_10y2y", "米国10年-2年スプレッド (日次)"),
    ("FEDFUNDS", "fed_funds_rate", "FFレート (月次)"),
]


class FredFetcher(BaseFetcher):
    """FRED APIからマクロ系列を取得。APIキーがなければスキップ。"""

    source_name = "fred"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.api_key: str | None = os.environ.get("FRED_API_KEY")

    def fetch(self) -> list[FetchResult]:
        if not self.api_key:
            logger.warning(
                "FRED_API_KEY が未設定です。マクロ指標をスキップします。"
                " → https://fred.stlouisfed.org/docs/api/api_key.html で無料取得後、"
                " 環境変数 FRED_API_KEY にセットしてください。"
            )
            return [
                FetchResult(
                    key="fred_all",
                    source=self.source_name,
                    fetched_at=datetime.now(),
                    error="FRED_API_KEY not set",
                    notes=["FRED_API_KEY 未設定のため全系列スキップ。無料キーを取得してください。"],
                )
            ]

        results: list[FetchResult] = []
        for series_id, key, desc in FRED_SERIES:
            r = self._fetch_series(series_id, key, desc)
            self.log_result(r)
            if r.is_ok():
                assert r.df is not None
                self.save_processed(f"fred_{key}", r.df)
            results.append(r)

        return results

    def _fetch_series(self, series_id: str, key: str, desc: str) -> FetchResult:
        fetched_at = datetime.now()
        result_key = f"fred_{key}"

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": "2000-01-01",
        }
        raw = self.retry_get(FRED_API_BASE, params=params)

        if raw is None:
            return FetchResult(
                key=result_key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=f"API failed for {series_id}",
            )

        try:
            self.save_raw(result_key, raw, fetched_at)
            obs = raw.get("observations", [])
            if not obs:
                return FetchResult(
                    key=result_key,
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error=f"no observations for {series_id}",
                )

            df = pd.DataFrame(obs)[["date", "value"]].copy()
            df["date"] = pd.to_datetime(df["date"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.set_index("date").sort_index()
            df.columns = [key]
            df.index.name = "date"

            return FetchResult(
                key=result_key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=[f"{series_id}: {desc}"],
            )
        except Exception as exc:
            return FetchResult(
                key=result_key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )
