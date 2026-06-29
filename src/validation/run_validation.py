"""Step2 検証パイプライン — フジクラ・ローツェ・XRP を対象に先行指標の有効性を検証。

出力:
  outputs/lag_correlation_matrix.csv
  outputs/event_study_results.csv
  outputs/indicator_scorecard.csv
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import INDICATORS, OUTPUTS, DataQuality
from src.data_sources.base import setup_logging
from src.features.engineer import FeatureEngineer
from src.features.events import ThresholdEventDetector
from src.validation.event_study import EventStudy
from src.validation.lag_correlation import LagCorrelationAnalyzer
from src.validation.ranker import IndicatorRanker

logger = logging.getLogger(__name__)

# Step2 の検証対象（資産キー → price Parquet のキー）
STEP2_TARGETS: dict[str, str] = {
    "fujikura": "price_fujikura",
    "lasertec_rorze": "price_lasertec_rorze",
    "xrp": "price_xrp",
}

PROCESSED_DIR = Path("data/processed")
OUTPUT_DIR = Path(OUTPUTS)


def _load_price(asset_key: str) -> pd.Series | None:
    """Parquetから終値系列を読む。"""
    path = PROCESSED_DIR / f"price_{asset_key}.parquet"
    if not path.exists():
        logger.warning("price file not found: %s", path)
        return None
    df = pd.read_parquet(path)
    # カラム名は yfinance の auto_adjust が揺れる。Close / close / Price を探す
    for col in ["Close", "close", "Price", "price"]:
        if col in df.columns:
            s = df[col].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            s.name = asset_key
            return s
    # マルチレベルカラムの場合
    if isinstance(df.columns, pd.MultiIndex):
        try:
            s = df.xs("Close", axis=1, level=0).squeeze().dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            s.name = asset_key
            return s
        except Exception:
            pass
    logger.warning("Close column not found in %s. columns=%s", path.name, list(df.columns)[:5])
    return None


# 光モジュール需要のピアバスケット候補(対象は実行時に除外する)
OPTICAL_PEERS: list[str] = ["fujikura", "sumitomo_electric", "furukawa_electric", "murata"]


def _load_price_close(stem: str) -> pd.Series | None:
    """price_*.parquet から Close 系列を読む(tz除去)。"""
    path = PROCESSED_DIR / f"{stem}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if "Close" in df.columns:
        return df["Close"].dropna()
    if isinstance(df.columns, pd.MultiIndex):
        try:
            return df.xs("Close", axis=1, level=0).squeeze().dropna()
        except Exception:
            return None
    return None


def _peer_basket_excluding(target_key: str, peers: list[str]) -> pd.Series | None:
    """対象を除いたピア銘柄の等加重正規化バスケットを作る(自己proxy回避)。"""
    series_list: list[pd.Series] = []
    for p in peers:
        if p == target_key:
            continue  # 自分自身は除外(循環参照を防ぐ)
        s = _load_price_close(f"price_{p}")
        if s is not None and len(s) > 30:
            series_list.append(s / s.iloc[0])  # 初日=1に正規化
    if not series_list:
        return None
    basket = pd.concat(series_list, axis=1).mean(axis=1)
    return basket.dropna()


def _load_indicator_series(indicator_key: str, target_key: str) -> pd.Series | None:
    """対応する処理済みファイルから指標値を1系列読む。

    target_key: 検証対象の資産。proxyバスケットから対象自身を除外するために使う。
    """
    # 光モジュール需要 = 対象を除いた光関連ピアバスケット(自己proxy回避)
    if indicator_key == "optical_module_demand":
        s = _peer_basket_excluding(target_key, OPTICAL_PEERS)
        if s is not None:
            s.name = indicator_key
        return s

    mapping: dict[str, tuple[str, str]] = {
        # indicator_key → (parquet_stem, column)
        "xrp_price": ("price_xrp", "Close"),
        "stablecoin_tvl": ("defillama_stablecoin_tvl", "stablecoin_tvl_usd"),
        "amm_tvl": ("defillama_xrpl_tvl", "tvl_usd"),
        "sox_index": ("price_index_sox", "Close"),
        "tsmc_capex": ("capex_tsm", "capex"),
        "nvidia_revenue": ("capex_nvda", "capex"),         # proxy: NVDAのCAPEXを売上代理に
        "hyperscaler_capex": ("capex_hyperscaler_total", "hyperscaler_capex_total"),
    }

    if indicator_key not in mapping:
        return None

    stem, col = mapping[indicator_key]
    path = PROCESSED_DIR / f"{stem}.parquet"
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    # timezone を除去
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    if col in df.columns:
        s = df[col].dropna()
        s.name = indicator_key
        return s

    # Close カラムが MultiIndex になっている場合
    if isinstance(df.columns, pd.MultiIndex):
        try:
            s = df.xs(col, axis=1, level=0).squeeze().dropna()
            s.name = indicator_key
            return s
        except Exception:
            pass

    logger.warning("column '%s' not found in %s", col, path.name)
    return None


def run_step2() -> None:
    """全検証を実行し結果を outputs/ に保存。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    eng = FeatureEngineer()
    analyzer = LagCorrelationAnalyzer()
    event_detector = ThresholdEventDetector(threshold=1.5, cooldown_days=14)
    event_study = EventStudy()
    ranker = IndicatorRanker()

    all_lag_rows: list[pd.DataFrame] = []
    all_event_rows: list[pd.DataFrame] = []
    all_scorecard_rows: list[pd.DataFrame] = []

    # 検証対象の指標を絞る（verified / proxy で data があるもの）
    candidate_indicators = [
        ind for ind in INDICATORS
        if ind.data_quality != DataQuality.UNAVAILABLE
    ]

    for asset_key in STEP2_TARGETS:
        logger.info("=== 検証対象資産: %s ===", asset_key)

        price = _load_price(asset_key)
        if price is None:
            logger.warning("skip %s: price not loaded", asset_key)
            continue

        # 将来リターン生成
        fwd_rets = eng.build_forward_returns(price, name=asset_key)

        for ind in candidate_indicators:
            # この指標が対象資産に関係するか（targets で絞る）
            if asset_key not in ind.targets:
                continue

            logger.info("  指標: %s (quality=%s)", ind.key, ind.data_quality.value)

            series = _load_indicator_series(ind.key, asset_key)
            if series is None:
                logger.info("    → データなし、スキップ")
                continue

            # 日付インデックス統一
            series.index = pd.to_datetime(series.index).tz_localize(None)

            # 特徴量生成
            try:
                feats = eng.build_indicator_features(series, name=ind.key)
            except Exception as exc:
                logger.warning("    feature build failed: %s", exc)
                continue

            if len(feats.dropna(how="all")) < 30:
                logger.warning("    → 特徴量が少なすぎる (rows=%d)、スキップ", len(feats))
                continue

            # --- ラグ相関 ---
            try:
                lag_df = analyzer.compute(feats, fwd_rets, ind.key, asset_key)
                if not lag_df.empty:
                    all_lag_rows.append(lag_df)
            except Exception as exc:
                logger.warning("    lag_correlation failed: %s", exc)
                lag_df = pd.DataFrame()

            # --- イベントスタディ ---
            # 定常化Zスコア(_dz)の急上昇をイベントとする。レベルZスコアだとトレンド相場で
            # 「常にイベント」になり見せかけの的中率を生むため。
            event_col = f"{ind.key}_dz"
            event_df = pd.DataFrame()
            if event_col in feats.columns:
                try:
                    events = event_detector.detect(feats[event_col])
                    if len(events) >= event_study.min_events:
                        event_df = event_study.analyze(
                            events, fwd_rets, ind.key, asset_key,
                            direction="above",
                        )
                        if not event_df.empty:
                            all_event_rows.append(event_df)
                except Exception as exc:
                    logger.warning("    event_study failed: %s", exc)

            # --- スコアカード ---
            try:
                sc = ranker.build_scorecard(
                    lag_df, event_df,
                    indicator_key=ind.key,
                    target_key=asset_key,
                    data_quality=ind.data_quality.value,
                    confidence_weight=ind.confidence_weight,
                )
                if not sc.empty:
                    all_scorecard_rows.append(sc)
            except Exception as exc:
                logger.warning("    scorecard failed: %s", exc)

    # --- 出力 ---
    def save(frames: list[pd.DataFrame], name: str) -> None:
        if not frames:
            logger.warning("%s: 出力データなし", name)
            return
        df = pd.concat(frames, ignore_index=True)
        path = OUTPUT_DIR / name
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("saved: %s (%d rows)", path, len(df))

    save(all_lag_rows, "lag_correlation_matrix.csv")
    save(all_event_rows, "event_study_results.csv")
    save(all_scorecard_rows, "indicator_scorecard.csv")

    # スコアカードのサマリーを表示
    if all_scorecard_rows:
        sc_df = pd.concat(all_scorecard_rows, ignore_index=True)
        logger.info("\n=== 有効性スコアカード サマリー (見出し相関=定常/変化ベース) ===")
        for _, row in sc_df.sort_values("rank").iterrows():
            logger.info(
                "  [%s] %s → %s  lag=%sd h=%sd  変化corr=%.3f (level=%.3f) hit=%.1f%%  %s",
                row["rank"],
                row["indicator"],
                row["target"],
                row["best_lag_days"],
                row["best_horizon_days"],
                row["spearman_r_stationary"],
                row["spearman_r_level"],
                row["hit_rate"] * 100,
                row["confidence_note"],
            )


if __name__ == "__main__":
    setup_logging()
    run_step2()
