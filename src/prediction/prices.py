"""価格系列の読み込み(予測台帳のbaseline/事後評価で共通利用)。

notifications/backtest_eval.pyの同名ロジックとの違いは、非上場銘柄を
PRICE_PROXY経由で代理銘柄の株価にフォールバックする点(§8確定事項:
Quantinuumは HON proxy + 近似フラグで評価し、IPO観測時にproxy→verifiedへ
昇格する運用とする)。代理を使った場合は呼び出し側が承知できるよう bool で返す。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, PRICE_PROXY

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)


def load_close_series(key: str, processed_dir: Path = PROCESSED_DIR) -> pd.Series | None:
    """price_{key}.parquet の終値系列を読み込む。無ければNone。"""
    path = processed_dir / f"price_{key}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        if "Close" not in df.columns:
            return None
        return df["Close"].dropna().sort_index()
    except Exception as exc:
        logger.warning("price load failed key=%s: %s", key, exc)
        return None


def resolve_price_series(
    target: str, processed_dir: Path = PROCESSED_DIR
) -> tuple[pd.Series | None, bool]:
    """target自身の価格系列を返す。無ければPRICE_PROXY経由で代理銘柄を試す。

    戻り値の bool は「代理銘柄の価格を使ったか(近似評価)」。
    """
    series = load_close_series(target, processed_dir)
    if series is not None and not series.empty:
        return series, False
    proxy_key = PRICE_PROXY.get(target)
    if proxy_key:
        proxy_series = load_close_series(proxy_key, processed_dir)
        if proxy_series is not None and not proxy_series.empty:
            return proxy_series, True
    return None, False


def price_at_or_before(series: pd.Series, target_date: pd.Timestamp) -> float | None:
    """target_date以前で最も近い終値を返す(ルックアヘッド回避)。"""
    window = series[series.index <= target_date]
    if window.empty:
        return None
    return float(window.iloc[-1])
