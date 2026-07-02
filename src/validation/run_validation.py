"""Step2 検証パイプライン — 保有銘柄のうち検証条件を満たすものを対象に先行指標を検証。

STEP2_TARGETSは2026-07-02にハードコード(fujikura/lasertec_rorze/xrpの3銘柄限定)から
データ駆動に変更した。config.INSTRUMENTS + config.INDICATORS から動的に対象を決定する
(build_step2_targets())。指標の読み込みも `src/indicator_loader.py`(config.Indicatorの
parquet_stem/column/loader/freq メタデータに基づくデータ駆動ローダー、
src/scoring/engine.py のExtendedスコア計算と共有)に一本化した。

出力:
  outputs/lag_correlation_matrix.csv
  outputs/event_study_results.csv
  outputs/indicator_scorecard.csv
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import (
    INDICATORS,
    MIN_PRICE_ROWS,
    OUTPUTS,
    PRICE_PROXY,
    DataQuality,
    held_instruments,
)
from src.data_sources.base import setup_logging
from src.features.engineer import FeatureEngineer
from src.features.events import ThresholdEventDetector
from src.indicator_loader import load_indicator_series
from src.validation.event_study import EventStudy
from src.validation.lag_correlation import LagCorrelationAnalyzer
from src.validation.ranker import IndicatorRanker

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")
OUTPUT_DIR = Path(OUTPUTS)


def _resolve_price_key(asset_key: str) -> str:
    """検証対象の価格系列キーを解決する(非上場銘柄は代理銘柄の価格を使う)。"""
    return PRICE_PROXY.get(asset_key, asset_key)


def _load_price(asset_key: str) -> pd.Series | None:
    """Parquetから終値系列を読む。非上場銘柄は代理銘柄(PRICE_PROXY)の価格を使う。"""
    price_key = _resolve_price_key(asset_key)
    path = PROCESSED_DIR / f"price_{price_key}.parquet"
    if not path.exists():
        logger.warning("price file not found: %s", path)
        return None
    df = pd.read_parquet(path)
    for col in ["Close", "close", "Price", "price"]:
        if col in df.columns:
            s = df[col].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            s.name = asset_key
            return s
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


def build_step2_targets() -> dict[str, str]:
    """Step2検証対象を動的に決定する。

    条件: held銘柄 かつ data_quality != UNAVAILABLE(非上場でも代理があればOK)
        かつ 価格データが MIN_PRICE_ROWS 行以上 かつ 紐づく利用可能な指標が1つ以上ある。
    spacex(価格データ無し)は自動的に除外される。qnt_tokenはconfig.py側でIndicatorが
    定義されていないため自動的に除外される(ドメイン論理の無い指標を追加しない方針)。
    """
    targets: dict[str, str] = {}
    for inst in held_instruments():
        if inst.data_quality == DataQuality.UNAVAILABLE:
            continue

        price_key = _resolve_price_key(inst.key)
        path = PROCESSED_DIR / f"price_{price_key}.parquet"
        if not path.exists():
            continue
        try:
            n_rows = len(pd.read_parquet(path))
        except Exception as exc:
            logger.warning("price row count failed for %s: %s", inst.key, exc)
            continue
        if n_rows < MIN_PRICE_ROWS:
            logger.info(
                "  %s: 価格データ%d行 < 最低%d行 → Step2対象外",
                inst.key, n_rows, MIN_PRICE_ROWS,
            )
            continue

        has_indicator = any(
            inst.key in ind.targets and ind.data_quality != DataQuality.UNAVAILABLE
            for ind in INDICATORS
        )
        if not has_indicator:
            continue

        targets[inst.key] = f"price_{price_key}"

    return targets


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

    step2_targets = build_step2_targets()
    logger.info("Step2検証対象(動的決定): %s", list(step2_targets.keys()))

    # 検証対象の指標を絞る（verified / proxy で data があるもの、Step2検証可能なもの）
    candidate_indicators = [
        ind for ind in INDICATORS
        if ind.data_quality != DataQuality.UNAVAILABLE and ind.step2_verifiable
    ]

    for asset_key in step2_targets:
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

            logger.info(
                "  指標: %s (quality=%s, freq=%s)", ind.key, ind.data_quality.value, ind.freq
            )

            series = load_indicator_series(ind, asset_key, respect_step2_flag=True)
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
                    freq=ind.freq,
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

    if all_scorecard_rows:
        sc_df = pd.concat(all_scorecard_rows, ignore_index=True)
        sc_df = ranker.apply_global_fdr_correction(sc_df)
        path = OUTPUT_DIR / "indicator_scorecard.csv"
        sc_df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("saved: %s (%d rows)", path, len(sc_df))

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
    else:
        logger.warning("indicator_scorecard.csv: 出力データなし")


if __name__ == "__main__":
    setup_logging()
    run_step2()
