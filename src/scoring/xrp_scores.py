"""XRP専用スコア — 総合実需スコアとロック需要スコアを計算。

methodology.md §5 XRP専用スコア:

  総合実需スコア 0–100:
    XRPL DeFi TVL / Stablecoin TVL / AMM XRP balance / XRPL Tx count の加重合成。

  ロック需要スコア 0–100 (因果連鎖の代理):
    RLUSD供給↑ → XRPL利用↑ → AMM/ブリッジ/担保でXRPロック↑ → 流通量↓ → 価格↑
    AMM内XRP残高を主代理とし、取得不可コンポーネント(Lending/RWA)は
    スコアに算入せず confidence を自然に下げる。

confidence の計算方針:
  = (利用可能コンポーネントの重み合計) / (全コンポーネントの重み合計)
  unavailable コンポーネントは score=None だが weight は分母に含まれるため、
  自動的に confidence が下がる。追加のペナルティは不要。

段階: 0–30 未発生 / 30–50 初動 / 50–70 加速 / 70–90 本格化 / 90+ 需給ショック
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, XRP_LOCK_DEMAND_STAGES
from src.scoring.components import ComponentScore, aggregate_components, make_component_from_series

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

# 後方互換エイリアス(既存の公開API・テストが参照する名前を維持)。
# 実体は src/scoring/components.py の汎用 ComponentScore と同一構造。
XrpComponentScore = ComponentScore


@dataclass
class XrpDemandResult:
    """XRP需要スコアの計算結果。"""

    score: float | None            # 0–100 総合スコア (None = 算出不可)
    confidence_pct: float          # 0–1 信頼度 (利用可能重み / 全重み)
    data_coverage_pct: float       # 0–1 verified データカバレッジ
    components: list[XrpComponentScore] = field(default_factory=list)
    stage: str = ""                # ロック需要スコア専用の段階ラベル
    note: str = ""


def _load_series_and_latest(stem: str, col: str) -> tuple[pd.Series | None, float | None]:
    """Parquetから指定カラムの系列と最新値を返す。ファイルがなければ (None, None)。"""
    path = PROCESSED_DIR / f"{stem}.parquet"
    if not path.exists():
        return None, None
    try:
        df = pd.read_parquet(path)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        df = df.sort_index()
        if col not in df.columns:
            return None, None
        series = df[col].dropna()
        if series.empty:
            return None, None
        return series, float(series.iloc[-1])
    except Exception as exc:
        logger.warning("load failed: %s / %s: %s", stem, col, exc)
        return None, None


def _make_component(
    name: str,
    series: pd.Series | None,
    latest: float | None,
    weight: float,
    note: str = "",
) -> XrpComponentScore:
    """系列と最新値からコンポーネントを作る。データなしは available=False。

    実装は src/scoring/components.py の汎用版に委譲(挙動不変)。
    """
    return make_component_from_series(
        name, series, latest, weight, data_quality="verified", note=note
    )


def _aggregate_components(
    components: list[XrpComponentScore],
    label: str,
) -> XrpDemandResult:
    """コンポーネントリストから総合スコアと confidence を計算。

    confidence = 利用可能重み / 全重み合計 (unavailable が分母に入ることで自然に低下)。
    実装は src/scoring/components.py の汎用版に委譲(挙動不変)。
    """
    agg = aggregate_components(components, label)
    return XrpDemandResult(
        score=agg.score,
        confidence_pct=agg.confidence_pct,
        data_coverage_pct=agg.data_coverage_pct,
        components=agg.components,
        note=agg.note,
    )


def _lock_demand_stage(score: float) -> str:
    """スコアから段階ラベルを返す (config.XRP_LOCK_DEMAND_STAGES 参照)。"""
    for threshold, label in XRP_LOCK_DEMAND_STAGES:
        if score < threshold:
            return label
    return "需給ショック"


def compute_xrp_real_demand() -> XrpDemandResult:
    """XRP総合実需スコアを計算。

    コンポーネント (すべて verified):
      XRPL DeFi TVL        weight=0.30
      Stablecoin TVL       weight=0.25
      AMM XRP balance      weight=0.25
      XRPL Tx count        weight=0.20
    """
    s1, v1 = _load_series_and_latest("defillama_xrpl_tvl", "tvl_usd")
    s2, v2 = _load_series_and_latest("defillama_stablecoin_tvl", "stablecoin_tvl_usd")
    s3, v3 = _load_series_and_latest("xrpl_amm_XRP_RLUSD", "xrp_balance")

    # Tx count: daily_tx が優先、なければ network_stats snapshot
    s4, v4 = _load_series_and_latest("xrpl_daily_tx", "txn_count")
    if s4 is None:
        s4, v4 = _load_series_and_latest("xrpl_network_stats", "txn_count")

    components = [
        _make_component("XRPL DeFi TVL", s1, v1, weight=0.30),
        _make_component("XRPL Stablecoin TVL", s2, v2, weight=0.25),
        _make_component("AMM XRP balance", s3, v3, weight=0.25,
                        note="AMM内XRP残高"),
        _make_component("XRPL Tx count", s4, v4, weight=0.20),
    ]
    return _aggregate_components(components, "XRP総合実需スコア")


def compute_xrp_lock_demand() -> XrpDemandResult:
    """XRPロック需要スコアを計算。

    因果連鎖:
      RLUSD供給↑ → XRPL利用↑ → AMM/ブリッジ/担保でXRPロック↑ → 流通量↓ → 価格↑

    Verified コンポーネント (スコア算入):
      AMM内XRP残高       weight=0.40  ← ロックの直接代理(主代理)
      RLUSD発行残高      weight=0.35  ← ロック需要の起点
      Stablecoin TVL    weight=0.15
      XRPL Txカウント   weight=0.10

    Unavailable コンポーネント (スコア非算入、分母に含めて confidence を下げる):
      Lending/Collateral  weight=0.20  → confidence -20%
      Institutional DeFi  weight=0.15  → confidence -15%
      RWA担保利用         weight=0.10  → confidence -10%

    Verified合計=1.00 / 全合計=1.45
    → データ揃い時の confidence ≈ 1.00/1.45 ≈ 0.69 (中程度)
    """
    s1, v1 = _load_series_and_latest("xrpl_amm_XRP_RLUSD", "xrp_balance")
    s2, v2 = _load_series_and_latest("xrpl_rlusd_supply", "rlusd_supply")
    s3, v3 = _load_series_and_latest("defillama_stablecoin_tvl", "stablecoin_tvl_usd")

    s4, v4 = _load_series_and_latest("xrpl_daily_tx", "txn_count")
    if s4 is None:
        s4, v4 = _load_series_and_latest("xrpl_network_stats", "txn_count")

    components: list[XrpComponentScore] = [
        _make_component(
            "AMM内XRP残高(ロック直接代理)", s1, v1, weight=0.40,
            note="AMMプールに固定されたXRP量。ロック需要の主代理。",
        ),
        _make_component(
            "RLUSD発行残高", s2, v2, weight=0.35,
            note="RLUSD増発 → XRP担保/ブリッジ需要増の起点。",
        ),
        _make_component("XRPL Stablecoin TVL", s3, v3, weight=0.15),
        _make_component("XRPL Txカウント", s4, v4, weight=0.10),
        # --- unavailable: スコア非算入、分母に含める ---
        XrpComponentScore(
            name="Lending/Collateral利用量", score=None, weight=0.20,
            available=False, data_quality="unavailable",
            note="無料APIなし → スコア非算入。confidence分母に含め信頼度を下げる。",
        ),
        XrpComponentScore(
            name="Institutional DeFi", score=None, weight=0.15,
            available=False, data_quality="unavailable",
            note="無料APIなし → スコア非算入。",
        ),
        XrpComponentScore(
            name="RWA担保利用", score=None, weight=0.10,
            available=False, data_quality="unavailable",
            note="無料APIなし → スコア非算入。",
        ),
    ]

    result = _aggregate_components(components, "XRPロック需要スコア")

    if result.score is not None:
        result.stage = _lock_demand_stage(result.score)

    result.note = (
        "ロック需要スコアは AMM内XRP残高(主代理)・RLUSD供給残高・XRPL TVL の加重合成。"
        "Lending/RWAデータは無料取得不可のため confidence は中程度(~69%)。"
        "本格化以上の判定には「信頼度: 中／代理ベース」を必ず確認すること。"
    )
    return result
