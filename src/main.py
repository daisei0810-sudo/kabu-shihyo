"""エントリポイント — データ取得から出力まで全ステップを順に実行。

使い方:
  python -m src.main              # Step1: データ取得のみ
  python -m src.main --step 2     # Step2: 有効性検証のみ
  python -m src.main --step 3     # Step3: スコアリングのみ
  python -m src.main --step all   # 全ステップ

環境変数:
  FRED_API_KEY       : FRED APIキー (未設定時はFREDスキップ)
  COINGECKO_API_KEY  : CoinGecko Demo APIキー (未設定時はyfinanceフォールバック)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from src.data_sources.base import setup_logging
from src.data_sources.coingecko import CoinGeckoFetcher
from src.data_sources.defillama import DefiLlamaFetcher
from src.data_sources.fred import FredFetcher
from src.data_sources.xrpl_fetcher import XrplFetcher
from src.data_sources.yfinance_fetcher import YfinanceFetcher

logger = logging.getLogger(__name__)


def run_step1() -> int:
    """Step1: 全データソースからデータ取得・蓄積。取得失敗件数を返す。"""
    logger.info("=" * 60)
    logger.info("Step1: データ取得開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    fetchers = [
        YfinanceFetcher(),
        CoinGeckoFetcher(),
        DefiLlamaFetcher(),
        FredFetcher(),
        XrplFetcher(),
    ]

    total_ok = 0
    total_fail = 0

    for fetcher in fetchers:
        logger.info("\n--- %s ---", fetcher.source_name)
        results = fetcher.fetch()
        for r in results:
            if r.is_ok():
                total_ok += 1
            else:
                total_fail += 1

    logger.info("\n" + "=" * 60)
    logger.info("Step1 完了: 成功=%d  失敗=%d", total_ok, total_fail)
    if total_fail > 0:
        logger.warning(
            "%d 件が取得失敗。上記ログの ⚠️ を確認してください。"
            " 多くは無料API制限・非公開データで正常な動作です。",
            total_fail,
        )
    logger.info("=" * 60)
    return total_fail


def run_step3() -> None:
    """Step3: Hard/Extended スコアリング + XRP専用スコア + 投資判断を出力。"""
    logger.info("=" * 60)
    logger.info("Step3: スコアリング開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.scoring.portfolio import PortfolioScorer
    scorer = PortfolioScorer()
    result = scorer.run()
    scorer.save_csv(result)

    logger.info("\n=== ポートフォリオ シグナルサマリー ===")
    for sig in result.signals:
        logger.info(
            "  [%s] %s  hard=%s ext=%s conf=%.0f%%  outlook=%s / %s",
            sig.layer[:8],
            sig.name_ja,
            f"{sig.hard_score:.0f}" if sig.hard_score is not None else " -- ",
            f"{sig.extended_score:.0f}" if sig.extended_score is not None else " -- ",
            sig.confidence_pct * 100,
            sig.outlook,
            sig.action,
        )

    if result.xrp_lock_demand is not None:
        ld = result.xrp_lock_demand
        logger.info(
            "\nXRPロック需要スコア: %s  stage=%s  confidence=%.0f%%",
            f"{ld.score:.1f}" if ld.score is not None else "None",
            ld.stage,
            ld.confidence_pct * 100,
        )

    logger.info("Portfolio avg — Hard: %s / Extended: %s",
                result.portfolio_hard_avg, result.portfolio_extended_avg)
    logger.info("=" * 60)


def run_step4() -> None:
    """Step4: daily_report.md 生成 + Plotly ダッシュボード(PWA)出力。"""
    logger.info("=" * 60)
    logger.info("Step4: レポート・ダッシュボード生成 %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.dashboard.builder import build_dashboard
    from src.reporting.daily_report import generate_daily_report

    generate_daily_report()
    build_dashboard()

    logger.info("outputs/ に以下を出力しました:")
    logger.info("  daily_report.md   … Markdownサマリーレポート")
    logger.info("  index.html        … Plotlyダッシュボード(PWA対応)")
    logger.info("  manifest.json     … PWAマニフェスト(iPhone「ホーム画面に追加」)")
    logger.info("  sw.js             … サービスワーカー(オフラインキャッシュ)")
    logger.info("=" * 60)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="先行指標監視システム")
    parser.add_argument(
        "--step",
        choices=["1", "2", "3", "4", "all"],
        default="1",
        help="実行するステップ (default: 1)",
    )
    args = parser.parse_args()

    if args.step in ("1", "all"):
        fails = run_step1()
        if fails > 0 and args.step == "1":
            sys.exit(0)

    if args.step in ("2", "all"):
        from src.validation.run_validation import run_step2
        run_step2()

    if args.step in ("3", "all"):
        run_step3()

    if args.step in ("4", "all"):
        run_step4()

    logger.info("完了。")


if __name__ == "__main__":
    main()
