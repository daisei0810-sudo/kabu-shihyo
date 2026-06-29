"""DefiLlama 無料API fetcher — TVL・ステーブルコインTVLを取得。

無料・認証不要。
主要エンドポイント:
  - /v2/historicalChainTvl/{chain}    チェーン別TVL (日次)
  - /stablecoincharts/{chain}         チェーン別 stablecoin TVL (日次)
  - /stablecoincharts/all             全チェーン合算 stablecoin TVL
chain名は "XRP_Ledger"
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from src.data_sources.base import BaseFetcher, FetchResult

logger = logging.getLogger(__name__)

DEFILLAMA_BASE = "https://api.llama.fi"
STABLECOINS_BASE = "https://stablecoins.llama.fi"

XRPL_CHAIN = "XRPL"  # DefiLlama の正式chain名 (XRP_Ledger は404)


class DefiLlamaFetcher(BaseFetcher):
    """DefiLlamaからXRPL TVLとステーブルコインTVLを取得。"""

    source_name = "defillama"

    def fetch(self) -> list[FetchResult]:
        results: list[FetchResult] = []

        r1 = self._fetch_chain_tvl()
        self.log_result(r1)
        if r1.is_ok():
            assert r1.df is not None
            self.save_processed("defillama_xrpl_tvl", r1.df)
        results.append(r1)

        r2 = self._fetch_stablecoin_tvl()
        self.log_result(r2)
        if r2.is_ok():
            assert r2.df is not None
            self.save_processed("defillama_stablecoin_tvl", r2.df)
        results.append(r2)

        return results

    def _fetch_chain_tvl(self) -> FetchResult:
        """XRP LedgerのDeFi TVL(日次)。"""
        fetched_at = datetime.now()
        url = f"{DEFILLAMA_BASE}/v2/historicalChainTvl/{XRPL_CHAIN}"
        raw = self.retry_get(url)

        if raw is None:
            return FetchResult(
                key="defillama_xrpl_tvl",
                source=self.source_name,
                fetched_at=fetched_at,
                error="chain TVL API failed",
                notes=["XRP_LedgerのTVLが取れない場合は chain名が変わった可能性あり"],
            )

        try:
            self.save_raw("defillama_xrpl_tvl", raw, fetched_at)
            df = pd.DataFrame(raw)
            if df.empty or "date" not in df.columns:
                return FetchResult(
                    key="defillama_xrpl_tvl",
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error=f"unexpected shape: {df.columns.tolist()}",
                )
            df["date"] = pd.to_datetime(df["date"].astype(int), unit="s", utc=True).dt.normalize()
            df = df.set_index("date").sort_index()
            df.columns = ["tvl_usd"]
            return FetchResult(
                key="defillama_xrpl_tvl",
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=[f"XRPL DeFi TVL (日次) rows={len(df)}"],
            )
        except Exception as exc:
            return FetchResult(
                key="defillama_xrpl_tvl",
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )

    def _fetch_stablecoin_tvl(self) -> FetchResult:
        """XRP Ledger上のステーブルコイン総TVL(日次)。"""
        fetched_at = datetime.now()
        url = f"{STABLECOINS_BASE}/stablecoincharts/{XRPL_CHAIN}"
        raw = self.retry_get(url)

        if raw is None:
            # フォールバック: 全チェーン合算から代替はできないので skip
            return FetchResult(
                key="defillama_stablecoin_tvl",
                source=self.source_name,
                fetched_at=fetched_at,
                error="stablecoin TVL API failed",
                notes=["XRPL上のステーブルコインTVLが取れません。RLUSD/USDCはまだ規模が小さくAPIに反映されていない可能性あり"],
            )

        try:
            self.save_raw("defillama_stablecoin_tvl", raw, fetched_at)
            # レスポンス形式: [{date:"unix_str", totalCirculatingUSD:{peggedUSD:...}, ...}, ...]
            rows = []
            for item in raw:
                date_val = pd.to_datetime(int(item["date"]), unit="s", utc=True).normalize()
                circ_usd = item.get("totalCirculatingUSD", {})
                total_usd = sum(v for v in circ_usd.values() if isinstance(v, (int, float)))
                rows.append({
                    "date": date_val,
                    "stablecoin_tvl_usd": total_usd,
                    "pegged_usd": circ_usd.get("peggedUSD", 0.0),
                })

            if not rows:
                return FetchResult(
                    key="defillama_stablecoin_tvl",
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error="empty stablecoin data after parse",
                )

            df = pd.DataFrame(rows).set_index("date").sort_index()
            return FetchResult(
                key="defillama_stablecoin_tvl",
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=[f"XRPL Stablecoin TVL rows={len(df)}"],
            )
        except Exception as exc:
            return FetchResult(
                key="defillama_stablecoin_tvl",
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )
