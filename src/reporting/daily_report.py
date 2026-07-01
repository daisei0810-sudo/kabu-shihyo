"""日次レポート生成 — outputs/daily_report.md を生成。

portfolio_signal_scores.csv と indicator_scorecard.csv を読み込み、
Markdown 形式のサマリーレポートを出力する。
データがない場合でもクラッシュしない。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)

_OUTLOOK_ICON: dict[str, str] = {
    "強気": "🟢",
    "中立-強気": "🟡",
    "中立(要確認)": "🟡",
    "中立-弱気": "🟠",
    "中立": "⚪",
    "弱気": "🔴",
    "不明": "❓",
}


def _icon(outlook: str) -> str:
    for key, icon in _OUTLOOK_ICON.items():
        if key in outlook:
            return icon
    return "⚪"


_TRIGGER_ICON: dict[str, str] = {
    "dip": "🟢", "sell": "🔴", "demand_index": "🔵", "ai_bubble": "🟠",
    "collapse": "🚨", "decision_change": "🟡", "capex": "🟣", "material": "📰",
}


def _section_notifications() -> list[str]:
    """§17/§18: 本日の通知セクション(daily_report冒頭に配置)。

    notifications.jsonl は Step6(通知パイプライン)が既に生成済みのものを読むだけ。
    daily_report.py はMarkdown整形の責務のみを持つ(notifications→reportingの
    一方向依存)。
    """
    lines: list[str] = ["## 🔔 本日の通知", ""]
    try:
        from src.notifications.store import load_notifications
        notifications = [n for n in load_notifications() if n.status == "active"]
    except Exception as exc:
        logger.warning("notifications load failed: %s", exc)
        lines += ["*通知データなし (Step6 未実行)*", ""]
        return lines

    lines[0] = f"## 🔔 本日の通知 ({len(notifications)}件)"
    lines.append(
        "> 変化のあった項目のみ表示。送信先(メール/Slack)は未実装のため、"
        "本レポート内表示が通知の実体。"
    )
    lines.append("")

    if not notifications:
        lines.append(
            "> 本日、通知条件を満たす変化はありませんでした。"
            "(初回実行時は判断履歴が無いため判断変更通知は出ません＝正常)"
        )
        lines.append("")
        return lines

    for n in notifications:
        icon = _TRIGGER_ICON.get(n.trigger_type, "🔔")
        title = n.name_ja or n.target or n.trigger_type
        if n.prev_judgment and n.curr_judgment:
            headline = f"{icon} [{title}] 判断変更: {n.prev_judgment} → {n.curr_judgment}"
        else:
            headline = f"{icon} [{title}] {n.change_reason}"
        lines.append(f"### {headline}")
        lines.append("")
        lines.append(f"- 通知日時: {n.notified_at}")
        if n.change_confidence is not None:
            lines.append(f"- 変更確信度(代理値): {n.change_confidence:.0f}")
        lines.append(f"- 理由: {n.change_reason}")
        if n.dip_score is not None or n.sell_score is not None:
            lines.append(
                f"- 押し目・売り時 ⚠️暫定版: dip={_fmt_score(n.dip_score)} "
                f"/ sell={_fmt_score(n.sell_score)} / hold={_fmt_score(n.hold_score)} "
                f"→ {n.dip_sell_decision or '--'}"
            )
        lines.append("")

    lines.append(
        "> 📭 材料連動の通知条件(顧客確認/ガイダンス修正/受注残変化/補助金確定)は、"
        "対象となる材料データが未取得のため待機中です(未実装ではありません)。"
    )
    lines.append("")
    return lines


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


def _section_portfolio(df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## ポートフォリオ シグナル", ""]
    portfolio = df[~df["target"].str.contains("demand", na=False)].copy()

    if portfolio.empty:
        lines += ["*シグナルデータなし*", ""]
        return lines

    lines.append("| 銘柄 | Hard | Extended | Confidence | Outlook | Action |")
    lines.append("|------|-----:|---------:|:----------:|---------|--------|")
    for _, row in portfolio.iterrows():
        icon = _icon(str(row.get("outlook", "")))
        lines.append(
            f"| {row.get('name_ja', row['target'])} "
            f"| {_fmt_score(row.get('hard_score'))} "
            f"| {_fmt_score(row.get('extended_score'))} "
            f"| {_fmt_pct(row.get('confidence_pct'))} "
            f"| {icon} {row.get('outlook', '--')} "
            f"| {row.get('action', '--')} |"
        )

    hard_avg = portfolio["hard_score"].dropna().mean()
    ext_avg  = portfolio["extended_score"].dropna().mean()
    lines.append("")
    lines.append(
        f"**ポートフォリオ平均** — "
        f"Hard: {_fmt_score(hard_avg)} / Extended: {_fmt_score(ext_avg)}"
    )
    lines.append("")
    return lines


def _section_xrp(df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## XRP 専用スコア", ""]

    for target, label in [
        ("xrp_lock_demand", "ロック需要スコア"),
        ("xrp_real_demand",  "総合実需スコア"),
    ]:
        row = df[df["target"] == target]
        if row.empty:
            lines.append(f"*{label}: データなし*")
            lines.append("")
            continue
        r = row.iloc[0]
        score = _fmt_score(r.get("hard_score"))
        conf  = _fmt_pct(r.get("confidence_pct"))
        name  = str(r.get("name_ja", label))
        note_raw = r.get("signal_note", "")
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


def _section_technicals(tech: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## テクニカル判定 (RSI・移動平均乖離)", ""]
    if tech is None or tech.empty:
        lines += ["*テクニカルデータなし (Step3 未実行)*", ""]
        return lines

    _TECH_ICON: dict[str, str] = {
        "強い押し目候補": "🟢",
        "押し目候補":     "🟡",
        "中立":           "⚪",
        "過熱警戒":       "🟠",
        "強い過熱警戒":   "🔴",
        "データ不足":     "❓",
        "不明":           "❓",
    }

    def _icon(outlook: str) -> str:
        for k, ic in _TECH_ICON.items():
            if k in outlook:
                return ic
        return "⚪"

    lines.append("| 銘柄 | RSI | 25MA乖離 | 200MA乖離 | 判定 |")
    lines.append("|------|----:|--------:|---------:|------|")
    for _, row in tech.iterrows():
        rsi   = _fmt_score(row.get("rsi"))
        d25   = f"{float(row.get('ma25_dev')):+.1f}%" if pd.notna(row.get("ma25_dev")) else "--"
        d200  = f"{float(row.get('ma200_dev')):+.1f}%" if pd.notna(row.get("ma200_dev")) else "--"
        out   = str(row.get("tech_outlook", "--"))
        icon  = _icon(out)
        lines.append(
            f"| {row.get('name_ja', row.get('target',''))} "
            f"| {rsi} | {d25} | {d200} | {icon} {out} |"
        )
    lines.append("")
    lines.append("> RSI<30 + 200MA乖離<-10% = 強い押し目候補  "
                 "| RSI>70 + 200MA乖離>+20% = 強い過熱警戒")
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


def _section_dip_sell(ds: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## 押し目・売り時判定 (簡易版・暫定)", ""]
    lines.append(
        "> ⚠️ 材料データ(ガイダンス修正・受注残変化等)は未反映の暫定版。"
        "テクニカル指標(RSI/MA乖離)とHard/Extendedスコアのみで近似。"
        "ニュース・材料監視の実装後に本判定へ置き換え予定。"
    )
    lines.append("")
    if ds is None or ds.empty:
        lines += ["*データなし (Step3 未実行)*", ""]
        return lines

    _DECISION_ICON: dict[str, str] = {
        "強い押し目": "🟢",
        "押し目候補": "🟡",
        "保有継続":   "⚪",
        "過熱警戒":   "🟠",
        "売り時候補": "🔴",
        "不明":       "❓",
    }

    def _icon(decision: str) -> str:
        return _DECISION_ICON.get(decision, "⚪")

    lines.append("| 銘柄 | dip_score | sell_score | hold_score | 判定 | 推奨アクション |")
    lines.append("|------|----------:|-----------:|-----------:|------|---------------|")
    for _, row in ds.iterrows():
        dip  = _fmt_score(row.get("dip_score"))
        sell = _fmt_score(row.get("sell_score"))
        hold = _fmt_score(row.get("hold_score"))
        dec  = str(row.get("decision", "--"))
        icon = _icon(dec)
        lines.append(
            f"| {row.get('name_ja', row.get('target',''))} "
            f"| {dip} | {sell} | {hold} "
            f"| {icon} {dec} | {row.get('recommended_action', '--')} |"
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
    """outputs/daily_report.md を生成してレポート文字列を返す。

    portfolio_signal_scores.csv / indicator_scorecard.csv がない場合は
    その旨を記載して継続する(クラッシュしない)。
    """
    now = datetime.now()
    lines: list[str] = [
        f"# 先行指標監視レポート {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 推測でスコアを断定しない。データが取れない指標は「取得不可/信頼度低」と明示する。  ",
        "> Hard スコア = verified 指標のみ。Extended = proxy/estimated を信頼度重み付きで加算。",
        "",
    ]

    signals_df    = _load_csv("portfolio_signal_scores.csv")
    sc_df         = _load_csv("indicator_scorecard.csv")
    tech_df       = _load_csv("technical_scores.csv")
    macro_df      = _load_csv("macro_indicators.csv")
    ds_df         = _load_csv("dip_sell_scores.csv")
    collapse_df   = _load_csv("collapse_watch.csv")
    demand_df     = _load_csv("demand_index_scores.csv")
    components_df = _load_csv("demand_index_components.csv")
    cycles_df     = _load_csv("cycle_scores.csv")

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

    lines.extend(_section_notifications())

    lines.extend(_section_collapse_watch(
        collapse_df if collapse_df is not None else pd.DataFrame(), collapse_level, collapse_note
    ))

    if signals_df is not None and not signals_df.empty:
        lines.extend(_section_portfolio(signals_df))
        lines.extend(_section_xrp(signals_df))
    else:
        lines += ["*portfolio_signal_scores.csv なし (Step3 未実行)*", ""]

    lines.extend(_section_demand_index(
        demand_df if demand_df is not None else pd.DataFrame(),
        components_df if components_df is not None else pd.DataFrame(),
    ))
    lines.extend(_section_cycle_scores(cycles_df if cycles_df is not None else pd.DataFrame()))
    lines.extend(_section_macro(macro_df if macro_df is not None else pd.DataFrame()))
    lines.extend(_section_technicals(tech_df if tech_df is not None else pd.DataFrame()))
    lines.extend(_section_dip_sell(ds_df if ds_df is not None else pd.DataFrame()))
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
