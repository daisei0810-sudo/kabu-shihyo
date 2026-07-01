"""EDINET(金融庁 電子開示システム) fetcher(§8 source_rank=A: 法定開示)。

保有銘柄の大半(フジクラ・ローツェ・キオクシア・村田製作所・ハーモニックドライブ・
ファナック・安川電機等)は日本上場企業であり、SEC EDGAR(米国上場企業のみ対象)では
一切カバーできない。EDINETはその欠落を埋める日本版の法定開示システムであり、
金融庁が無料で公開しているAPIを使う。

SEC EDGARとの方式の違い(重要):
  EDGARは自由記述の全文検索APIだが、EDINETは「指定日に提出された開示の一覧」を
  返す日付ベースのAPIである。企業名による絞り込みはクライアント側で行う
  (material_id.py の COMPANY_ALIASES と同じ発想で、提出者名(filerName)に
  保有銘柄の会社名が含まれるかを文字列一致で判定する)。

認証: 無料の "Subscription-Key" が必要。https://api.edinet-fsa.go.jp で登録する。
環境変数 EDINET_API_KEY にセットする(未設定時はFRED同様スキップし、クラッシュしない)。

動作確認済み(2026-07-02): 実際のAPIキーでフジクラ・村田製作所・ファナックの
臨時報告書等を取得できることを確認済み。失敗時は既存 data_sources/ 全体の
方針通りクラッシュせず空リストを返す。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

from src.data_sources.base import BaseFetcher

logger = logging.getLogger(__name__)

EDINET_DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"


def _api_key() -> str | None:
    key = os.environ.get("EDINET_API_KEY")
    if not key:
        logger.info(
            "EDINET_API_KEY 未設定。https://api.edinet-fsa.go.jp で無料登録の上、"
            "環境変数に設定すること。EDINET取得をスキップします。"
        )
    return key


def fetch_edinet_documents_for_date(target_date: date) -> list[dict[str, Any]]:
    """指定日にEDINETへ提出された開示一覧を取得する。

    typeパラメータは "2"(メタデータ+提出書類一覧)を指定する。
    失敗時・キー未設定時は空リストを返す(クラッシュしない)。
    """
    key = _api_key()
    if not key:
        return []

    params = {
        "date": target_date.isoformat(),
        "type": "2",
        "Subscription-Key": key,
    }
    data = BaseFetcher.retry_get(EDINET_DOCUMENTS_URL, params=params)
    if data is None:
        logger.warning("EDINET document list fetch failed date=%s", target_date)
        return []
    results = data.get("results")
    return results if isinstance(results, list) else []


def fetch_edinet_for_companies(
    company_aliases: list[str],
    lookback_days: int = 7,
    request_interval_sec: float = 0.3,
) -> list[dict[str, Any]]:
    """直近lookback_days日分の開示を取得し、提出者名(filerName)を会社名でフィルタする。

    EDINETは日付ごとにその日全ての提出書類を返すため、日数分APIを叩き
    クライアント側でマッチングする(SEC EDGARの全文検索とは方式が異なる)。
    """
    key = _api_key()
    if not key or not company_aliases:
        return []

    end = date.today()
    matched: list[dict[str, Any]] = []
    lowered_aliases = [(a, a.lower()) for a in company_aliases]

    for i in range(lookback_days):
        if i > 0:
            time.sleep(request_interval_sec)
        target_date = end - timedelta(days=i)
        docs = fetch_edinet_documents_for_date(target_date)
        for doc in docs:
            filer_name = str(doc.get("filerName") or "").lower()
            if not filer_name:
                continue
            for original, lowered in lowered_aliases:
                if lowered in filer_name:
                    doc = dict(doc)
                    doc["matched_alias"] = original
                    matched.append(doc)
                    break
    return matched


def parse_submit_datetime(raw: str | None) -> datetime | None:
    """EDINETの submitDateTime("YYYY-MM-DD HH:MM" 形式を想定)をパースする。"""
    if not raw:
        return None
    from datetime import UTC

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    logger.debug("EDINET submitDateTime 解析失敗: %r", raw)
    return None
