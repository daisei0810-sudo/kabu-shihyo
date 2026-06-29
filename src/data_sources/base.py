"""ベースfetcher — ロギング・リトライ・保存・欠損率計算の共通処理。"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import DATA_PROCESSED, DATA_RAW

logger = logging.getLogger(__name__)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@dataclass
class FetchResult:
    """1件のfetch結果 + メタデータ。"""

    key: str
    source: str
    fetched_at: datetime
    df: pd.DataFrame | None = None
    missing_rate: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def is_ok(self) -> bool:
        return self.df is not None and not self.df.empty


class BaseFetcher(ABC):
    """全fetcherの抽象基底クラス。"""

    source_name: str = ""

    def __init__(
        self,
        raw_dir: str = DATA_RAW,
        processed_dir: str = DATA_PROCESSED,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def fetch(self) -> list[FetchResult]:
        """データを取得して返す。例外は内部で捕捉しerrorフィールドへ入れる。"""
        ...

    # ------------------------------------------------------------------
    # 保存 / 読み込み
    # ------------------------------------------------------------------

    def save_raw(self, key: str, data: Any, fetched_at: datetime) -> Path:
        """生レスポンスをJSON形式でスナップショット保存（タイムスタンプ付き）。"""
        ts = fetched_at.strftime("%Y%m%d_%H%M%S")
        path = self.raw_dir / f"{key}_{ts}.json"
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str, indent=2)
        except Exception as exc:
            logger.warning("raw save failed key=%s err=%s", key, exc)
        return path

    def save_processed(self, key: str, df: pd.DataFrame) -> Path:
        """処理済みDataFrameをParquetで保存（最新版で上書き）。"""
        path = self.processed_dir / f"{key}.parquet"
        try:
            df.to_parquet(path, index=True)
        except Exception as exc:
            logger.warning("processed save failed key=%s err=%s", key, exc)
        return path

    def load_processed(self, key: str) -> pd.DataFrame | None:
        """処理済みDataFrameを読み込む。存在しなければ None。"""
        path = self.processed_dir / f"{key}.parquet"
        if not path.exists():
            return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning("load_processed failed key=%s err=%s", key, exc)
            return None

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def compute_missing_rate(df: pd.DataFrame) -> float:
        """全セルに対するNaN率(0.0-1.0)。"""
        if df.empty:
            return 1.0
        return float(df.isna().sum().sum()) / df.size

    @staticmethod
    def log_result(result: FetchResult) -> None:
        if result.is_ok():
            assert result.df is not None
            logger.info(
                "✅ %s | key=%-30s rows=%4d  missing=%.1f%%",
                result.source,
                result.key,
                len(result.df),
                result.missing_rate * 100,
            )
        else:
            logger.warning(
                "⚠️  %s | key=%-30s FAILED  error=%s",
                result.source,
                result.key,
                result.error,
            )
        for note in result.notes:
            logger.info("    ℹ️  %s", note)

    @staticmethod
    def retry_get(
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_attempts: int = 3,
        backoff: float = 2.0,
    ) -> Any:
        """GETリクエストをリトライ付きで実行。失敗時は None を返す（例外を送出しない）。"""
        import requests  # ローカルimportで循環を避ける

        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                if attempt < max_attempts:
                    wait = backoff**attempt
                    logger.debug(
                        "retry %d/%d url=%s wait=%.1fs err=%s",
                        attempt, max_attempts, url, wait, exc,
                    )
                    time.sleep(wait)
                else:
                    logger.warning("GET failed url=%s err=%s", url, exc)
                    return None
        return None
