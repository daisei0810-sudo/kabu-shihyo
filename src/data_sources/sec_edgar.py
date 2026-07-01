"""SEC EDGAR 全文検索 fetcher(§8 source_rank=A: 法定開示)。

SEC EDGAR Full Text Search API を使用する。
  https://www.sec.gov/edgar/search/ のUIが叩いている公開JSON API。
  エンドポイント: https://efts.sec.gov/LATEST/search-index

注意(重要):
  - SEC は "Fair Access" ポリシーとして、リクエストヘッダに連絡先を含む
    説明的な User-Agent を要求する(例: "CompanyName contact@example.com")。
    未設定/汎用UAだとレート制限やブロックの対象になりうる。
    環境変数 SEC_EDGAR_USER_AGENT で必ず実際の連絡先を設定すること。
  - このAPIは公式ドキュメント化された安定エンドポイントだが、レスポンス形式が
    将来変わる可能性はある。取得失敗時はクラッシュさせず空リストを返す
    (既存 data_sources/ 全体の方針と同じ)。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any

from src.data_sources.base import BaseFetcher

logger = logging.getLogger(__name__)

EDGAR_FULLTEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

_DEFAULT_UA = "kabu-shihyo-tool (contact-email-not-set)"


def _user_agent() -> str:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT")
    if not ua:
        logger.warning(
            "SEC_EDGAR_USER_AGENT 未設定。SECのFair Accessポリシーに反する可能性があるため、"
            "実際の連絡先(例: 'kabu-shihyo-tool your-email@example.com')を設定すること。"
        )
        return _DEFAULT_UA
    return ua


def fetch_edgar_fulltext(
    query: str,
    forms: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """EDGAR全文検索を実行し、生ヒット結果のリストを返す。

    失敗時(ネットワークエラー・レスポンス形式変化等)は空リストを返しクラッシュしない。

    実APIの挙動(実機検証済み、2026-07-02):
      - forms はカンマ結合文字列ではなく、同名パラメータの繰り返し(リスト)で
        送る必要がある(?forms=8-K&forms=10-Q ...)。カンマ結合だと500エラー。
      - dateRange=custom + startdt/enddt を指定する場合、forms も同時に
        指定しないと500エラーになる(未指定の組み合わせはAPI側が想定していない模様)。
        forms未指定で日付範囲だけ欲しい場合は、安全側でforms=["8-K"]を補う。
    """
    params: dict[str, Any] = {"q": query}
    effective_forms = list(forms) if forms else None

    if start_date and end_date:
        if not effective_forms:
            logger.info("dateRange指定時はforms必須のため、既定値['8-K']を補完します。")
            effective_forms = ["8-K"]
        params["dateRange"] = "custom"
        params["startdt"] = start_date.isoformat()
        params["enddt"] = end_date.isoformat()

    if effective_forms:
        params["forms"] = effective_forms

    headers = {"User-Agent": _user_agent()}
    # SEC側で複数forms+日付範囲の組み合わせが間欠的に500を返すことを実機で確認済み。
    # 既存 data_sources 全体のリトライ方針(3回・指数バックオフ)に合わせて再試行する。
    data = BaseFetcher.retry_get(EDGAR_FULLTEXT_SEARCH_URL, params=params, headers=headers)
    if data is None:
        logger.warning("EDGAR fulltext search failed(リトライ後も失敗) query=%r", query)
        return []

    hits = data.get("hits", {}).get("hits", [])
    results: list[dict[str, Any]] = []
    for hit in hits[:max_results]:
        src = hit.get("_source", {})
        results.append({
            "title": src.get("display_names", [query])[0] if src.get("display_names") else query,
            "form_type": src.get("root_form") or src.get("form"),
            "filed_at": src.get("file_date"),
            "cik": src.get("ciks", [None])[0] if src.get("ciks") else None,
            "accession_no": hit.get("_id"),
            "raw": src,
        })
    return results


def fetch_edgar_for_companies(
    company_queries: list[str],
    forms: list[str] | None = None,
    lookback_days: int = 7,
    max_results_per_company: int = 5,
    request_interval_sec: float = 0.3,
) -> list[dict[str, Any]]:
    """複数企業名について直近 lookback_days 日の開示を検索する。

    SEC は短時間の連続リクエストを制限しているため、企業間で間隔を空ける。
    """
    from datetime import timedelta

    end = date.today()
    start = end - timedelta(days=lookback_days)

    all_results: list[dict[str, Any]] = []
    for i, company in enumerate(company_queries):
        if i > 0:
            time.sleep(request_interval_sec)
        hits = fetch_edgar_fulltext(
            company, forms=forms, start_date=start, end_date=end,
            max_results=max_results_per_company,
        )
        for h in hits:
            h["query_company"] = company
        all_results.extend(hits)
    return all_results
