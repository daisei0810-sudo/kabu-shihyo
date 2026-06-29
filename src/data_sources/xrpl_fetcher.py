"""XRPL on-chain fetcher — トランザクション統計・AMM・RLUSD供給量を取得。

データソース:
  1. XRPScan 無料API  : 直近ネットワーク統計
  2. XRPLCluster JSON-RPC (https://xrplcluster.com/) : AMM情報・gateway_balances
  3. xrpl.org Data API v2  : 日次トランザクション統計（利用可能な場合）

⚠️  RLUSD 発行体アドレス:
  RLUSD_ISSUER_ADDRESS は Ripple 公式の発表で確認が必要です。
  現在は設定ファイル(config.py)に定数として持ち、実行前に確認してください。
  Opusへの設計依頼事項: XRPScanで "RLUSD" を検索して発行体アドレスを特定する。

⚠️  AMM プール識別:
  amm_info には資産ペアの指定が必要です(XRP + トークン)。
  主要XRP AMMプールのIDはOpcusが確定する必要あり。
  現在は主要ステーブルコインとのペアを試みます。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from src.data_sources.base import BaseFetcher, FetchResult

logger = logging.getLogger(__name__)

XRPL_RPC = "https://xrplcluster.com/"
XRPSCAN_API = "https://api.xrpscan.com/api/v1"
XRPL_DATA_API = "https://data.xrpl.org/v2"  # 利用可能な場合

# RLUSD 発行体アドレス — 2026-06-29 オンチェーンで確定済み。
#   gateway_balances で RLUSD発行残高 ≈8.1億、account_info の Domain=ripple.com を確認。
RLUSD_ISSUER_ADDRESS = "rMxCKbEDwqr76QuheSUMdEGf4B9xJ8m5De"


def to_hex_currency(code: str) -> str:
    """XRPL通貨コードを標準形式に変換。

    3文字以下はそのまま(ISO風)。4文字以上は160bit(40桁hex)へ符号化する必要がある。
    例: "RLUSD" → "524C555344000000000000000000000000000000"
    これを怠ると amm_info は "Issue is malformed"、gateway_balances は誤キーで0を返す。
    """
    if len(code) <= 3:
        return code
    return code.encode("ascii").ljust(20, b"\x00").hex().upper()


RLUSD_HEX = to_hex_currency("RLUSD")

# 主要XRP AMMペア。4文字以上の通貨コードは必ず hex 符号化する。
XRP_AMM_PAIRS: list[dict[str, object]] = [
    {
        "name": "XRP/RLUSD",
        "asset": {"currency": "XRP"},
        "asset2": {"currency": RLUSD_HEX, "issuer": RLUSD_ISSUER_ADDRESS},
        "token_label": "RLUSD",
    },
]


class XrplFetcher(BaseFetcher):
    """XRPLオンチェーンデータの取得。"""

    source_name = "xrpl"

    def fetch(self) -> list[FetchResult]:
        results: list[FetchResult] = []

        # 1. ネットワーク統計 (XRPScan)
        r_stats = self._fetch_network_stats()
        self.log_result(r_stats)
        if r_stats.is_ok():
            assert r_stats.df is not None
            self.save_processed("xrpl_network_stats", r_stats.df)
        results.append(r_stats)

        # 2. 日次トランザクション統計
        r_tx = self._fetch_daily_tx_stats()
        self.log_result(r_tx)
        if r_tx.is_ok():
            assert r_tx.df is not None
            self.save_processed("xrpl_daily_tx", r_tx.df)
        results.append(r_tx)

        # 3. AMM情報 (amm_info)
        for pair_def in XRP_AMM_PAIRS:
            time.sleep(0.5)
            r_amm = self._fetch_amm_info(pair_def)
            self.log_result(r_amm)
            if r_amm.is_ok():
                assert r_amm.df is not None
                name = str(pair_def["name"]).replace("/", "_")
                self.save_processed(f"xrpl_amm_{name}", r_amm.df)
            results.append(r_amm)

        # 4. RLUSD 発行残高 (gateway_balances)
        r_rlusd = self._fetch_rlusd_supply()
        self.log_result(r_rlusd)
        if r_rlusd.is_ok():
            assert r_rlusd.df is not None
            self.save_processed("xrpl_rlusd_supply", r_rlusd.df)
        results.append(r_rlusd)

        return results

    # ------------------------------------------------------------------
    # ネットワーク統計 (XRPScan)
    # ------------------------------------------------------------------

    def _fetch_network_stats(self) -> FetchResult:
        fetched_at = datetime.now()
        key = "xrpl_network_stats"

        raw = self.retry_get(f"{XRPSCAN_API}/ledger")

        if raw is None:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error="XRPScan metrics API failed",
                notes=["XRPScan 無料APIのレート制限か一時障害の可能性"],
            )

        try:
            self.save_raw(key, raw, fetched_at)
            # /ledger エンドポイント: current_ledger と ledgers リストを返す
            ledgers = raw.get("ledgers")
            ledger_data = ledgers[0] if isinstance(ledgers, list) and ledgers else {}
            row: dict[str, object] = {
                "fetched_at": fetched_at,
                "ledger_index": raw.get("current_ledger") or ledger_data.get("ledger_index"),
                "ledger_hash": ledger_data.get("ledger_hash"),
                "txn_count": ledger_data.get("txn_count"),
                "close_time": ledger_data.get("close_time"),
            }
            df = pd.DataFrame([row]).set_index("fetched_at")
            df.index.name = "date"
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=self.compute_missing_rate(df),
                notes=["XRPScan current metrics snapshot (1行)"],
            )
        except Exception as exc:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # 日次トランザクション統計 (xrpl.org Data API v2)
    # ------------------------------------------------------------------

    def _fetch_daily_tx_stats(self) -> FetchResult:
        """日次Txカウント・Payment件数の歴史データ。

        Data API v2が利用可能なら使用。失敗時はXRPScanの現時点値のみ。
        """
        fetched_at = datetime.now()
        key = "xrpl_daily_tx"
        end_date = fetched_at.date()
        start_date = end_date - timedelta(days=365 * 2)

        # Data API v2 試行
        url = f"{XRPL_DATA_API}/network/payment_activity"
        params = {
            "date_start": start_date.isoformat(),
            "date_end": end_date.isoformat(),
            "limit": 1000,
        }
        raw = self.retry_get(url, params=params, max_attempts=2)

        if raw and isinstance(raw, dict) and raw.get("rows"):
            try:
                self.save_raw(key, raw, fetched_at)
                rows = raw["rows"]
                df = pd.DataFrame(rows)
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.set_index("date").sort_index()
                return FetchResult(
                    key=key,
                    source=self.source_name,
                    fetched_at=fetched_at,
                    df=df,
                    missing_rate=self.compute_missing_rate(df),
                    notes=[f"xrpl.org Data API v2 (日次Tx統計) rows={len(df)}"],
                )
            except Exception as exc:
                logger.warning("Data API parse failed: %s", exc)

        # フォールバック: XRPScan 月次サマリー
        url2 = f"{XRPSCAN_API}/transactions/summary"
        raw2 = self.retry_get(url2, max_attempts=2)
        if raw2:
            try:
                self.save_raw(f"{key}_xrpscan", raw2, fetched_at)
                if isinstance(raw2, list):
                    df = pd.DataFrame(raw2)
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"])
                        df = df.set_index("date").sort_index()
                    return FetchResult(
                        key=key,
                        source=self.source_name,
                        fetched_at=fetched_at,
                        df=df,
                        missing_rate=self.compute_missing_rate(df),
                        notes=["XRPScan transactions summary (フォールバック)"],
                    )
            except Exception as exc:
                logger.warning("XRPScan parse failed: %s", exc)

        return FetchResult(
            key=key,
            source=self.source_name,
            fetched_at=fetched_at,
            error="both Data API v2 and XRPScan failed for daily tx stats",
            notes=[
                "日次Tx統計の歴史データ取得に失敗。",
                "data.xrpl.org/v2 は利用不可の可能性あり。",
                "代替: XRPScan Pro API (有料) または BigQuery public datasets を検討。",
            ],
        )

    # ------------------------------------------------------------------
    # AMM情報 (JSON-RPC amm_info)
    # ------------------------------------------------------------------

    def _fetch_amm_info(self, pair_def: dict[str, object]) -> FetchResult:
        """指定ペアのAMM情報を取得(現時点スナップショット)。"""
        fetched_at = datetime.now()
        pair_name = str(pair_def["name"]).replace("/", "_")
        key = f"xrpl_amm_{pair_name}"

        payload: Any = {
            "method": "amm_info",
            "params": [
                {
                    "asset": pair_def["asset"],
                    "asset2": pair_def["asset2"],
                    "ledger_index": "validated",
                }
            ],
        }

        try:
            resp = requests.post(XRPL_RPC, json=payload, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=f"amm_info RPC failed: {exc}",
                notes=[
                    f"ペア: {pair_def['name']}",
                    "RLUSD_ISSUER_ADDRESS が正しいか確認してください(Opus設計事項)",
                ],
            )

        result = raw.get("result", {})
        if result.get("error"):
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=f"amm_info error: {result.get('error_message', result.get('error'))}",
                notes=[
                    "AMMプールが存在しないか、アドレスが誤っている可能性。",
                    "RLUSD_ISSUER_ADDRESS を xrpl_fetcher.py で要確認(Opus設計事項)。",
                ],
            )

        try:
            self.save_raw(key, raw, fetched_at)
            amm = result.get("amm", {})
            amount = amm.get("amount", {})
            amount2 = amm.get("amount2", {})
            lp_token = amm.get("lp_token", {})

            # XRP残高 (dropsから XRP へ変換)
            xrp_drops: float = 0.0
            token_value: float = 0.0

            if isinstance(amount, str):
                xrp_drops = float(amount)
            elif isinstance(amount, dict):
                token_value = float(amount.get("value", 0))

            if isinstance(amount2, str):
                xrp_drops = float(amount2)
            elif isinstance(amount2, dict):
                token_value = float(amount2.get("value", 0))

            row = {
                "fetched_at": fetched_at,
                "xrp_balance": xrp_drops / 1_000_000,  # drops -> XRP
                "token_balance": token_value,
                "token_currency": (
                    amount2.get("currency") if isinstance(amount2, dict)
                    else amount.get("currency") if isinstance(amount, dict)
                    else "unknown"
                ),
                "lp_token_value": (
                    float(lp_token.get("value", 0)) if isinstance(lp_token, dict) else 0.0
                ),
                "trading_fee": amm.get("trading_fee", 0),
            }
            df = pd.DataFrame([row]).set_index("fetched_at")
            df.index.name = "date"

            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=0.0,
                notes=[f"AMM snapshot: {pair_def['name']}, XRP={row['xrp_balance']:.2f}"],
            )
        except Exception as exc:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # RLUSD 発行残高 (gateway_balances)
    # ------------------------------------------------------------------

    def _fetch_rlusd_supply(self) -> FetchResult:
        """RLUSD発行体アドレスの gateway_balances から発行残高を取得。

        obligations は通貨コードがキー。RLUSDは4文字以上なので hex(RLUSD_HEX)キーで参照する。
        """
        fetched_at = datetime.now()
        key = "xrpl_rlusd_supply"

        payload: Any = {
            "method": "gateway_balances",
            "params": [
                {
                    "account": RLUSD_ISSUER_ADDRESS,
                    "ledger_index": "validated",
                    "strict": True,
                }
            ],
        }

        try:
            resp = requests.post(XRPL_RPC, json=payload, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=f"gateway_balances RPC failed: {exc}",
                notes=["RLUSD供給量の取得失敗。RLUSD_ISSUER_ADDRESS要確認(Opus設計事項)"],
            )

        result = raw.get("result", {})
        if result.get("error"):
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=f"gateway_balances error: {result.get('error_message', result.get('error'))}",
                notes=[
                    f"RLUSD_ISSUER_ADDRESS={RLUSD_ISSUER_ADDRESS} は暫定値。",
                    "正しいアドレスに更新すれば取得できます(Opus設計事項)。",
                ],
            )

        try:
            self.save_raw(key, raw, fetched_at)
            obligations = result.get("obligations", {})
            # RLUSDは hexキー。後方互換で ASCII "RLUSD" もフォールバック参照
            rlusd_supply = float(obligations.get(RLUSD_HEX, obligations.get("RLUSD", 0)))

            row = {
                "fetched_at": fetched_at,
                "rlusd_supply": rlusd_supply,
                "issuer": RLUSD_ISSUER_ADDRESS,
                "all_obligations": str(obligations),
            }
            df = pd.DataFrame([row]).set_index("fetched_at")
            df.index.name = "date"

            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                df=df,
                missing_rate=0.0,
                notes=[f"RLUSD supply={rlusd_supply:,.0f} (snapshot)"],
            )
        except Exception as exc:
            return FetchResult(
                key=key,
                source=self.source_name,
                fetched_at=fetched_at,
                error=str(exc),
            )
