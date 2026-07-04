"""日次レポート生成 — outputs/daily_report.md を生成(公開、GitHub Pagesへデプロイ)。

indicator_scorecard.csv 等の集計データのみを読み込み、Markdown 形式のサマリー
レポートを出力する。データがない場合でもクラッシュしない。

保有銘柄ごとのスコア・outlook・action・テクニカル判定・押し目売り時判定・通知は
売買判断そのもの(docs/investment_os_design.md §8確定事項)のため、本モジュールは
扱わない。それらは private/ 配下のデータを読む `src.reporting.decision_report`
(非公開レポート)に統合されている。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)


def _section_backtest_summary() -> list[str]:
    lines: list[str] = ["## 事後検証サマリー (§13, 学習は行わない・表示のみ)", ""]
    try:
        from src.notifications.backtest_eval import summarize_backtests
        from src.notifications.store import load_backtests
        summary = summarize_backtests(load_backtests())
    except Exception as exc:
        logger.warning("backtest summary failed: %s", exc)
        lines += ["*データなし (Step6 未実行)*", ""]
        return lines

    if summary.n_pending == 0 and summary.n_evaluated == 0:
        lines += ["*通知がまだ無いため事後検証データもありません*", ""]
        return lines

    lines.append(
        f"評価待ち **{summary.n_pending}件** / 評価済み **{summary.n_evaluated}件** "
        f"/ データ欠損 **{summary.n_skipped}件**"
    )
    lines.append("")
    if summary.n_evaluated > 0:
        avg_val = summary.avg_excess_return
        fpr_val = summary.false_positive_rate
        avg = f"{avg_val*100:+.1f}%" if avg_val is not None else "--"
        fpr = f"{fpr_val*100:.0f}%" if fpr_val is not None else "--"
        lines.append(f"平均超過収益: {avg} / 誤検知率: {fpr}")
        lines.append("")
    if summary.next_due_date:
        lines.append(f"> 次回評価予定日: {summary.next_due_date}")
        lines.append("")
    lines.append(
        "> §14自動学習は評価済みbacktestが100件を超えるまで実装しない"
        "(学習対象データが無い状態で学習器を書かない方針)。"
    )
    lines.append("")
    return lines


def _load_csv(name: str) -> pd.DataFrame | None:
    path = OUTPUT_DIR / name
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("load failed: %s: %s", name, exc)
        return None


def _fmt_score(val: object) -> str:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f"{f:.0f}" if not pd.isna(f) else "--"
    except (TypeError, ValueError):
        return "--"


def _fmt_pct(val: object) -> str:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f"{f * 100:.0f}%" if not pd.isna(f) else "--"
    except (TypeError, ValueError):
        return "--"


def _section_xrp(df: pd.DataFrame) -> list[str]:
    """XRP集計スコア(outputs/xrp_demand_scores.csv)。個別銘柄の売買判断を含まない
    市場指標のため公開する(§8確定事項)。
    """
    lines: list[str] = ["## XRP 専用スコア", ""]

    for target, label in [
        ("xrp_lock_demand", "ロック需要スコア"),
        ("xrp_real_demand",  "総合実需スコア"),
    ]:
        row = df[df["target"] == target] if not df.empty else pd.DataFrame()
        if row.empty:
            lines.append(f"*{label}: データなし*")
            lines.append("")
            continue
        r = row.iloc[0]
        score = _fmt_score(r.get("score"))
        conf  = _fmt_pct(r.get("confidence_pct"))
        name  = str(r.get("name_ja", label))
        note_raw = r.get("note", "")
        note = "" if pd.isna(note_raw) else str(note_raw)[:120]
        lines.append(f"**{name}**: スコア **{score}** / Confidence {conf}")
        if note:
            lines.append(f"> {note}")
        lines.append("")

    return lines


def _section_macro(macro: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## マクロ環境", ""]
    if macro is None or macro.empty:
        lines += ["*マクロデータなし (VIX/USDJPY/US10Y は Step1 で取得)*", ""]
        return lines

    row = macro.iloc[0]

    def _v(col: str, fmt: str = ".2f") -> str:
        v = row.get(col)
        try:
            return f"{float(v):{fmt}}" if pd.notna(v) else "--"
        except (TypeError, ValueError):
            return "--"

    lines.append("| 指標 | 現在値 | トレンド |")
    lines.append("|------|-------:|---------|")
    lines.append(f"| VIX | {_v('vix', '.1f')} ({row.get('vix_label','--')}) | — |")
    lines.append(
        f"| USD/JPY | {_v('usdjpy', '.2f')} | {row.get('usdjpy_trend','')} |"
    )
    lines.append(
        f"| 米10年金利 | {_v('us10y', '.2f')}% | {row.get('us10y_trend','')} |"
    )
    lines.append("")
    return lines


_COLLAPSE_LEVEL_ICON: dict[int, str] = {0: "🟢", 1: "🟡", 2: "🟠", 3: "🔴"}

_HOLDINGS_NAME_JA: dict[str, str] = {
    "fujikura": "フジクラ", "lasertec_rorze": "ローツェ", "kioxia": "キオクシア",
    "advantest": "アドバンテスト", "towa": "TOWA", "shibaura": "芝浦メカトロニクス",
    "murata": "村田製作所",
}


def _section_collapse_watch(cw: pd.DataFrame, level: int | None, note: str) -> list[str]:
    lines: list[str] = ["## ⚠️ AIサイクル崩壊先行警戒 (§11)", ""]
    if cw is None or cw.empty or level is None:
        lines += ["*データなし (Step3 未実行)*", ""]
        return lines

    icon = _COLLAPSE_LEVEL_ICON.get(level, "❓")
    n_det = int(cw["deteriorated"].sum()) if "deteriorated" in cw.columns else 0
    lines.append(f"### {icon} LEVEL{level} ({n_det}/{len(cw)}項目 悪化)")
    lines.append("")
    lines.append("| 監視項目 | 判定 | 詳細 |")
    lines.append("|---------|:----:|------|")
    for _, row in cw.iterrows():
        det = row.get("deteriorated")
        if pd.isna(det):
            mark = "❓"
        else:
            mark = "🔴悪化" if bool(det) else "🟢正常"
        lines.append(f"| {row.get('name','--')} | {mark} | {row.get('value_note','')} |")
    lines.append("")
    if note:
        lines.append(f"> {note}")
        lines.append("")

    if level >= 3:
        holdings = ", ".join(_HOLDINGS_NAME_JA.get(k, k) for k in _HOLDINGS_NAME_JA)
        lines.append(
            f"> 🔴 **LEVEL3到達 — 保有7銘柄の強制再評価を推奨**: {holdings}"
        )
        lines.append("")
    return lines


def _section_demand_index(demand: pd.DataFrame, components: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## 実需指数 / AIバブルスコア", ""]
    if demand is None or demand.empty:
        lines += ["*データなし (Step3 未実行)*", ""]
        return lines

    def _chg(v: object) -> str:
        try:
            f = float(v)  # type: ignore[arg-type]
            return f"{f:+.1f}" if not pd.isna(f) else "履歴蓄積中"
        except (TypeError, ValueError):
            return "履歴蓄積中"

    lines.append("| 指標 | スコア | Confidence | 変化(1日/1週/1月) |")
    lines.append("|------|-------:|:----------:|-------------------|")
    for _, row in demand.iterrows():
        label = "実需指数" if row.get("label") == "real_demand_index" else "AIバブルスコア"
        chg = (
            f"{_chg(row.get('change_1d'))} / "
            f"{_chg(row.get('change_1w'))} / {_chg(row.get('change_1m'))}"
        )
        lines.append(
            f"| {label} | {_fmt_score(row.get('score'))} "
            f"| {_fmt_pct(row.get('confidence_pct'))} | {chg} |"
        )

    real_row = demand[demand["label"] == "real_demand_index"]
    bubble_row = demand[demand["label"] == "ai_bubble_score"]
    if not real_row.empty and not bubble_row.empty:
        r_score = real_row.iloc[0].get("score")
        b_score = bubble_row.iloc[0].get("score")
        if pd.notna(r_score) and pd.notna(b_score):
            divergence = float(b_score) - float(r_score)
            interp = (
                "株価が実需を大きく先行(バブル警戒)" if divergence > 20
                else "実需が株価に未織り込み(割安の可能性)" if divergence < -20
                else "実需とバブルスコアは概ね整合"
            )
            lines.append("")
            lines.append(f"**乖離(AIバブル−実需) = {divergence:+.1f}** — {interp}")
    lines.append("")

    if components is not None and not components.empty:
        lines.append("<details><summary>構成要素の内訳</summary>")
        lines.append("")
        for label_key, label_ja in [
            ("real_demand_index", "実需指数"), ("ai_bubble_score", "AIバブルスコア"),
        ]:
            sub = components[components["label"] == label_key]
            if sub.empty:
                continue
            lines.append(f"**{label_ja}**")
            lines.append("")
            lines.append("| 構成要素 | スコア | 重み | 品質 |")
            lines.append("|---------|-------:|-----:|------|")
            for _, row in sub.iterrows():
                avail = row.get("available")
                score_disp = _fmt_score(row.get("score")) if avail else "取得不可"
                lines.append(
                    f"| {row.get('component','')} | {score_disp} "
                    f"| {row.get('weight',0):.2f} | {row.get('data_quality','')} |"
                )
            lines.append("")
        lines.append("</details>")
        lines.append("")

    return lines


def _section_cycle_scores(cycles: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## サイクルスコア (AI/光通信/量子/ロボティクス/CoWoS/HBM)", ""]
    if cycles is None or cycles.empty:
        lines += ["*データなし (Step3 未実行)*", ""]
        return lines

    lines.append("| サイクル | スコア | Confidence | 構成銘柄 | 備考 |")
    lines.append("|---------|-------:|:----------:|:--------:|------|")
    for _, row in cycles.iterrows():
        ref = "⚠️参考値" if bool(row.get("reference_only")) else ""
        lines.append(
            f"| {row.get('name_ja','')} {ref} | {_fmt_score(row.get('score'))} "
            f"| {_fmt_pct(row.get('confidence_pct'))} "
            f"| {row.get('n_available',0)}/{row.get('n_constituents',0)} "
            f"| {str(row.get('note',''))[:60]} |"
        )
    lines.append("")
    lines.append(
        "> ⚠️参考値 = 単一銘柄proxyのみ(CoWoS/HBM)。confidence上限30%にキャップ済み。"
        " 電力設備サイクルは対象銘柄がユニバースに無いため実装せず(unavailable)。"
    )
    lines.append("")
    return lines


def _section_scorecard(sc: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## 有効性スコアカード (定常相関ベース)", ""]

    if sc.empty:
        lines += ["*indicator_scorecard.csv がありません (Step2 未実行)*", ""]
        return lines

    _RANK_BADGE = {"A+": "🏆", "A": "🥇", "B": "🥈", "C": "🥉", "D": "❌"}

    lines.append("| ランク | 指標 | 対象 | 変化相関 | 的中率 | 実効N | 信頼度コメント |")
    lines.append("|--------|------|------|:--------:|:------:|:-----:|---------------|")
    for _, row in sc.sort_values("rank").iterrows():
        rank = str(row.get("rank", "D"))
        badge = _RANK_BADGE.get(rank, rank)
        corr = f"{float(row.get('spearman_r_stationary', 0)):.3f}"
        hr   = f"{float(row.get('hit_rate', 0))*100:.0f}%"
        en   = f"{float(row.get('effective_n', 0)):.1f}"
        note = str(row.get("confidence_note", ""))[:60]
        lines.append(
            f"| {badge} {rank} "
            f"| {row.get('indicator', '--')} "
            f"| {row.get('target', '--')} "
            f"| {corr} | {hr} | {en} "
            f"| {note} |"
        )

    lines.append("")
    adopted = sc[sc["rank"].isin(["A+", "A", "B"])]
    if adopted.empty:
        lines.append(
            "> ⚠️ 現時点では A/B ランク指標はゼロ。すべて C/D (履歴不足・見せかけ相関)。"
            " データ蓄積により改善する見込み。"
        )
    lines.append("")
    return lines


def _section_data_quality() -> list[str]:
    lines: list[str] = ["## データ品質", ""]
    lines.append("| バッジ | 品質 | 説明 | スコア算入 |")
    lines.append("|:------:|------|------|:----------:|")
    lines.append("| 🟢 | verified  | 無料APIで直接取得 | Hard / Extended |")
    lines.append("| 🟡 | proxy     | 代理指標(関連株価等) | Extended のみ |")
    lines.append("| 🟠 | estimated | イベント推定 | Extended のみ |")
    lines.append("| ⚪ | unavailable | 取得不可 | 表示のみ |")
    lines.append("")
    lines.append(
        "取得不可指標: SpaceX評価額 / HBM価格 / CoWoS稼働率 / BBレシオ / "
        "取引所XRP残高 / クジラウォレット / Lending/Collateral / RWA担保"
    )
    lines.append("")
    return lines


def generate_daily_report() -> str:
    """outputs/daily_report.md を生成してレポート文字列を返す(公開)。

    indicator_scorecard.csv 等がない場合はその旨を記載して継続する(クラッシュしない)。
    保有銘柄ごとの投資判断は private/decision_report.md (非公開)を参照。
    """
    now = datetime.now()
    lines: list[str] = [
        f"# 先行指標監視レポート {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 推測でスコアを断定しない。データが取れない指標は「取得不可/信頼度低」と明示する。  ",
        "> Hard スコア = verified 指標のみ。Extended = proxy/estimated を信頼度重み付きで加算。",
        "",
        "> ℹ️ 保有銘柄ごとの投資判断(outlook/action・テクニカル判定・押し目売り時判定・"
        "通知)は非公開レポート(`private/decision_report.md`)に統合されています"
        "(docs/investment_os_design.md §8確定事項)。",
        "",
    ]

    sc_df         = _load_csv("indicator_scorecard.csv")
    macro_df      = _load_csv("macro_indicators.csv")
    collapse_df   = _load_csv("collapse_watch.csv")
    demand_df     = _load_csv("demand_index_scores.csv")
    components_df = _load_csv("demand_index_components.csv")
    cycles_df     = _load_csv("cycle_scores.csv")
    xrp_df        = _load_csv("xrp_demand_scores.csv")

    collapse_level = None
    collapse_note = ""
    if collapse_df is not None and not collapse_df.empty and "deteriorated" in collapse_df.columns:
        n_det = int(collapse_df["deteriorated"].fillna(False).astype(bool).sum())
        from src.scoring.collapse_watch import LEVEL_THRESHOLDS
        collapse_level = 0
        for lv in (3, 2, 1):
            if n_det >= LEVEL_THRESHOLDS[lv]:
                collapse_level = lv
                break
        collapse_note = (
            f"監視可能{len(collapse_df)}項目中{n_det}項目が悪化。"
            "閾値は指示書15項目版の比率をスケールした事前固定値(バックテスト未実施)。"
        )

    lines.extend(_section_collapse_watch(
        collapse_df if collapse_df is not None else pd.DataFrame(), collapse_level, collapse_note
    ))
    lines.extend(_section_xrp(xrp_df if xrp_df is not None else pd.DataFrame()))
    lines.extend(_section_demand_index(
        demand_df if demand_df is not None else pd.DataFrame(),
        components_df if components_df is not None else pd.DataFrame(),
    ))
    lines.extend(_section_cycle_scores(cycles_df if cycles_df is not None else pd.DataFrame()))
    lines.extend(_section_macro(macro_df if macro_df is not None else pd.DataFrame()))
    lines.extend(_section_backtest_summary())
    lines.extend(_section_scorecard(sc_df if sc_df is not None else pd.DataFrame()))
    lines.extend(_section_data_quality())

    lines += [
        "---",
        f"*生成: {now.isoformat()} | 先行指標監視システム*",
    ]

    report = "\n".join(lines)
    output_path = OUTPUT_DIR / "daily_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    logger.info("daily_report.md saved (%d chars)", len(report))
    return report
