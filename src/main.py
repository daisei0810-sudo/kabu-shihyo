"""エントリポイント — データ取得から出力まで全ステップを順に実行。

使い方:
  python -m src.main              # Step1: データ取得のみ
  python -m src.main --step 2     # Step2: 有効性検証のみ
  python -m src.main --step 3     # Step3: スコアリングのみ
  python -m src.main --step 5     # Step5: 材料取込(SEC EDGAR+EDINET+RSS+手動入力。allには未含有)
  python -m src.main --step 6     # Step6: 通知パイプライン(§13/§17/§18)
  python -m src.main --step 7     # Step7: 予測台帳+指標重み自動更新(Investment OS Layer5)
  python -m src.main --step 8     # Step8: テーマスコアリング(Investment OS Layer4、6軸)
  python -m src.main --step 9     # Step9: 意思決定エンジン(Investment OS Layer2。allには未含有)
  python -m src.main --step 10    # Step10: リスクエンジン(Investment OS Layer6)
  python -m src.main --step 11    # Step11: 資金配分エンジン(Investment OS Layer9。allには未含有)
  python -m src.main --step all   # 全ステップ(1→2→3→8→10→7→6→4、5材料取込/9判断/11配分は含まない)

環境変数:
  FRED_API_KEY          : FRED APIキー (未設定時はFREDスキップ)
  COINGECKO_API_KEY     : CoinGecko Demo APIキー (未設定時はyfinanceフォールバック)
  SEC_EDGAR_USER_AGENT  : SEC EDGAR Fair Accessポリシー用の連絡先付きUser-Agent
                          (例: "kabu-shihyo-tool your-email@example.com")
  EDINET_API_KEY         : EDINET(日本の開示システム)無料APIキー
                          (https://api.edinet-fsa.go.jp で登録。未設定時はEDINETスキップ。
                          動作確認済み — 保有の日本上場銘柄の材料取得に使用)
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


def run_step7() -> None:
    """Step7: 予測台帳(Investment OS Layer5、最重要レイヤー)。Step3完了後に実行する。

    当日の投資判断(現状はStep3の outlook/action)を予測として記帳し、3/6/12ヶ月後の
    due_date到来分を実際の株価で評価する。3ヶ月後の答え合わせは今日記帳を始めないと
    3ヶ月遅れるため、Layer2(シナリオ判定エンジン)が未完成でも稼働させる
    (docs/investment_os_design.md §5 フェーズP1)。
    """
    logger.info("=" * 60)
    logger.info("Step7: 予測台帳開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.prediction.pipeline import run_predictions
    try:
        run_predictions()
    except Exception as exc:
        logger.warning("予測台帳パイプライン失敗(後続ステップは継続実行): %s", exc)

    # 指標重み自動更新(§4.6(c))。評価済みサンプルが貯まるまではmultiplier=1.0の
    # まま(n<10は更新しない実効サンプルガード)。同じLayer5の一部としてここで実行する。
    from src.prediction.weight_updater import run_weight_update
    try:
        run_weight_update()
    except Exception as exc:
        logger.warning("指標重み自動更新失敗(後続ステップは継続実行): %s", exc)
    logger.info("=" * 60)


def run_step8() -> None:
    """Step8: テーマスコアリング(Investment OS Layer4、6軸ルーブリック)。Step3完了後に実行する。

    構造変化30/需給25/業績20/バリュエーション10/資金流入10/政策追い風5でテーマを採点し
    outputs/theme_scores.csv へ出力する。保有情報を含まない集計値のみのため公開で問題ない
    (`--step all` に含む)。
    """
    logger.info("=" * 60)
    logger.info("Step8: テーマスコアリング開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.scoring.theme_score import compute_all_theme_scores, save_theme_scores_csv
    try:
        results = compute_all_theme_scores()
        save_theme_scores_csv(results)
        for r in results:
            logger.info(
                "  [%s] total=%s conf=%.0f%%",
                r.theme, f"{r.total:.0f}" if r.total is not None else "--",
                r.confidence_pct * 100,
            )
    except Exception as exc:
        logger.warning("テーマスコアリング失敗(後続ステップは継続実行): %s", exc)
    logger.info("=" * 60)


def run_step10() -> None:
    """Step10: リスクエンジン(Investment OS Layer6)。Step3完了後に実行する。

    保有銘柄×6リスクカテゴリ(regulation/tech_defeat/dilution/competition_loss/
    capex_cut/customer_churn)を評価する。個別銘柄ごとの悪化理由(どの懸念材料で
    警戒しているか)は private/risk_scores.csv(非公開)、テーマ集約のrisk_level
    (0-3、具体的理由は含まない)は outputs/risk_level_by_theme.csv(公開)へ出力する
    (docs/investment_os_design.md §8確定事項。collapse_watch.csvと同じ扱い)。
    `--step all` に含む(private/への書き込みは既存のportfolio_signal_scores.csv等と
    同様、private companion repoが未設定でもクラッシュしない揮発動作)。
    """
    logger.info("=" * 60)
    logger.info("Step10: リスクエンジン開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.risk.pipeline import run_risk_engine
    try:
        run_risk_engine()
    except Exception as exc:
        logger.warning("リスクエンジン失敗(後続ステップは継続実行): %s", exc)
    logger.info("=" * 60)


def run_step11() -> None:
    """Step11: 資金配分エンジン(Investment OS Layer9)。Step8・Step10完了後に実行する。

    テーマスコア×リスクヘアカット×相関ペナルティでルールベース配分を算出する。
    推奨配分・現在配分(private/holdings.csvから)・差分はいずれも保有資産構成を
    示すため private/allocation.csv(非公開)へ出力する。Step9と同じ理由により
    `--step all` にはまだ含めない。
    """
    logger.info("=" * 60)
    logger.info("Step11: 資金配分エンジン開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.allocation.pipeline import run_allocation
    try:
        run_allocation()
    except Exception as exc:
        logger.warning("資金配分エンジン失敗: %s", exc)
    logger.info("=" * 60)


def run_step12() -> None:
    """Step12: 新規発掘エンジン(Investment OS Layer7/8)。Step8・Step10完了後に実行する。

    非保有銘柄のランキング(L7)と新テーマ候補の追跡(L9)。保有銘柄の判断を
    一切含まないため、他レイヤーと異なり公開データとして outputs/ へ出力する
    (docs/investment_os_design.md §8確定事項の対象外)。`--step all` に含む。
    """
    logger.info("=" * 60)
    logger.info("Step12: 新規発掘エンジン開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.discovery.pipeline import run_discovery
    try:
        run_discovery()
    except Exception as exc:
        logger.warning("新規発掘エンジン失敗(後続ステップは継続実行): %s", exc)
    logger.info("=" * 60)


def run_step9() -> None:
    """Step9: 意思決定エンジン(Investment OS Layer2)。Step3(・Step8)完了後に実行する。

    シナリオ(bull/neutral/bear)を評価しDecisionRecordを生成、L5予測台帳へpush型で
    記帳し、非公開の投資判断レポート(private/decision_report.md)を出力する。
    DecisionRecordは保有銘柄の売買判断そのものであり、docs/investment_os_design.md
    §8確定事項により公開してはいけない。private companion repo(③A)による永続化は
    実装済みだが、`--step all` には含めない(明示的な実行のみとし、日次自動実行は
    daily.yml側でPRIVATE_REPO_PAT設定済みの場合のみ`--step 9`を個別に呼ぶ)。
    """
    logger.info("=" * 60)
    logger.info("Step9: 意思決定エンジン開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.decision.pipeline import run_decisions
    from src.reporting.decision_report import generate_decision_report
    try:
        records = run_decisions()
        generate_decision_report()
        logger.info("private/decision_report.md 生成完了(%d件の判断、非公開)", len(records))
    except Exception as exc:
        logger.warning("意思決定エンジン失敗: %s", exc)
    logger.info("=" * 60)


def run_step5() -> None:
    """Step5: 材料取込(SEC EDGAR + EDINET + RSS + 手動入力)。Phase6 基盤。

    ニュース/IR/政府発表を取込み、重複検知・鮮度判定を経て data/materials.db
    (揮発キャッシュ) へ登録し、data/materials/*.jsonl(正本)へ書き戻す。
    `--step all` にはまだ含めない: 日次自動実行(GitHub Actions)へ組み込む前に、
    手動実行で実データに対する動作を確認すること。

    EDINET(日本の開示システム)は動作確認済み。EDINET_API_KEY未設定時は
    自動的にスキップされる(クラッシュしない)。未取得の場合は
    https://api.edinet-fsa.go.jp で無料登録すること。
    """
    logger.info("=" * 60)
    logger.info("Step5: 材料取込開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.config import MATERIALS_DB, MATERIALS_DUMP_DIR, held_instruments
    from src.materials.ingest import run_ingest

    # SEC EDGARは米国上場企業のみ対象(.T等の日本ティッカーは対象外)。
    us_company_queries = [
        inst.name_ja for inst in held_instruments()
        if inst.ticker and not inst.ticker.endswith(".T") and "-USD" not in inst.ticker
    ]
    # EDINETは日本上場企業(.Tティッカー)が対象。SEC EDGARが一切カバーできない
    # 保有銘柄の大半(フジクラ/ローツェ/キオクシア等)をここで補う。
    jp_company_aliases = [
        inst.name_ja for inst in held_instruments()
        if inst.ticker and inst.ticker.endswith(".T")
    ]

    counts = run_ingest(
        db_path=MATERIALS_DB,
        dump_dir=MATERIALS_DUMP_DIR,
        company_queries=us_company_queries,
        edgar_forms=["8-K", "10-Q", "10-K"],
        edinet_company_aliases=jp_company_aliases,
    )
    logger.info(
        "材料取込サマリ: SEC EDGAR=%d件 / EDINET=%d件 / RSS=%d件 / 手動=%d件",
        counts.get("sec_edgar", 0), counts.get("edinet", 0),
        counts.get("rss", 0), counts.get("manual", 0),
    )
    logger.info("=" * 60)


def run_step6() -> None:
    """Step6: 通知パイプライン(§13/§17/§18)。Step3完了後、Step4より前に実行する。

    dip/sell閾値・実需指数/AIバブルスコアの変化・崩壊警戒LEVEL上昇・投資判断変化・
    CAPEX急変を検知し outputs/notifications/notifications.jsonl へ登録する。
    §13事後検証(backtests)のpending生成・期日到来分の評価も同時に行う。
    `--step all` に含む(通知はStep3出力を読むだけの後段処理でクラッシュしても
    Step4のレポート生成自体は独立して継続できるよう、内部で例外を握り潰す設計)。
    """
    logger.info("=" * 60)
    logger.info("Step6: 通知パイプライン開始  %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    from src.notifications.pipeline import run_notifications
    try:
        run_notifications()
    except Exception as exc:
        logger.warning("通知パイプライン失敗(Step4は継続実行): %s", exc)
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
        choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "all"],
        default="1",
        help="実行するステップ (default: 1)。5(材料取込)・9(意思決定エンジン)・"
             "11(資金配分)は明示指定時のみ実行、allには未含有(5はPhase6動作確認中、"
             "9・11は非公開出力のため日次自動実行はdaily.yml側でPRIVATE_REPO_PAT設定時"
             "のみ個別に呼ぶ)。6(通知)・7(予測台帳)・8(テーマスコア)・10(リスク)・"
             "12(新規発掘、保有銘柄を含まないため公開)はallに含む。"
             "all の実行順は 1→2→3→8→10→12→7→6→4",
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

    if args.step == "5":
        run_step5()

    if args.step in ("8", "all"):
        run_step8()

    if args.step in ("10", "all"):
        run_step10()

    if args.step in ("12", "all"):
        run_step12()

    if args.step in ("7", "all"):
        run_step7()

    if args.step == "9":
        run_step9()

    if args.step == "11":
        run_step11()

    if args.step in ("6", "all"):
        run_step6()

    if args.step in ("4", "all"):
        run_step4()

    logger.info("完了。")


if __name__ == "__main__":
    main()
