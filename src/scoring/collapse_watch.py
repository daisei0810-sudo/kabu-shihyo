"""AIサイクル崩壊先行警戒(§11)。

指示書は15項目を毎日監視しLEVEL1(3項目悪化)/LEVEL2(5項目悪化)/LEVEL3(7項目悪化)を
判定するが、無料データで監視可能なのは6項目のみ(残り12項目はHBM/CoWoS/OpenAI/GPU在庫
等の一次指標で無料APIが存在しない)。

閾値は指示書の比率(3/15=20%, 5/15=33%, 7/15=47%)を6項目にスケールして再導出した
(2/3/4項目、四捨五入。ただし1項目だと単発ノイズで誤発報するためLEVEL1は切り上げて2項目)。
この閾値はバックテストで最適化していない事前固定値であり、後から検証可能な形で
ここに導出根拠を残す(methodology.md の多重検定・ルックアヘッド回避思想を踏襲)。

各項目の悪化判定閾値(+0.3%pt, -5%, -8%等)も事前固定値であり、当日以前のデータのみを
使う(ルックアヘッドなし)。判定はすべて生データ(price_*.parquet)の直接差分に基づくため、
スコア履歴(score_history.py)の蓄積を待たず導入初日から機能する。

LEVEL3発生時は保有7銘柄(フジクラ/ローツェ/キオクシア/アドバンテスト/TOWA/芝浦/村田)を
強制再評価フラグとして提示する(自動売買はしない、表示のみ)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED
from src.scoring.cycle_scores import _load_close

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

# LEVEL閾値の導出根拠: 指示書15項目版の比率(3/5/7 = 20%/33%/47%)を
# 監視可能6項目にスケール(2/3/4項目)。バックテスト未実施の事前固定値。
LEVEL_THRESHOLDS: dict[int, int] = {1: 2, 2: 3, 3: 4}

HOLDINGS_FORCE_REEVAL: list[str] = [
    "fujikura", "lasertec_rorze", "kioxia", "advantest", "towa", "shibaura", "murata",
]

# 監視不能12項目(指示書§11の一次崩壊シグナル。無料APIなし)
UNAVAILABLE_WATCH_ITEMS: list[str] = [
    "HBM価格下落", "HBM ASP下落", "HBMリードタイム短縮", "CoWoSリードタイム短縮",
    "GPUクラウド価格下落", "GPU稼働率Proxy低下", "OpenAI調達条件悪化", "OpenAI評価額低下",
    "Rubin納期遅延", "GPU/HBM/CoWoS在庫積み上がり", "ETF資金流出", "信用過熱",
]

OPTICAL_BASKET_KEYS = ["fujikura", "sumitomo_electric", "furukawa_electric", "murata"]


@dataclass
class WatchItem:
    """1監視項目の判定結果。"""

    name: str
    deteriorated: bool | None   # None = データ不足で判定不能
    value_note: str
    available: bool


@dataclass
class CollapseWatchResult:
    """崩壊警戒の総合判定。"""

    level: int                          # 0-3
    n_deteriorated: int
    n_monitorable: int                  # 監視可能項目数(=6)
    items: list[WatchItem] = field(default_factory=list)
    unavailable_items: list[str] = field(default_factory=list)
    force_reeval_holdings: list[str] = field(default_factory=list)
    note: str = ""


def _ma_deviation(series: pd.Series, window: int) -> float | None:
    """(直近値 - 移動平均) / 移動平均 × 100 (%)。データ不足時はNone。"""
    clean = series.dropna()
    if len(clean) < window:
        return None
    ma = clean.rolling(window).mean()
    c, m = float(clean.iloc[-1]), float(ma.iloc[-1])
    if pd.isna(c) or pd.isna(m) or m == 0:
        return None
    return (c - m) / m * 100


def _level_change(series: pd.Series, lookback: int = 65) -> float | None:
    """lookback日前との絶対水準差(%ポイント等)。データ不足時はNone。"""
    clean = series.dropna()
    if len(clean) < lookback + 1:
        return None
    prev = float(clean.iloc[-(lookback + 1)])
    curr = float(clean.iloc[-1])
    return curr - prev


def _check_us10y(processed_dir: Path) -> WatchItem:
    s = _load_close("index_us10y", processed_dir)
    if s is None:
        return WatchItem("長期金利上昇", None, "データなし", False)
    chg = _level_change(s, lookback=65)
    if chg is None:
        return WatchItem("長期金利上昇", None, "データ不足(65日分未満)", False)
    deteriorated = chg > 0.3
    return WatchItem("長期金利上昇", deteriorated, f"65日変化={chg:+.2f}%pt(閾値+0.3%pt)", True)


def _check_sox(processed_dir: Path) -> WatchItem:
    s = _load_close("index_sox", processed_dir)
    if s is None:
        return WatchItem("SOX指数下落", None, "データなし", False)
    dev = _ma_deviation(s, window=25)
    if dev is None:
        return WatchItem("SOX指数下落", None, "データ不足(25日分未満)", False)
    deteriorated = dev < -5.0
    return WatchItem("SOX指数下落", deteriorated, f"25日MA乖離={dev:+.1f}%(閾値-5%)", True)


def _check_nvidia(processed_dir: Path) -> WatchItem:
    s = _load_close("nvidia", processed_dir)
    if s is None:
        return WatchItem("NVIDIA株価下落", None, "データなし", False)
    dev = _ma_deviation(s, window=25)
    if dev is None:
        return WatchItem("NVIDIA株価下落", None, "データ不足(25日分未満)", False)
    deteriorated = dev < -8.0
    return WatchItem("NVIDIA株価下落", deteriorated, f"25日MA乖離={dev:+.1f}%(閾値-8%)", True)


def _check_hyperscaler_capex(processed_dir: Path) -> WatchItem:
    path = processed_dir / "capex_hyperscaler_total.parquet"
    if not path.exists():
        return WatchItem("ハイパースケーラーCAPEX減速", None, "データなし", False)
    try:
        df = pd.read_parquet(path)
        s = df["hyperscaler_capex_total"].dropna()
    except Exception as exc:
        logger.warning("hyperscaler capex read failed: %s", exc)
        return WatchItem("ハイパースケーラーCAPEX減速", None, f"読込失敗: {exc}", False)
    if len(s) < 2:
        return WatchItem("ハイパースケーラーCAPEX減速", None, "四半期データ不足", False)
    prev, curr = float(s.iloc[-2]), float(s.iloc[-1])
    if prev == 0:
        return WatchItem("ハイパースケーラーCAPEX減速", None, "前期値が0", False)
    qoq = (curr - prev) / abs(prev)
    deteriorated = qoq < 0
    return WatchItem(
        "ハイパースケーラーCAPEX減速", deteriorated, f"QoQ={qoq:+.0%}(マイナスで悪化)", True
    )


def _check_optical_basket(processed_dir: Path) -> WatchItem:
    from src.scoring.cycle_scores import basket_score

    score, note, n_available = basket_score(OPTICAL_BASKET_KEYS, processed_dir)
    if n_available == 0:
        return WatchItem("光通信バスケット下落", None, "構成銘柄データなし", False)

    series_list = []
    for key in OPTICAL_BASKET_KEYS:
        s = _load_close(key, processed_dir)
        if s is not None and len(s) > 0 and float(s.iloc[0]) != 0:
            series_list.append(s / float(s.iloc[0]))
    if not series_list:
        return WatchItem("光通信バスケット下落", None, "構成銘柄データなし", False)
    basket = pd.concat(series_list, axis=1).mean(axis=1).dropna()
    dev = _ma_deviation(basket, window=25)
    if dev is None:
        return WatchItem("光通信バスケット下落", None, "データ不足(25日分未満)", False)
    deteriorated = dev < -7.0
    return WatchItem(
        "光通信バスケット下落", deteriorated, f"25日MA乖離={dev:+.1f}%(閾値-7%)", True
    )


def _check_vix(processed_dir: Path) -> WatchItem:
    s = _load_close("index_vix", processed_dir)
    if s is None:
        return WatchItem("VIX急騰", None, "データなし", False)
    latest = float(s.dropna().iloc[-1])
    deteriorated = latest > 25.0
    return WatchItem("VIX急騰", deteriorated, f"VIX={latest:.1f}(閾値>25)", True)


def compute_collapse_watch(processed_dir: Path = PROCESSED_DIR) -> CollapseWatchResult:
    """§11 AIサイクル崩壊先行警戒(6項目版)を計算する。"""
    items = [
        _check_us10y(processed_dir),
        _check_sox(processed_dir),
        _check_nvidia(processed_dir),
        _check_hyperscaler_capex(processed_dir),
        _check_optical_basket(processed_dir),
        _check_vix(processed_dir),
    ]
    n_deteriorated = sum(1 for i in items if i.deteriorated is True)

    level = 0
    for lv in (3, 2, 1):
        if n_deteriorated >= LEVEL_THRESHOLDS[lv]:
            level = lv
            break

    force_reeval = list(HOLDINGS_FORCE_REEVAL) if level >= 3 else []

    return CollapseWatchResult(
        level=level,
        n_deteriorated=n_deteriorated,
        n_monitorable=len(items),
        items=items,
        unavailable_items=list(UNAVAILABLE_WATCH_ITEMS),
        force_reeval_holdings=force_reeval,
        note=(
            f"監視可能{len(items)}項目中{n_deteriorated}項目が悪化 → LEVEL{level}。"
            f"閾値(2/3/4項目)は指示書15項目版の比率(20%/33%/47%)をスケールした"
            "事前固定値(バックテスト未実施)。HBM/CoWoS/OpenAI等の一次シグナルは"
            "無料では監視不能なため、本警戒は市場価格の遅行的反応に依存する。"
        ),
    )
