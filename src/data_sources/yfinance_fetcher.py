"""yfinance fetcher — 株価・ETF価格 + 四半期財務(CAPEX)取得。

取得対象:
  - 全Instrumentのticker(Noneを除く)の日次OHLCV
  - NVIDIA / Hyperscaler(MSFT,GOOGL,AMZN,META) / TSM の四半期キャッシュフロー(capex)
  - SOX/SMH/SOXX インデックス
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from src.config import HISTORY_DAYS, INDEX_TICKERS, INSTRUMENTS
from src.data_sources.base import BaseFetcher, FetchResult

logger = logging.getLogger(__name__)

# CAPEXを取得する対象ティッカー
CAPEX_TICKERS: list[str] = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM"]


class YfinanceFetcher(BaseFetcher):
    """yfinanceを使った株価・指数・四半期財務データ取得。"""

    source_name = "yfinance"

    def fetch(self) -> list[FetchResult]:
        results: list[FetchResult] = []
        results.extend(self._fetch_prices())
        results.extend(self._fetch_capex())
        return results

    # ------------------------------------------------------------------
    # 株価(日次OHLCV)
    # ------------------------------------------------------------------

    def _fetch_prices(self) -> list[FetchResult]:
        """全Instrumentの日次価格を一括取得し、銘柄ごとにParquetへ保存。"""
        tickers_map: dict[str, str] = {}  # ticker -> key

        for inst in INSTRUMENTS:
            if inst.ticker is not None:
                tickers_map[inst.ticker] = inst.key

        # インデックス
        for idx_key, ticker in INDEX_TICKERS.items():
            tickers_map[ticker] = f"index_{idx_key}"

        if not tickers_map:
            return []

        start_date = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
        all_tickers = list(tickers_map.keys())
        fetched_at = datetime.now()

        logger.info("yfinance: downloading %d tickers...", len(all_tickers))
        try:
            raw = yf.download(
                all_tickers,
                start=start_date,
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            logger.warning("yfinance batch download failed: %s", exc)
            return [
                FetchResult(
                    key="price_batch",
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error=str(exc),
                )
            ]

        results: list[FetchResult] = []

        for ticker, key in tickers_map.items():
            result = self._extract_ticker(raw, ticker, key, fetched_at, all_tickers)
            self.log_result(result)
            if result.is_ok():
                assert result.df is not None
                self.save_processed(f"price_{key}", result.df)
            results.append(result)

        return results

    def _extract_ticker(
        self,
        raw: pd.DataFrame,
        ticker: str,
        key: str,
        fetched_at: datetime,
        all_tickers: list[str],
    ) -> FetchResult:
        try:
            if len(all_tickers) == 1:
                df = raw.copy()
            else:
                # マルチレベルカラム: (field, ticker)
                df = raw.xs(ticker, axis=1, level=1).copy()

            df.index = pd.to_datetime(df.index)
            df.index.name = "date"
            df = df.dropna(how="all")

            if df.empty:
                return FetchResult(
                    key=f"price_{key}",
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error=f"empty data for {ticker}",
                )

            missing = self.compute_missing_rate(df)
            return FetchResult(
                key=f"price_{key}",
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=missing,
            )
        except Exception as exc:
            return FetchResult(
                key=f"price_{key}",
                source=self.source_name,
                fetched_at=fetched_at,
                error=f"{ticker}: {exc}",
            )

    # ------------------------------------------------------------------
    # 四半期CAPEX
    # ------------------------------------------------------------------

    def _fetch_capex(self) -> list[FetchResult]:
        """四半期キャッシュフロー計算書からCapital Expenditureを取得。"""
        results: list[FetchResult] = []
        fetched_at = datetime.now()

        for ticker in CAPEX_TICKERS:
            result = self._fetch_one_capex(ticker, fetched_at)
            self.log_result(result)
            if result.is_ok():
                assert result.df is not None
                self.save_processed(f"capex_{ticker.lower()}", result.df)
            results.append(result)

        # Hyperscaler合算
        hyperscalers = ["MSFT", "GOOGL", "AMZN", "META"]
        dfs = []
        for t in hyperscalers:
            df = self.load_processed(f"capex_{t.lower()}")
            if df is not None and not df.empty and "capex" in df.columns:
                dfs.append(df["capex"])

        if dfs:
            combined = pd.concat(dfs, axis=1)
            combined.columns = hyperscalers[: len(dfs)]
            combined["hyperscaler_capex_total"] = combined.sum(axis=1)
            combined.index = pd.to_datetime(combined.index)
            missing = self.compute_missing_rate(combined)
            agg_result = FetchResult(
                key="capex_hyperscaler_total",
                source=self.source_name,
                fetched_at=fetched_at,
                df=combined,
                missing_rate=missing,
                notes=["MSFT+GOOGL+AMZN+META のCapEx合算 (四半期・遅延あり)"],
            )
            self.log_result(agg_result)
            if agg_result.is_ok():
                assert agg_result.df is not None
                self.save_processed("capex_hyperscaler_total", agg_result.df)
            results.append(agg_result)

        return results

    def _fetch_one_capex(self, ticker: str, fetched_at: datetime) -> FetchResult:
        key = f"capex_{ticker.lower()}"
        try:
            t = yf.Ticker(ticker)
            cf = t.quarterly_cashflow
            if cf is None or cf.empty:
                return FetchResult(
                    key=key,
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error=f"{ticker}: empty cashflow",
                    notes=["四半期CFが取れない場合はyfinanceの遅延/データ欠如の可能性"],
                )

            # CapEx行を探す（列名は英語・大文字混じりで揺れる）
            capex_row = None
            for row_name in cf.index:
                if "capital" in str(row_name).lower() and "expenditure" in str(row_name).lower():
                    capex_row = row_name
                    break
                if str(row_name).lower() in ("capitalexpenditures", "capex"):
                    capex_row = row_name
                    break

            if capex_row is None:
                return FetchResult(
                    key=key,
                    source=self.source_name,
                    fetched_at=fetched_at,
                    error=f"{ticker}: capex row not found. rows={list(cf.index)[:5]}",
                )

            series = cf.loc[capex_row].dropna()
            df = series.to_frame(name="capex")
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            # CAPEXは通常負値（支出）。絶対値に変換
            df["capex"] = df["capex"].abs()
            df.index.name = "date"

            # 生データ保存 (Timestamp keyを文字列変換)
            raw_dict = {
                str(k): {str(kk): vv for kk, vv in v.items()}
                for k, v in cf.to_dict().items()
            }
            self.save_raw(key, raw_dict, fetched_at)

            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=[f"{ticker} 四半期CapEx (USD, 遅延あり)"],
            )
        except Exception as exc:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )
