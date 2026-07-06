"""テーマ間の相関行列を保有銘柄バスケットの90日リターンから算出する。"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, INSTRUMENTS

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)
DEFAULT_LOOKBACK_DAYS = 90


def _load_close(key: str, processed_dir: Path) -> pd.Series | None:
    path = processed_dir / f"price_{key}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        if "Close" not in df.columns:
            return None
        s = df["Close"].dropna()
        return s if len(s) > 0 else None
    except Exception as exc:
        logger.warning("price load failed key=%s: %s", key, exc)
        return None


def theme_price_basket(theme: str, processed_dir: Path = PROCESSED_DIR) -> pd.Series | None:
    """テーマの保有銘柄を等加重平均した正規化バスケット系列(初日=1)。"""
    instruments = [
        i for i in INSTRUMENTS if i.layer.value == theme and i.held and i.ticker
    ]
    series_list = []
    for inst in instruments:
        s = _load_close(inst.key, processed_dir)
        if s is not None and len(s) > 0 and float(s.iloc[0]) != 0:
            series_list.append(s / float(s.iloc[0]))
    if not series_list:
        return None
    basket = pd.concat(series_list, axis=1).mean(axis=1).dropna()
    return basket if not basket.empty else None


def compute_theme_correlation_matrix(
    themes: list[str],
    processed_dir: Path = PROCESSED_DIR,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """保有銘柄バスケットの直近lookback_days日リターンからテーマ間相関行列を計算する。

    バスケットが無い(保有銘柄なし)テーマ、データ不足のテーマは行列に含めない
    (捏造しない。相関ペナルティは計算可能なテーマ間のみ適用される)。
    """
    returns: dict[str, pd.Series] = {}
    for theme in themes:
        basket = theme_price_basket(theme, processed_dir)
        if basket is None:
            continue
        r = basket.pct_change().dropna()
        if len(r) < lookback_days:
            continue
        returns[theme] = r.tail(lookback_days).reset_index(drop=True)

    if len(returns) < 2:
        return pd.DataFrame()

    df = pd.DataFrame(returns).dropna()
    if df.empty or len(df) < 2:
        return pd.DataFrame()
    return df.corr()
