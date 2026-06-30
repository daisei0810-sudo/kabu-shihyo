"""テクニカル指標 + マクロ指標スコア。

RSI(14) と移動平均乖離率(25/75/200日)で押し目/過熱度を判定。
VIX・USD/JPY・米10年金利で市場環境を把握。

設計:
  - price_*.parquet の Close 列を読み込む
  - データ不足(< 30行)は tech_outlook="データ不足" を返す
  - ^TNX は yfinance が % を直接返す (e.g. 4.25 = 4.25%)
  - JPY=X は 1USD=?JPY で返る (JPY/USD の逆数ではない)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import DATA_PROCESSED, held_instruments

logger = logging.getLogger(__name__)
PROC_DIR = Path(DATA_PROCESSED)


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class TechnicalResult:
    """1銘柄のテクニカル指標。"""
    target: str
    name_ja: str
    rsi: float | None = None
    ma25_dev: float | None = None   # (close - MA25) / MA25 × 100 (%)
    ma75_dev: float | None = None
    ma200_dev: float | None = None
    close: float | None = None
    tech_outlook: str = "不明"
    tech_note: str = ""


@dataclass
class MacroResult:
    """マクロ環境指標（毎日1スナップショット）。"""
    vix: float | None = None
    vix_label: str = "不明"
    usdjpy: float | None = None
    usdjpy_trend: str = ""   # 例: "円安 (+3.2%)"
    us10y: float | None = None
    us10y_trend: str = ""    # 例: "上昇 (+0.18%pt)"


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _load_close(key: str) -> pd.Series | None:
    path = PROC_DIR / f"price_{key}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        if "Close" in df.columns:
            return df["Close"].dropna()
        return None
    except Exception as exc:
        logger.warning("load failed %s: %s", key, exc)
        return None


def _compute_rsi(close: pd.Series, period: int = 14) -> float | None:
    """Wilder RSI(14)。独立サンプルが不足する場合は None。"""
    clean = close.dropna()
    if len(clean) < period + 5:
        return None
    delta = clean.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_s = 100 - (100 / (1 + rs))
    last = rsi_s.iloc[-1]
    return None if pd.isna(last) else float(last)


def _ma_dev(close: pd.Series, window: int) -> float | None:
    """(終値 - MA_n) / MA_n × 100 (%)。データ不足時は None。"""
    clean = close.dropna()
    if len(clean) < window:
        return None
    ma = clean.rolling(window).mean()
    c, m = float(clean.iloc[-1]), float(ma.iloc[-1])
    if pd.isna(c) or pd.isna(m) or m == 0:
        return None
    return (c - m) / m * 100


def _technical_outlook(rsi: float | None, dev200: float | None) -> str:
    """RSI + 200日MA乖離 → テクニカルアウトルック (押し目判定に使う)。"""
    if rsi is None and dev200 is None:
        return "不明"
    r = rsi if rsi is not None else 50.0
    d = dev200 if dev200 is not None else 0.0

    if r < 30 and d < -10:
        return "強い押し目候補"
    if r < 35 or d < -8:
        return "押し目候補"
    if r > 72 and d > 20:
        return "強い過熱警戒"
    if r > 65 or d > 15:
        return "過熱警戒"
    return "中立"


def _pct_trend(series: pd.Series, lookback: int = 65) -> float | None:
    """lookback 日前比 (%)。データ不足時は None。"""
    n = len(series.dropna())
    lb = min(lookback, n - 1)
    if lb < 5:
        return None
    prev = float(series.dropna().iloc[-(lb + 1)])
    curr = float(series.dropna().iloc[-1])
    if prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def compute_technicals() -> list[TechnicalResult]:
    """保有銘柄全体のテクニカル指標を計算して返す。"""
    results: list[TechnicalResult] = []

    for inst in held_instruments():
        if inst.ticker is None:
            continue

        close = _load_close(inst.key)
        if close is None or len(close.dropna()) < 30:
            results.append(TechnicalResult(
                target=inst.key,
                name_ja=inst.name_ja,
                tech_outlook="データ不足",
            ))
            continue

        rsi    = _compute_rsi(close)
        dev25  = _ma_dev(close, 25)
        dev75  = _ma_dev(close, 75)
        dev200 = _ma_dev(close, 200)
        last_c = float(close.iloc[-1]) if not pd.isna(close.iloc[-1]) else None

        outlook = _technical_outlook(rsi, dev200)

        parts: list[str] = []
        if rsi is not None:
            parts.append(f"RSI={rsi:.0f}")
        if dev200 is not None:
            parts.append(f"200MA{dev200:+.1f}%")

        results.append(TechnicalResult(
            target=inst.key,
            name_ja=inst.name_ja,
            rsi=rsi,
            ma25_dev=dev25,
            ma75_dev=dev75,
            ma200_dev=dev200,
            close=last_c,
            tech_outlook=outlook,
            tech_note=", ".join(parts),
        ))

    return results


def compute_macro() -> MacroResult:
    """VIX・USD/JPY・米10年金利の現在値とトレンドを返す。"""
    macro = MacroResult()

    # --- VIX ---
    vix_s = _load_close("index_vix")
    if vix_s is not None and len(vix_s) >= 1:
        macro.vix = float(vix_s.iloc[-1])
        v = macro.vix
        if v < 15:
            macro.vix_label = "安定(<15)"
        elif v < 20:
            macro.vix_label = "やや不安(15-20)"
        elif v < 25:
            macro.vix_label = "注意(20-25)"
        elif v < 30:
            macro.vix_label = "警戒(25-30)"
        else:
            macro.vix_label = "パニック(>30)"

    # --- USD/JPY ---
    usdjpy_s = _load_close("index_usdjpy")
    if usdjpy_s is not None and len(usdjpy_s) >= 2:
        macro.usdjpy = float(usdjpy_s.dropna().iloc[-1])
        chg = _pct_trend(usdjpy_s)
        if chg is not None:
            if chg > 2.0:
                macro.usdjpy_trend = f"円安 (+{chg:.1f}%)"
            elif chg < -2.0:
                macro.usdjpy_trend = f"円高 ({chg:.1f}%)"
            else:
                macro.usdjpy_trend = f"横ばい ({chg:+.1f}%)"

    # --- 米10年金利 (^TNX: yfinanceは %値 e.g. 4.25) ---
    us10y_s = _load_close("index_us10y")
    if us10y_s is not None and len(us10y_s) >= 2:
        macro.us10y = float(us10y_s.dropna().iloc[-1])
        # 金利は絶対値差(bps換算ではなく%pt)で比較
        clean = us10y_s.dropna()
        n = len(clean)
        lb = min(65, n - 1)
        if lb >= 5:
            prev = float(clean.iloc[-(lb + 1)])
            chg_pt = macro.us10y - prev
            if chg_pt > 0.2:
                macro.us10y_trend = f"上昇 (+{chg_pt:.2f}%pt)"
            elif chg_pt < -0.2:
                macro.us10y_trend = f"低下 ({chg_pt:.2f}%pt)"
            else:
                macro.us10y_trend = f"横ばい ({chg_pt:+.2f}%pt)"

    return macro
