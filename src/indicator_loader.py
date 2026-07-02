"""Indicator(config.py)のメタデータに基づく時系列読み込み — データ駆動ローダー。

`src/validation/`(Step2統計検証)と`src/scoring/`(Step3 Hard/Extendedスコア)の
両方から使われる共通ロジック。以前はこの読み込みロジックが両パッケージに別々に
ハードコードされており(indicator_key→(parquet_stem, column)のmapping辞書が
validation/run_validation.pyとscoring/engine.pyの2箇所に重複)、新しい指標を
追加するたびに両方を手で更新する必要があった。これを一本化する。

step2_verifiable=False の指標(四半期capex等、Step2の統計的厳密性を満たせない)は
Step2からは除外するが、Extendedスコア計算(score_from_series)では引き続き使う
(score_from_series自身が短い系列は自然にNoneを返すため、二重にガードする必要はない)。
呼び出し側が `respect_step2_flag=True` を指定した場合のみ Step2 向けに除外する。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, Indicator

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

# ピアバスケット系ローダーの構成銘柄(自己除外はpeer_basket_excluding内で行う)
OPTICAL_PEERS: list[str] = ["fujikura", "sumitomo_electric", "furukawa_electric", "murata"]
ROBOTICS_PEERS: list[str] = ["harmonic", "fanuc", "yaskawa", "nabtesco"]
QUANTUM_PEERS: list[str] = ["ionq", "dwave", "rigetti", "ibm"]  # honeywell除外(循環参照回避)

# 月次データを日次へffillする際の最大補完日数
MONTHLY_FFILL_LIMIT_DAYS = 40


def read_parquet_column(
    stem: str, column: str, processed_dir: Path = PROCESSED_DIR
) -> pd.Series | None:
    """*.parquet から指定カラムを1系列読む(tz除去)。"""
    path = processed_dir / f"{stem}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        logger.warning("parquet read failed %s: %s", stem, exc)
        return None
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if column in df.columns:
        s = df[column].dropna()
        return s if not s.empty else None
    if isinstance(df.columns, pd.MultiIndex):
        try:
            s = df.xs(column, axis=1, level=0).squeeze().dropna()
            return s if not s.empty else None
        except Exception:
            return None
    return None


def peer_basket_excluding(
    target_key: str, peers: list[str], processed_dir: Path = PROCESSED_DIR
) -> pd.Series | None:
    """対象を除いたピア銘柄の等加重正規化バスケットを作る(自己proxy回避)。"""
    series_list: list[pd.Series] = []
    for p in peers:
        if p == target_key:
            continue  # 自分自身は除外(循環参照を防ぐ)
        s = read_parquet_column(f"price_{p}", "Close", processed_dir)
        if s is not None and len(s) > 30 and float(s.iloc[0]) != 0:
            series_list.append(s / float(s.iloc[0]))  # 初日=1に正規化
    if not series_list:
        return None
    basket = pd.concat(series_list, axis=1).mean(axis=1)
    basket = basket.dropna()
    return basket if not basket.empty else None


_SPECIAL_LOADERS: dict[str, Callable[[str, Path], pd.Series | None]] = {
    "peer_basket:optical": lambda target, d: peer_basket_excluding(target, OPTICAL_PEERS, d),
    "peer_basket:robotics": lambda target, d: peer_basket_excluding(target, ROBOTICS_PEERS, d),
    "peer_basket:quantum": lambda target, d: peer_basket_excluding(target, QUANTUM_PEERS, d),
}


def load_indicator_series(
    ind: Indicator,
    target_key: str,
    processed_dir: Path = PROCESSED_DIR,
    respect_step2_flag: bool = False,
) -> pd.Series | None:
    """Indicatorのメタデータに基づき時系列を読み込む。

    Args:
        respect_step2_flag: Trueの場合、step2_verifiable=Falseの指標を
            常にNoneにする(Step2統計検証パイプライン向け)。Falseの場合は
            score_from_series等の呼び出し側が短い系列を自然にNone扱いする
            前提で読み込む(Step3 Extendedスコア向け)。
    """
    if respect_step2_flag and not ind.step2_verifiable:
        return None

    series: pd.Series | None = None

    if ind.loader is not None:
        loader_fn = _SPECIAL_LOADERS.get(ind.loader)
        if loader_fn is None:
            logger.warning("未登録のloader: %s (indicator=%s)", ind.loader, ind.key)
            return None
        series = loader_fn(target_key, processed_dir)
    elif ind.parquet_stem is not None and ind.column is not None:
        series = read_parquet_column(ind.parquet_stem, ind.column, processed_dir)
    else:
        return None

    if series is None or series.empty:
        return None

    if ind.freq == "monthly":
        series = series.asfreq("D").ffill(limit=MONTHLY_FFILL_LIMIT_DAYS)
        series = series.dropna()
        if series.empty:
            return None

    series.name = ind.key
    return series
