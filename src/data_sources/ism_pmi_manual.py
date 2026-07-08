"""ISM製造業PMI 手動入力ローダー。

無料での自動取得を広く調査したが実現できなかった(docs/investment_os_design.md参照):
FRED経由の系列(ISMMAN/NAPM)はISM社のライセンス変更により2016年頃に配信終了、
代替候補のDBnomics(ISM/pmi/pm)も2025-09分以降のデータが破損(実体経済的にあり得ない
一桁台の値)しており、同じくISM社側の提供停止が疑われる。2025-08分までは
DBnomicsの実データで検証済みのため`config/ism_pmi_manual.csv`に事前投入し、
それ以降は毎月ユーザーがISM公式の無料プレスリリース(https://www.ismworld.org/)
から手入力する運用に切り替える。

ISMは毎月第1営業日に前月分を公表する。本ローダーは最新エントリが公表サイクルから
乖離している場合に鮮度警告を出す(忘れ防止。daily_report.pyの
_section_manual_data_freshness()で公開レポートにも表示)。
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.data_sources.base import BaseFetcher, FetchResult

logger = logging.getLogger(__name__)

MANUAL_CSV_PATH = Path("config/ism_pmi_manual.csv")


def expected_latest_month(today: date) -> date:
    """ISMの公表サイクル(毎月第1営業日に前月分)から、本日時点で入手済みで
    あるべき最新の対象月(月初に正規化)を返す。日付の余裕を見て5日を閾値にする
    (祝日・週末で第1営業日が数日ずれる場合を吸収)。
    """
    first_of_this_month = today.replace(day=1)
    if today.day > 5:
        # 今月の第1営業日は十分過ぎている → 前月分は公表済みのはず
        return (first_of_this_month - timedelta(days=1)).replace(day=1)
    # 今月分の公表直後〜前かもしれないので、前々月分までを期待値にする
    prev_month_end = first_of_this_month - timedelta(days=1)
    return (prev_month_end.replace(day=1) - timedelta(days=1)).replace(day=1)


def staleness_note(latest_month: date, today: date | None = None) -> tuple[str, bool]:
    """(表示文字列, 更新遅延しているか)を返す。"""
    today = today or date.today()
    expected = expected_latest_month(today)
    months_behind = (expected.year - latest_month.year) * 12 + (expected.month - latest_month.month)
    if months_behind > 0:
        return (
            f"⚠️ 更新遅延({months_behind}ヶ月分、最終={latest_month:%Y-%m}、"
            f"期待={expected:%Y-%m}) → https://www.ismworld.org/ の最新値を"
            "config/ism_pmi_manual.csv へ追記してください",
            True,
        )
    return f"最終更新: {latest_month:%Y-%m}(最新)", False


def _read_manual_csv(path: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            month_raw, value_raw = row.get("month"), row.get("value")
            if not month_raw or not value_raw:
                continue
            try:
                month_date = pd.Period(month_raw, freq="M").to_timestamp()
                rows.append({"date": month_date, "ism_mfg_pmi": float(value_raw)})
            except (ValueError, TypeError) as exc:
                logger.warning("ism_pmi_manual.csv 行の解析失敗: %r (%s)", row, exc)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("date").sort_index()


class IsmPmiManualLoader(BaseFetcher):
    """config/ism_pmi_manual.csv → data/processed/ism_pmi_manual.parquet。

    ネットワークアクセスは行わない(手動CSVの変換のみ)が、他のfetcherと
    同じインターフェースに揃えることでStep1のfetchersリストへそのまま追加できる。
    """

    source_name = "ism_pmi_manual"

    def fetch(self, csv_path: Path = MANUAL_CSV_PATH) -> list[FetchResult]:
        fetched_at = datetime.now()
        result_key = "ism_pmi_manual"

        if not csv_path.exists():
            return [FetchResult(
                key=result_key, source=self.source_name, fetched_at=fetched_at,
                error=f"{csv_path} が存在しません",
            )]

        df = _read_manual_csv(csv_path)
        if df.empty:
            return [FetchResult(
                key=result_key, source=self.source_name, fetched_at=fetched_at,
                error=f"{csv_path} にデータ行がありません",
            )]

        note, is_stale = staleness_note(df.index.max().date())
        if is_stale:
            logger.warning(note)

        result = FetchResult(
            key=result_key, source=self.source_name, fetched_at=fetched_at,
            df=df, missing_rate=self.compute_missing_rate(df), notes=[note],
        )
        self.save_processed(result_key, df)
        return [result]
