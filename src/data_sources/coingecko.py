"""CoinGecko fetcher — 暗号資産の日次価格・出来高・時価総額を取得。

CoinGecko無料API(v3)のmarket_chartは2024年末以降APIキーが必要となった。
  - 現在の対応: yfinanceをprimary source として使用（XRP-USD, QNT-USDは取得成功済み）
  - CoinGeckoはsimple/price(現在値のみ)を試行し、追加メタデータとして補完
  - 将来的にCoinGecko Demo APIキー(無料)を取得した場合は COINGECKO_API_KEY 環境変数で有効化

data_quality:
  - yfinance由来のXRP/QNT価格: verified (yfinance_fetcherで取得済み)
  - CoinGeckoから取れる追加メタデータ: verified (取得できた場合のみ)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

import pandas as pd

from src.config import HISTORY_DAYS, INSTRUMENTS
from src.data_sources.base import BaseFetcher, FetchResult

logger = logging.getLogger(__name__)

CG_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoFetcher(BaseFetcher):
    """CoinGecko / yfinance による暗号資産価格取得。"""

    source_name = "coingecko"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.api_key: str | None = os.environ.get("COINGECKO_API_KEY")

    def fetch(self) -> list[FetchResult]:
        results: list[FetchResult] = []
        targets = [inst for inst in INSTRUMENTS if inst.coingecko_id is not None]

        for i, inst in enumerate(targets):
            if i > 0:
                time.sleep(2.0)

            r = self._fetch_coin(inst.coingecko_id or "", inst.key, inst.ticker)
            self.log_result(r)
            if r.is_ok():
                assert r.df is not None
                self.save_processed(f"cg_{inst.key}", r.df)
            results.append(r)

        return results

    def _fetch_coin(self, coin_id: str, key: str, yf_ticker: str | None) -> FetchResult:
        fetched_at = datetime.now()

        # --- 1次試行: CoinGecko market_chart (APIキーあれば) ---
        if self.api_key:
            r = self._try_cg_market_chart(coin_id, key, fetched_at)
            if r.is_ok():
                return r

        # --- フォールバック: yfinanceでXRP/QNT価格を読み込む ---
        if yf_ticker is not None:
            r = self._fallback_yfinance(yf_ticker, key, fetched_at, coin_id)
            if r.is_ok():
                return r

        # --- 最終: CoinGecko simple/price (現在値のみ・参考用) ---
        return self._try_cg_simple_price(coin_id, key, fetched_at)

    def _try_cg_market_chart(self, coin_id: str, key: str, fetched_at: datetime) -> FetchResult:
        """CoinGecko market_chart (APIキーがある場合のみ)。"""
        result_key = f"cg_{key}"
        headers = {}
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key

        url = f"{CG_BASE}/coins/{coin_id}/market_chart"
        raw = self.retry_get(
            url,
            params={"vs_currency": "usd", "days": "max", "interval": "daily"},
            headers=headers,
            max_attempts=2,
        )
        if raw is None:
            return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                               error="CG market_chart failed")

        try:
            self.save_raw(result_key, raw, fetched_at)
            prices = raw.get("prices", [])
            volumes = raw.get("total_volumes", [])
            market_caps = raw.get("market_caps", [])
            if not prices:
                return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                                   error=f"empty prices for {coin_id}")

            def to_series(data: list[list[float]], col: str) -> pd.Series:
                if not data:
                    return pd.Series(dtype=float, name=col)
                tmp = pd.DataFrame(data, columns=["ts_ms", col])
                tmp["date"] = pd.to_datetime(tmp["ts_ms"], unit="ms", utc=True).dt.normalize()
                return tmp.drop_duplicates("date").set_index("date")[col]

            df = pd.concat([to_series(prices, "price_usd"),
                            to_series(volumes, "volume_usd"),
                            to_series(market_caps, "market_cap_usd")], axis=1)
            df.index.name = "date"
            df = df.sort_index()
            return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                               df=df, missing_rate=self.compute_missing_rate(df),
                               notes=[f"CoinGecko market_chart (APIキー使用) coin_id={coin_id}"])
        except Exception as exc:
            return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                               error=str(exc))

    def _fallback_yfinance(
        self, ticker: str, key: str, fetched_at: datetime, coin_id: str
    ) -> FetchResult:
        """yfinanceから既存の処理済みデータを読む（yfinance_fetcherが先に実行済み前提）。"""
        result_key = f"cg_{key}"
        from datetime import datetime

        import yfinance as yf

        try:
            start = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
            df_raw = yf.download(ticker, start=start, auto_adjust=True, progress=False)
            if df_raw is None or df_raw.empty:
                return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                                   error=f"yfinance empty for {ticker}")

            df_raw.index = pd.to_datetime(df_raw.index)
            df_raw.index.name = "date"
            # カラムを標準化
            col_map: dict[str, str] = {}
            for c in df_raw.columns:
                cs = str(c).lower()
                if "close" in cs:
                    col_map[c] = "price_usd"
                elif "volume" in cs:
                    col_map[c] = "volume_usd"
            df = df_raw.rename(columns=col_map)[list(col_map.values())].copy()
            return FetchResult(
                key=result_key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=[
                    f"yfinanceフォールバック(ticker={ticker}): CoinGecko無料APIはAPIキーが必要。",
                    "COINGECKO_API_KEY 環境変数をセットすると CoinGecko から直接取得できます。",
                    "https://www.coingecko.com/en/api (Demo APIは無料)",
                ],
            )
        except Exception as exc:
            return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                               error=f"yfinance fallback failed: {exc}")

    def _try_cg_simple_price(self, coin_id: str, key: str, fetched_at: datetime) -> FetchResult:
        """CoinGecko simple/price (現在値のみ)。"""
        result_key = f"cg_{key}"
        headers = {}
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key

        raw = self.retry_get(
            f"{CG_BASE}/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
            },
            headers=headers,
            max_attempts=2,
        )
        if raw is None or coin_id not in raw:
            return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                               error=f"simple/price failed for {coin_id}")
        try:
            data = raw[coin_id]
            row = {
                "date": fetched_at,
                "price_usd": data.get("usd"),
                "market_cap_usd": data.get("usd_market_cap"),
                "volume_usd": data.get("usd_24h_vol"),
            }
            df = pd.DataFrame([row]).set_index("date")
            df.index.name = "date"
            return FetchResult(
                key=result_key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=["CoinGecko simple/price: 現在値スナップショットのみ(履歴なし)"],
            )
        except Exception as exc:
            return FetchResult(key=result_key, source=self.source_name, fetched_at=fetched_at,
                               error=str(exc))
