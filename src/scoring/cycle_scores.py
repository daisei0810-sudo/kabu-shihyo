"""サイクルスコア群 — AI/光通信/量子/ロボティクス/CoWoS/HBM。

指示書§3.1が列挙する7種のサイクルスコアのうち、無料データで意味ある信頼度を
持てる4種(AI・光通信・量子・ロボティクス)を実装し、単一銘柄proxyしか無い2種
(CoWoS・HBM)は confidence を強制的に低くキャップした「参考値」として提供する。
電力設備サイクルは対象銘柄・proxy銘柄がユニバースに存在しないため実装しない
(unavailableとして扱う。無理に作ると全構成要素unavailableでscore=Noneにしかならず有害無益)。

判断基準(P-hacking回避のため事前固定・後から検証可能な形で明文化):
  バスケット構成銘柄が2銘柄以上かつverified価格を持つ → 実装(通常confidence)
  1銘柄のみのproxy → 実装するがconfidenceを0.3上限にキャップし「参考値」と明示
  0銘柄(対象銘柄なし) → 実装しない(unavailable)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED
from src.scoring.capex_trend import capex_trend_score
from src.scoring.components import ComponentScore, aggregate_components
from src.scoring.normalizer import score_from_series

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

CONFIDENCE_CAP_SINGLE_STOCK = 0.30


@dataclass
class BasketDefinition:
    """バスケット型サイクルスコアの定義。"""

    name_ja: str
    constituents: list[str]
    base_confidence: float
    reference_only: bool
    note: str = ""


BASKET_DEFINITIONS: dict[str, BasketDefinition] = {
    "optical": BasketDefinition(
        name_ja="光通信サイクル",
        constituents=["fujikura", "sumitomo_electric", "furukawa_electric", "murata"],
        base_confidence=0.5,
        reference_only=False,
    ),
    "quantum": BasketDefinition(
        name_ja="量子商用化サイクル",
        constituents=["ionq", "dwave", "rigetti", "ibm", "honeywell"],
        base_confidence=0.5,
        reference_only=False,
    ),
    "robotics": BasketDefinition(
        name_ja="ロボティクス量産化サイクル",
        constituents=["harmonic", "fanuc", "yaskawa", "nabtesco"],
        base_confidence=0.5,
        reference_only=False,
    ),
    "cowos": BasketDefinition(
        name_ja="CoWoSサイクル(参考値)",
        constituents=["lasertec_rorze"],
        base_confidence=CONFIDENCE_CAP_SINGLE_STOCK,
        reference_only=True,
        note="CoWoS直接指標は無料では取得不可。ローツェ株価の弱い代理のみ。",
    ),
    "hbm": BasketDefinition(
        name_ja="HBMサイクル(参考値)",
        constituents=["kioxia"],
        base_confidence=CONFIDENCE_CAP_SINGLE_STOCK,
        reference_only=True,
        note="HBM直接指標は無料では取得不可。キオクシア株価の弱い代理のみ。",
    ),
}


@dataclass
class CycleScore:
    """1サイクルスコアの結果。"""

    key: str
    name_ja: str
    score: float | None
    confidence_pct: float
    n_constituents: int
    n_available: int
    constituents: list[str] = field(default_factory=list)
    reference_only: bool = False
    note: str = ""


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
        logger.warning("load failed key=%s: %s", key, exc)
        return None


def basket_score(
    keys: list[str], processed_dir: Path = PROCESSED_DIR
) -> tuple[float | None, str, int]:
    """複数銘柄の株価を初期値正規化して平均したバスケットの0-100スコア。

    engine.py::_load_peer_basket と同一ロジックの汎用版(自己proxy除外なし、
    サイクルスコアは特定ターゲットの代理ではなくレイヤー全体の合成指標のため)。
    戻り値: (score, note, 有効銘柄数)。
    """
    series_list: list[pd.Series] = []
    for key in keys:
        s = _load_close(key, processed_dir)
        if s is not None and len(s) > 0 and float(s.iloc[0]) != 0:
            series_list.append(s / float(s.iloc[0]))

    n_available = len(series_list)
    if n_available == 0:
        return None, "構成銘柄データなし", 0

    basket = pd.concat(series_list, axis=1).mean(axis=1).dropna()
    if basket.empty:
        return None, "バスケット合成後データなし", n_available

    latest = float(basket.iloc[-1])
    score, method_note = score_from_series(basket, latest)
    return score, f"{method_note} (構成銘柄{n_available}/{len(keys)})", n_available


def compute_basket_cycle_score(
    key: str, processed_dir: Path = PROCESSED_DIR
) -> CycleScore:
    """BASKET_DEFINITIONS に基づく1サイクルスコアを計算する。"""
    definition = BASKET_DEFINITIONS[key]
    constituents = list(definition.constituents)
    name_ja = definition.name_ja
    base_confidence = definition.base_confidence
    reference_only = definition.reference_only

    score, note, n_available = basket_score(constituents, processed_dir)

    if score is None:
        confidence = 0.0
    else:
        coverage = n_available / len(constituents) if constituents else 0.0
        confidence = base_confidence * coverage
        if reference_only:
            confidence = min(confidence, CONFIDENCE_CAP_SINGLE_STOCK)

    full_note = f"{definition.note} {note}".strip()

    return CycleScore(
        key=key,
        name_ja=name_ja,
        score=score,
        confidence_pct=round(confidence, 3),
        n_constituents=len(constituents),
        n_available=n_available,
        constituents=constituents,
        reference_only=reference_only,
        note=full_note,
    )


def compute_ai_cycle_score(processed_dir: Path = PROCESSED_DIR) -> CycleScore:
    """AIサイクル上昇継続確率 = hyperscaler capex(0.4) + NVIDIA capex(0.2) + SOXモメンタム(0.4)。

    CAPEXはscore_from_seriesが使えない(サンプル数不足)ためcapex_trend_scoreを使う。
    """
    components: list[ComponentScore] = []

    hyperscaler_path = processed_dir / "capex_hyperscaler_total.parquet"
    if hyperscaler_path.exists():
        try:
            df = pd.read_parquet(hyperscaler_path)
            s, note = capex_trend_score(df["hyperscaler_capex_total"])
            components.append(ComponentScore(
                name="ハイパースケーラーCAPEX", score=s, weight=0.4,
                available=s is not None, data_quality="verified", note=note,
            ))
        except Exception as exc:
            logger.warning("hyperscaler capex trend failed: %s", exc)
            components.append(ComponentScore(
                name="ハイパースケーラーCAPEX", score=None, weight=0.4,
                available=False, data_quality="verified", note=f"読込失敗: {exc}",
            ))
    else:
        components.append(ComponentScore(
            name="ハイパースケーラーCAPEX", score=None, weight=0.4,
            available=False, data_quality="verified", note="データなし",
        ))

    nvda_capex_path = processed_dir / "capex_nvda.parquet"
    if nvda_capex_path.exists():
        try:
            df = pd.read_parquet(nvda_capex_path)
            s, note = capex_trend_score(df["capex"])
            components.append(ComponentScore(
                name="NVIDIA CAPEX", score=s, weight=0.2,
                available=s is not None, data_quality="verified", note=note,
            ))
        except Exception as exc:
            logger.warning("nvda capex trend failed: %s", exc)
            components.append(ComponentScore(
                name="NVIDIA CAPEX", score=None, weight=0.2,
                available=False, data_quality="verified", note=f"読込失敗: {exc}",
            ))
    else:
        components.append(ComponentScore(
            name="NVIDIA CAPEX", score=None, weight=0.2,
            available=False, data_quality="verified", note="データなし",
        ))

    sox_series = _load_close("index_sox", processed_dir)
    if sox_series is not None:
        s, note = score_from_series(sox_series, float(sox_series.iloc[-1]))
        components.append(ComponentScore(
            name="SOX指数モメンタム", score=s, weight=0.4,
            available=s is not None, data_quality="proxy", note=note,
        ))
    else:
        components.append(ComponentScore(
            name="SOX指数モメンタム", score=None, weight=0.4,
            available=False, data_quality="proxy", note="データなし",
        ))

    agg = aggregate_components(components, "AIサイクルスコア")
    return CycleScore(
        key="ai_cycle",
        name_ja="AIサイクル上昇継続",
        score=agg.score,
        confidence_pct=agg.confidence_pct,
        n_constituents=len(components),
        n_available=sum(1 for c in components if c.available),
        constituents=["capex_hyperscaler_total", "capex_nvda", "index_sox"],
        reference_only=False,
        note="hyperscaler capex(0.4) + NVIDIA capex(0.2) + SOXモメンタム(0.4)の加重合成",
    )


def compute_cycle_scores(processed_dir: Path = PROCESSED_DIR) -> list[CycleScore]:
    """実装対象の全サイクルスコアを計算して返す。

    電力設備サイクルは対象銘柄がユニバースに存在しないため計算しない
    (指示書§3.1の7種中、実装可能な6種のみを返す)。
    """
    results = [compute_ai_cycle_score(processed_dir)]
    for key in ("optical", "quantum", "robotics", "cowos", "hbm"):
        results.append(compute_basket_cycle_score(key, processed_dir))
    return results
