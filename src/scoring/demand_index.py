"""実需指数(real_demand_index) + AIバブルスコア(ai_bubble_score) + 乖離。

指示書§4 Phase2-3。構成要素の大半(GPUクラウド価格・HBM価格/ASP/リードタイム・
CoWoSリードタイム・OpenAI評価額・信用倍率・ETF資金流入等)は無料では取得不可のため、
既存の components.py パターン(unavailableもweightを分母に残しconfidenceを下げる)
に従って正直に除外する。推測でスコアを埋めない。

実装しなかったもの(理由を明記):
  - PER/EV-EBITDA/PSR/FCF Yield(yfinance Ticker.info由来): infoの取得成否が不安定な上、
    Step3(オフライン原則)にネットワーク依存を持ち込むことになるため、Phase7では
    見送り。将来実装する場合はStep1側にfetcherを追加しparquet化してから読む設計にすること。
  - AI売上÷AI投資: 分子(NVIDIA日次株価)と分母(四半期capex)の頻度粒度が不一致で
    厳密な比率計算が困難なため、Phase7では unavailable として明示するに留める。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED
from src.scoring.capex_trend import capex_trend_score, growth_rate_to_score
from src.scoring.components import ComponentScore, aggregate_components
from src.scoring.cycle_scores import _load_close, basket_score
from src.scoring.normalizer import score_from_series

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

SEMICAP_BASKET = ["lasertec_rorze", "advantest", "towa", "kokusai_electric", "shibaura"]
OPTICAL_BASKET = ["fujikura", "sumitomo_electric", "furukawa_electric", "murata"]


@dataclass
class DemandIndexResult:
    """実需指数 / AIバブルスコアの計算結果(components.AggregateResult相当+ラベル)。"""

    label: str
    score: float | None
    confidence_pct: float
    data_coverage_pct: float
    components: list[ComponentScore]
    note: str = ""


def _capex_component(
    processed_dir: Path, parquet_key: str, col: str, name: str, weight: float
) -> ComponentScore:
    path = processed_dir / f"{parquet_key}.parquet"
    if not path.exists():
        return ComponentScore(
            name=name, score=None, weight=weight,
            available=False, data_quality="verified", note="データなし",
        )
    try:
        df = pd.read_parquet(path)
        s, note = capex_trend_score(df[col])
        return ComponentScore(
            name=name, score=s, weight=weight,
            available=s is not None, data_quality="verified", note=note,
        )
    except Exception as exc:
        logger.warning("%s capex trend failed: %s", name, exc)
        return ComponentScore(
            name=name, score=None, weight=weight,
            available=False, data_quality="verified", note=f"読込失敗: {exc}",
        )


def _price_percentile_component(
    key: str, name: str, weight: float, data_quality: str, processed_dir: Path
) -> ComponentScore:
    series = _load_close(key, processed_dir)
    if series is None:
        return ComponentScore(
            name=name, score=None, weight=weight,
            available=False, data_quality=data_quality, note="データなし",
        )
    s, note = score_from_series(series, float(series.iloc[-1]))
    return ComponentScore(
        name=name, score=s, weight=weight,
        available=s is not None, data_quality=data_quality, note=note,
    )


def _unavailable(name: str, weight: float, note: str) -> ComponentScore:
    return ComponentScore(
        name=name, score=None, weight=weight,
        available=False, data_quality="unavailable", note=note,
    )


def compute_real_demand_index(processed_dir: Path = PROCESSED_DIR) -> DemandIndexResult:
    """AIデータセンター/半導体の実需指数(0-100)。

    構成: hyperscaler capex(0.25) + NVIDIA capex(0.10) + TSMC capex(0.10)
        + SOXモメンタム(0.15) + 光通信バスケット(0.15) + 半導体装置バスケット(0.10)
    unavailable(重みのみ分母算入): AI売上/投資(0.05)・GPUクラウド価格(0.10)・
        HBM価格/ASP/リードタイム(0.10)・CoWoSリードタイム(0.05)・電力契約/受注残(0.05)
    """
    components: list[ComponentScore] = [
        _capex_component(
            processed_dir, "capex_hyperscaler_total", "hyperscaler_capex_total",
            "ハイパースケーラーCAPEX", 0.25,
        ),
        _capex_component(processed_dir, "capex_nvda", "capex", "NVIDIA CAPEX", 0.10),
        _capex_component(processed_dir, "capex_tsm", "capex", "TSMC CAPEX", 0.10),
        _price_percentile_component(
            "index_sox", "SOX指数モメンタム", 0.15, "proxy", processed_dir
        ),
    ]

    optical_score, optical_note, optical_n = basket_score(OPTICAL_BASKET, processed_dir)
    components.append(ComponentScore(
        name="光通信バスケット(フジクラ/住友電工/古河電工/村田)",
        score=optical_score, weight=0.15,
        available=optical_score is not None, data_quality="proxy",
        note=optical_note,
    ))

    semicap_score, semicap_note, semicap_n = basket_score(SEMICAP_BASKET, processed_dir)
    components.append(ComponentScore(
        name="半導体装置バスケット(ローツェ/アドバンテスト/TOWA/KOKUSAI/芝浦)",
        score=semicap_score, weight=0.10,
        available=semicap_score is not None, data_quality="proxy",
        note=semicap_note,
    ))

    components += [
        _unavailable("AI売上÷AI投資", 0.05,
                     "分子(日次株価)と分母(四半期capex)の粒度不一致のため見送り"),
        _unavailable("GPUクラウド価格", 0.10, "無料APIなし → 取得不可"),
        _unavailable("HBM価格/ASP/リードタイム", 0.10, "無料APIなし → 取得不可"),
        _unavailable("CoWoSリードタイム", 0.05, "無料APIなし → 取得不可"),
        _unavailable("データセンター電力契約/受注残", 0.05, "無料APIなし → 取得不可"),
    ]

    agg = aggregate_components(components, "実需指数")
    return DemandIndexResult(
        label="real_demand_index",
        score=agg.score,
        confidence_pct=agg.confidence_pct,
        data_coverage_pct=agg.data_coverage_pct,
        components=agg.components,
        note="AI/半導体の実需を無料データで近似。GPUクラウド価格・HBM価格等の"
             "一次指標は取得不可のためconfidenceは中程度に留まる。",
    )


def compute_ai_bubble_score(processed_dir: Path = PROCESSED_DIR) -> DemandIndexResult:
    """AI関連銘柄の過熱度・バブル度(0-100、高いほど過熱・警戒)。

    構成: NVIDIA株価レンジ位置(0.20) + SOXレンジ位置(0.15) + 米10年金利水準(0.15)
        + VIX逆数(0.05) + NVIDIA3ヶ月モメンタム(0.10)
    unavailable: AI売上/投資逆数(0.05)・バリュエーション比率(0.10, yfinance info不安定
        のためPhase7では見送り)・OpenAI評価額(0.05)・Stargate資金調達(0.03)・
        ETF資金流入(0.05)・信用倍率(0.05)
    """
    components: list[ComponentScore] = [
        _price_percentile_component(
            "nvidia", "NVIDIA株価レンジ位置", 0.20, "verified", processed_dir
        ),
        _price_percentile_component(
            "index_sox", "SOX指数レンジ位置", 0.15, "verified", processed_dir
        ),
        _price_percentile_component(
            "index_us10y", "米10年金利水準", 0.15, "verified", processed_dir
        ),
    ]

    vix_series = _load_close("index_vix", processed_dir)
    if vix_series is not None:
        raw_score, note = score_from_series(vix_series, float(vix_series.iloc[-1]))
        inv_score = None if raw_score is None else round(100 - raw_score, 1)
        components.append(ComponentScore(
            name="VIX逆数(低VIX=楽観過剰=バブル的)", score=inv_score, weight=0.05,
            available=inv_score is not None, data_quality="verified",
            note=f"逆数化: {note}",
        ))
    else:
        components.append(ComponentScore(
            name="VIX逆数(低VIX=楽観過剰=バブル的)", score=None, weight=0.05,
            available=False, data_quality="verified", note="データなし",
        ))

    nvda_series = _load_close("nvidia", processed_dir)
    if nvda_series is not None and len(nvda_series.dropna()) >= 6:
        clean = nvda_series.dropna()
        lb = min(65, len(clean) - 1)
        prev = float(clean.iloc[-(lb + 1)])
        curr = float(clean.iloc[-1])
        momentum = (curr - prev) / abs(prev) if prev != 0 else None
        mom_score = None if momentum is None else growth_rate_to_score(momentum, saturation=0.30)
        components.append(ComponentScore(
            name="NVIDIA 3ヶ月モメンタム", score=mom_score, weight=0.10,
            available=mom_score is not None, data_quality="verified",
            note=f"{lb}日騰落率={momentum:+.0%}" if momentum is not None else "データ不足",
        ))
    else:
        components.append(ComponentScore(
            name="NVIDIA 3ヶ月モメンタム", score=None, weight=0.10,
            available=False, data_quality="verified", note="データ不足",
        ))

    components += [
        _unavailable("AI売上÷AI投資(逆数)", 0.05, "実需指数と同様、粒度不一致のため見送り"),
        _unavailable("PER/EV-EBITDA/PSR/FCF Yield", 0.10,
                     "yfinance Ticker.infoが不安定 + Step3オフライン原則との整合のため"
                     "Phase7では見送り(Step1側fetcher実装が前提)"),
        _unavailable("OpenAI評価額/資金調達条件", 0.05, "非上場、無料時系列なし"),
        _unavailable("Stargate資金調達状況", 0.03, "無料時系列なし"),
        _unavailable("ETF資金流入", 0.05, "無料APIなし(config既存のetf_flowsと同一理由)"),
        _unavailable("信用倍率/信用評価損益率", 0.05, "無料日次APIなし"),
    ]

    agg = aggregate_components(components, "AIバブルスコア")
    return DemandIndexResult(
        label="ai_bubble_score",
        score=agg.score,
        confidence_pct=agg.confidence_pct,
        data_coverage_pct=agg.data_coverage_pct,
        components=agg.components,
        note="株価・金利・VIXから過熱度を近似。PER等バリュエーション指標・"
             "OpenAI評価額・ETF流入は取得不可のため未算入。",
    )


def compute_divergence(
    demand: DemandIndexResult, bubble: DemandIndexResult
) -> float | None:
    """乖離 = ai_bubble_score - real_demand_index。

    +20超: 株価が実需を大きく先行(バブル警戒) / -20未満: 実需が株価に未織り込み。
    両スコアはNVIDIA株価・SOX指数等の入力を一部共有するため完全独立ではない点に注意。
    """
    if demand.score is None or bubble.score is None:
        return None
    return round(bubble.score - demand.score, 1)
