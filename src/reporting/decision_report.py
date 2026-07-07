"""Layer10(投資判断レポート) — 非公開の投資判断レポートを生成する。

daily_report.py(公開、GitHub Pagesへデプロイされる)とは意図的に分離する:
本レポートはLayer2 DecisionRecord(保有銘柄ごとの売買判断そのもの)を含み、
docs/investment_os_design.md §8確定事項により公開してはいけない。出力先は
private/decision_report.md(プロジェクトルートの.gitignore対象)。

章立て(P3で「銘柄ごとの判断を理解するのに複数章を往復する必要がある」との
指摘を受け、保有銘柄単位に根拠を集約する構成へ再設計。未実装のレイヤー
(L7-8発掘)は正直に「未実装」と明示し、存在しないデータを捏造しない):
  ①ヘッダ → ②サマリー(全体の内訳・要注意判断) → ③本日の通知
  → ④リスクフラグ(悪化検知の速報) → ⑤保有銘柄別 詳細(テーマスコア内訳/
  シグナル/テクニカル/押し目・売り時/リスク/成立条件の中身を1銘柄1章に集約)
  → ⑥テーマスコア6軸(横断比較用) → ⑦Early Signal(材料note)
  → ⑧判断変更ログ → ⑨予測検証成績 → ⑩配分提案 → ⑪発掘ランキング(未実装)

入力契約: outputs/*.csv, outputs/prediction_accuracy.csv, private/decisions/*.jsonl
の読み取り専用。判定ロジック(decision/scoring/prediction)は import するが、
このモジュール自身は判定を行わない(daily_report.pyと同じ責務分離)。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS, PRIVATE_OUTPUTS
from src.decision.diff import diff
from src.decision.models import DecisionRecord, ScenarioAssessment
from src.decision.store import PRIVATE_DECISIONS_DIR, load_decisions, load_previous
from src.notifications.models import Notification

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(OUTPUTS)
PRIVATE_DIR = Path(PRIVATE_OUTPUTS)

_SCENARIO_LABEL: dict[str, str] = {"bull": "強気", "neutral": "中立", "bear": "弱気"}
_SCENARIO_ICON: dict[str, str] = {"bull": "🟢", "neutral": "⚪", "bear": "🔴"}


def _load_csv(name: str, base_dir: Path = OUTPUT_DIR) -> pd.DataFrame:
    path = base_dir / name
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("load failed: %s: %s", name, exc)
        return pd.DataFrame()


def _fmt_pct(val: float | None) -> str:
    return f"{val:.0%}" if val is not None else "--"


def _fmt_axis(val: object) -> str:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f"{f:.0f}" if not pd.isna(f) else "--"
    except (TypeError, ValueError):
        return "--"


# ---------------------------------------------------------------------------
# ①ヘッダ
# ---------------------------------------------------------------------------

def _section_header(as_of: date) -> list[str]:
    now = datetime.now()
    return [
        f"# 投資判断レポート(非公開) {as_of.isoformat()}",
        "",
        f"- レポート日: {as_of.isoformat()}",
        f"- 分析時刻: {now.strftime('%Y-%m-%d %H:%M')}",
        "- 信頼度/実測率: 各判断の項目内に個別表示(全社一律値は捏造しない)",
        "",
        "> ⚠️ **本レポートは非公開情報を含む**(保有銘柄の売買判断)。"
        "docs/investment_os_design.md §8確定事項によりGitHub Pages/公開リポジトリ"
        "には一切含めないこと。",
        "",
    ]


# ---------------------------------------------------------------------------
# ②Early Signal Layer(材料 → 先行指標 → 需給 → 構造変化)
# ---------------------------------------------------------------------------

def _section_early_signal(theme_scores_df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## Early Signal Layer(材料→先行指標→需給→構造変化)", ""]
    if theme_scores_df.empty:
        lines += ["*theme_scores.csv なし(Step3-L4未実行)*", ""]
        return lines

    lines.append(
        "> ニュース単体の羅列はしない。各テーマの構造変化/需給スコアの根拠"
        "(材料件数・サイクルスコア構成)を併記する。"
    )
    lines.append("")
    for _, row in theme_scores_df.iterrows():
        if pd.isna(row.get("total")):
            continue  # データ皆無のテーマは早期シグナル欄からは省略(⑩結論側で件数のみ言及)
        lines.append(f"**{row.get('name_ja', row.get('theme'))}**")
        note_raw = row.get("note")
        struct_note = str(note_raw)[:200] if pd.notna(note_raw) and note_raw else ""
        lines.append(f"- {struct_note}" if struct_note else "- (テーマ集計note未設定)")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# ③テーマスコア6軸
# ---------------------------------------------------------------------------

def _section_theme_scores(theme_scores_df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## テーマスコア(6軸ルーブリック)", ""]
    if theme_scores_df.empty:
        lines += ["*theme_scores.csv なし(Step3-L4未実行)*", ""]
        return lines

    lines.append(
        "| テーマ | 構造変化/30 | 需給/25 | 業績/20 | バリュエーション/10 "
        "| 資金流入/10 | 政策/5 | 総合 | Confidence |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|:---:|")
    for _, row in theme_scores_df.iterrows():
        lines.append(
            f"| {row.get('name_ja', row.get('theme'))} "
            f"| {_fmt_axis(row.get('structural_change'))} "
            f"| {_fmt_axis(row.get('supply_demand'))} "
            f"| {_fmt_axis(row.get('earnings'))} "
            f"| {_fmt_axis(row.get('valuation'))} "
            f"| {_fmt_axis(row.get('fund_flow'))} "
            f"| {_fmt_axis(row.get('policy_tailwind'))} "
            f"| {_fmt_axis(row.get('total'))} | {_fmt_pct(row.get('confidence_pct'))} |"
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# ④リスク(L6)
# ---------------------------------------------------------------------------

_RISK_CATEGORY_LABEL: dict[str, str] = {
    "regulation": "規制・制裁", "tech_defeat": "技術的敗北", "dilution": "希薄化",
    "competition_loss": "競合劣後", "capex_cut": "CAPEX減速", "customer_churn": "顧客離脱",
}


def _section_risk(risk_df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## リスクエンジン(Layer6、下落検知)", ""]
    if risk_df.empty:
        lines += ["*private/risk_scores.csv なし(--step 10 未実行)*", ""]
        return lines

    deteriorated = risk_df[risk_df["deteriorated"] == True]  # noqa: E712
    if deteriorated.empty:
        lines += ["*悪化しているカテゴリはありません*", ""]
        return lines

    lines.append("| 銘柄 | テーマ | カテゴリ | リスクスコア | 根拠 |")
    lines.append("|---|---|---|---:|---|")
    for _, row in deteriorated.iterrows():
        category = _RISK_CATEGORY_LABEL.get(str(row.get("category")), str(row.get("category")))
        lines.append(
            f"| {row.get('target')} | {row.get('theme')} | {category} "
            f"| {_fmt_axis(row.get('risk_score'))} | {row.get('evidence')} |"
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# ポートフォリオ詳細(daily_report.pyから移設。保有銘柄ごとのスコア/判断のため非公開)
# ---------------------------------------------------------------------------

_OUTLOOK_ICON: dict[str, str] = {
    "強気": "🟢", "中立-強気": "🟡", "中立(要確認)": "🟡",
    "中立-弱気": "🟠", "中立": "⚪", "弱気": "🔴", "不明": "❓",
}


def _outlook_icon(outlook: str) -> str:
    for key, icon in _OUTLOOK_ICON.items():
        if key in outlook:
            return icon
    return "⚪"


_BULLISH_OUTLOOKS: frozenset[str] = frozenset({"強気", "中立-強気"})
_BEARISH_OUTLOOKS: frozenset[str] = frozenset({"弱気", "中立-弱気"})
_BEARISH_DIP_DECISIONS: frozenset[str] = frozenset({"売り時候補", "過熱警戒"})
_BULLISH_DIP_DECISIONS: frozenset[str] = frozenset({"強い押し目", "押し目候補"})


def _detect_signal_divergence(outlook: str, dip_decision: str | None) -> str | None:
    """ポートフォリオシグナル(実需/セクター系)と押し目・売り時判定(自社株価テクニカル系)が
    逆方向を示している場合に警告文を返す。

    両者は異なる入力(前者=proxy/セクター指標のパーセンタイル、後者=自社株価の
    RSI/MA乖離)を見ているため、一致しないこと自体は異常ではない。だが並べて
    表示すると矛盾に見えるため、無理に一致させず「見ている指標が違う」ことを明示する。
    """
    if not dip_decision:
        return None
    if (outlook in _BULLISH_OUTLOOKS and dip_decision in _BEARISH_DIP_DECISIONS) or (
        outlook in _BEARISH_OUTLOOKS and dip_decision in _BULLISH_DIP_DECISIONS
    ):
        return (
            f"⚠️ ポートフォリオシグナルは「{outlook}」だが、押し目・売り時判定は"
            f"「{dip_decision}」。実需/セクター系スコアと自社株価のテクニカルが"
            "逆方向 — 詳細は「押し目・売り時判定」セクション参照。"
        )
    return None


_TECH_ICON: dict[str, str] = {
    "強い押し目候補": "🟢", "押し目候補": "🟡", "中立": "⚪",
    "過熱警戒": "🟠", "強い過熱警戒": "🔴", "データ不足": "❓", "不明": "❓",
}


def _tech_icon(outlook: str) -> str:
    for key, icon in _TECH_ICON.items():
        if key in outlook:
            return icon
    return "⚪"


_DIP_DECISION_ICON: dict[str, str] = {
    "強い押し目": "🟢", "押し目候補": "🟡", "保有継続": "⚪",
    "過熱警戒": "🟠", "売り時候補": "🔴", "不明": "❓",
}


_TRIGGER_ICON: dict[str, str] = {
    "dip": "🟢", "sell": "🔴", "demand_index": "🔵", "ai_bubble": "🟠",
    "collapse": "🚨", "decision_change": "🟡", "capex": "🟣", "material": "📰",
}


def _load_active_notifications() -> list[Notification]:
    """通知(prev/curr judgmentを含むため非公開)。private/notifications/を読む。"""
    try:
        from src.notifications.store import load_notifications
        return [n for n in load_notifications() if n.status == "active"]
    except Exception as exc:
        logger.warning("notifications load failed: %s", exc)
        return []


def _section_notifications(notifications: list[Notification]) -> list[str]:
    lines: list[str] = [f"## 🔔 本日の通知 ({len(notifications)}件)", ""]
    if not notifications:
        lines += ["*本日、通知条件を満たす変化はありませんでした*", ""]
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
        lines.append(f"- 理由: {n.change_reason}")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# ⑤投資判断(必須6項目) + ⑥判断変更ログ
# ---------------------------------------------------------------------------

def _build_target_name_map(*dfs: pd.DataFrame | None) -> dict[str, str]:
    names: dict[str, str] = {}
    for df in dfs:
        if df is None or df.empty or "target" not in df.columns:
            continue
        for _, row in df.iterrows():
            target = str(row.get("target", ""))
            if target and target not in names:
                names[target] = str(row.get("name_ja", target))
    return names


def _render_conditions(assessment: ScenarioAssessment) -> list[str]:
    """成立/未成立/観測不能の条件を実際の説明文つきで列挙する(件数だけでなく中身を見せる)。"""
    lines: list[str] = []
    met = [c for c in assessment.conditions if c.met is True]
    if met:
        detail = "、".join(
            f"{c.desc}(実測値={c.measured_value:.3f})" if c.measured_value is not None else c.desc
            for c in met
        )
        lines.append(f"- ⭐成立({len(met)}件): {detail}")
    if assessment.unmet:
        detail = "、".join(
            f"{c.desc}(実測値={c.measured_value:.3f})" if c.measured_value is not None else c.desc
            for c in assessment.unmet
        )
        lines.append(f"- ❌未成立({len(assessment.unmet)}件): {detail}")
    if assessment.unobservable:
        detail = "、".join(
            f"{c.desc}(データなし、data_quality={c.data_quality})"
            for c in assessment.unobservable
        )
        lines.append(f"- ❓観測不能({len(assessment.unobservable)}件): {detail}")
    return lines


def _section_holdings_detail(
    records: list[DecisionRecord],
    theme_scores_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    tech_df: pd.DataFrame,
    ds_df: pd.DataFrame,
    risk_df: pd.DataFrame,
    notifications: list[Notification],
) -> list[str]:
    """保有銘柄1件につき1章で、判断とその根拠(テーマスコア内訳・シグナル・テクニカル・
    リスク・成立条件の中身)を全て集約する。従来はこれらが5つの章に分散していたため、
    「何を根拠にどう判断したか」を追うのに複数表を往復する必要があった(ユーザー指摘により再設計)。
    """
    lines: list[str] = ["## 保有銘柄別 詳細(判断の根拠)", ""]
    if not records:
        lines += ["*private/decisions/ にレコードなし(--step 8 未実行)*", ""]
        return lines

    theme_row_by_theme: dict[str, pd.Series] = (
        {str(row["theme"]): row for _, row in theme_scores_df.iterrows()}
        if not theme_scores_df.empty else {}
    )
    signal_by_target: dict[str, pd.Series] = (
        {str(row["target"]): row for _, row in signals_df.iterrows()}
        if signals_df is not None and not signals_df.empty and "target" in signals_df.columns
        else {}
    )
    tech_by_target: dict[str, pd.Series] = (
        {str(row["target"]): row for _, row in tech_df.iterrows()}
        if tech_df is not None and not tech_df.empty and "target" in tech_df.columns
        else {}
    )
    dipsell_by_target: dict[str, pd.Series] = (
        {str(row["target"]): row for _, row in ds_df.iterrows()}
        if ds_df is not None and not ds_df.empty and "target" in ds_df.columns
        else {}
    )
    risk_by_target: dict[str, list[pd.Series]] = {}
    if risk_df is not None and not risk_df.empty:
        for _, row in risk_df[risk_df["deteriorated"] == True].iterrows():  # noqa: E712
            risk_by_target.setdefault(str(row["target"]), []).append(row)
    notif_by_target: dict[str, list[Notification]] = {}
    for n in notifications:
        if n.target:
            notif_by_target.setdefault(n.target, []).append(n)

    name_map = _build_target_name_map(signals_df, tech_df, ds_df)

    for r in records:
        name = name_map.get(r.target, r.target)
        icon = _SCENARIO_ICON.get(r.active_scenario, "⚪")
        label = _SCENARIO_LABEL.get(r.active_scenario, r.active_scenario)
        lines.append(f"### {name}({r.theme}) — {r.action} {icon}現在地:{label}")
        lines.append("")
        lines.append(f"- 判断理由: {r.reason}")
        lines.append(f"- 変更理由: {r.change_reason or '(前回から変更なし)'}")
        lines.append(f"- Confidence: {_fmt_pct(r.confidence)}")
        lines.append("")

        lines.append("| 根拠項目 | 値 | 意味 |")
        lines.append("|---|---|---|")

        theme_row = theme_row_by_theme.get(r.theme)
        if theme_row is not None:
            breakdown = (
                f"構造{_fmt_axis(theme_row.get('structural_change'))}"
                f"・需給{_fmt_axis(theme_row.get('supply_demand'))}"
                f"・業績{_fmt_axis(theme_row.get('earnings'))}"
                f"・valuation{_fmt_axis(theme_row.get('valuation'))}"
                f"・資金流入{_fmt_axis(theme_row.get('fund_flow'))}"
                f"・政策{_fmt_axis(theme_row.get('policy_tailwind'))}"
            )
            lines.append(
                f"| テーマスコア | {_fmt_axis(theme_row.get('total'))}/100"
                f"(confidence {_fmt_pct(theme_row.get('confidence_pct'))}) | {breakdown} |"
            )
        else:
            lines.append("| テーマスコア | -- | theme_scores.csv なし |")

        signal_row = signal_by_target.get(r.target)
        outlook = str(signal_row.get("outlook", "")) if signal_row is not None else ""
        if signal_row is not None:
            lines.append(
                f"| ポートフォリオシグナル | Hard{_fmt_axis(signal_row.get('hard_score'))} "
                f"/ Extended{_fmt_axis(signal_row.get('extended_score'))}"
                f"(confidence {_fmt_pct(signal_row.get('confidence_pct'))}) "
                f"| {_outlook_icon(outlook)} {outlook} |"
            )
        else:
            lines.append("| ポートフォリオシグナル | -- | portfolio_signal_scores.csv なし |")

        tech_row = tech_by_target.get(r.target)
        if tech_row is not None:
            d25 = (
                f"{float(tech_row.get('ma25_dev')):+.1f}%"
                if pd.notna(tech_row.get("ma25_dev")) else "--"
            )
            d200 = (
                f"{float(tech_row.get('ma200_dev')):+.1f}%"
                if pd.notna(tech_row.get("ma200_dev")) else "--"
            )
            tech_outlook = str(tech_row.get("tech_outlook", "--"))
            lines.append(
                f"| 株価テクニカル | RSI{_fmt_axis(tech_row.get('rsi'))}, "
                f"25MA{d25}, 200MA{d200} | {_tech_icon(tech_outlook)} {tech_outlook} |"
            )
        else:
            lines.append("| 株価テクニカル | -- | technical_scores.csv なし |")

        dip_row = dipsell_by_target.get(r.target)
        dip_decision: str | None = None
        if dip_row is not None:
            dip_decision = str(dip_row.get("decision", "--"))
            lines.append(
                f"| 押し目/売り時判定 | dip{_fmt_axis(dip_row.get('dip_score'))} "
                f"sell{_fmt_axis(dip_row.get('sell_score'))} "
                f"hold{_fmt_axis(dip_row.get('hold_score'))} "
                f"| {_DIP_DECISION_ICON.get(dip_decision, '⚪')} {dip_decision}(暫定版) |"
            )
        else:
            lines.append("| 押し目/売り時判定 | -- | dip_sell_scores.csv なし |")

        risk_items = risk_by_target.get(r.target, [])
        if risk_items:
            evidence = "、".join(
                f"{_RISK_CATEGORY_LABEL.get(str(i.get('category')), str(i.get('category')))}"
                f"(score={_fmt_axis(i.get('risk_score'))}: {i.get('evidence')})"
                for i in risk_items
            )
            lines.append(f"| Layer6リスク | ⚠️検知あり | {evidence} |")
        else:
            lines.append("| Layer6リスク | 検知なし | -- |")
        lines.append("")

        warning = _detect_signal_divergence(outlook, dip_decision)
        if warning:
            lines.append(f"> {warning}")
            lines.append("")

        lines.append("| シナリオ | 成立率 |")
        lines.append("|---|---:|")
        for a in r.scenario_assessments:
            slabel = _SCENARIO_LABEL.get(a.scenario_type, a.scenario_type)
            lines.append(f"| {slabel} | {_fmt_pct(a.fulfillment_rate)} |")
        lines.append("")

        active = next(
            (a for a in r.scenario_assessments if a.scenario_type == r.active_scenario), None
        )
        if active is not None and active.conditions:
            lines.append(f"**現在地シナリオ({label})の判定根拠**")
            lines.append("")
            lines.extend(_render_conditions(active))
            lines.append("")

        target_notifs = notif_by_target.get(r.target, [])
        if target_notifs:
            lines.append("**🔔 本日の関連通知**")
            for n in target_notifs:
                lines.append(f"- {n.change_reason}")
            lines.append("")

    return lines


def _section_change_log(
    records: list[DecisionRecord], prev: list[DecisionRecord] | None,
) -> list[str]:
    lines: list[str] = ["## 判断変更ログ(前回差分)", ""]
    if prev is None:
        lines += ["*前回スナップショットなし(初回実行)*", ""]
        return lines

    changes = diff(prev, records)
    if not changes:
        lines += ["*前回から変化なし*", ""]
        return lines

    lines.append("| 銘柄 | テーマ | 項目 | 前回 | 今回 |")
    lines.append("|---|---|---|---|---|")
    for c in changes:
        lines.append(f"| {c.target} | {c.theme} | {c.field} | {c.prev_value} | {c.curr_value} |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# ⑦予測検証成績(L5)
# ---------------------------------------------------------------------------

def _section_prediction_accuracy(acc_df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## 予測検証成績(Layer5)", ""]
    if acc_df.empty:
        lines += ["*prediction_accuracy.csv なし(--step 7 未実行)*", ""]
        return lines

    row = acc_df.iloc[0]
    hit_rate = row.get("hit_rate")
    avg_excess = row.get("avg_excess_return")
    lines.append(
        f"- 記帳済み予測: {int(row.get('n_predictions', 0))}件 "
        f"/ 評価待ち: {int(row.get('n_pending_evaluations', 0))}件 "
        f"/ 評価済み: {int(row.get('n_evaluated', 0))}件 "
        f"/ データ無し: {int(row.get('n_skipped', 0))}件"
    )
    lines.append(
        f"- 方向的中率: {_fmt_pct(hit_rate) if pd.notna(hit_rate) else '評価data不足'} "
        f"/ 平均超過リターン: "
        f"{f'{float(avg_excess)*100:+.1f}%' if pd.notna(avg_excess) else '評価data不足'}"
    )
    next_due = row.get("next_due_date")
    if pd.notna(next_due):
        lines.append(f"- 次回評価予定日: {next_due}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# ⑧配分提案(L9、未実装) / ⑨発掘ランキング(L7-8、未実装)
# ---------------------------------------------------------------------------

def _section_allocation(allocation_df: pd.DataFrame) -> list[str]:
    lines: list[str] = ["## 配分提案(Layer9)", ""]
    if allocation_df.empty:
        lines += ["*private/allocation.csv なし(--step 11 未実行)*", ""]
        return lines

    lines.append("| テーマ | テーマスコア | 推奨配分 | 現在配分 | 差分 | 根拠 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for _, row in allocation_df.iterrows():
        rec = row.get("recommended_pct")
        cur = row.get("current_pct")
        diff = row.get("diff_pct")
        lines.append(
            f"| {row.get('theme')} | {_fmt_axis(row.get('theme_score'))} "
            f"| {_fmt_axis(rec)}% | {_fmt_axis(cur) + '%' if pd.notna(cur) else '未入力'} "
            f"| {f'{float(diff):+.1f}%' if pd.notna(diff) else '--'} | {row.get('rationale')} |"
        )
    lines.append("")
    lines.append(
        "> 現在配分は private/holdings.csv (config/holdings.example.csv参照)を"
        "手動保守した場合のみ表示される。金額・株数は保持しない(比率のみ)。"
    )
    lines.append("")
    return lines


def _section_discovery(
    companies_df: pd.DataFrame, themes_df: pd.DataFrame,
) -> list[str]:
    lines: list[str] = ["## 発掘ランキング(Layer7-8)", ""]
    lines.append(
        "> 保有銘柄の判断を含まない公開データ(outputs/discovery_companies.csv, "
        "outputs/discovery_themes.csv)。参考として本レポートにも転記する。"
    )
    lines.append("")

    lines.append("### 新規投資候補(非保有銘柄ランキング)")
    lines.append("")
    if companies_df.empty:
        lines += ["*outputs/discovery_companies.csv なし(--step 12 未実行)*", ""]
    else:
        lines.append("| rank | 銘柄 | テーマ | expected_value | 相対モメンタム | 根拠 |")
        lines.append("|---:|---|---|---:|---:|---|")
        for _, row in companies_df.head(10).iterrows():
            rel = row.get("relative_momentum")
            lines.append(
                f"| {row.get('rank')} | {row.get('name_ja', row.get('company'))} "
                f"| {row.get('theme')} | {_fmt_axis(row.get('expected_value'))} "
                f"| {f'{float(rel):+.1f}%' if pd.notna(rel) else '--'} | {row.get('thesis')} |"
            )
        lines.append("")

    lines.append("### 新テーマ候補(status=watch)")
    lines.append("")
    if themes_df.empty:
        lines += ["*outputs/discovery_themes.csv なし(--step 12 未実行、"
                   "またはwatchテーマなし)*", ""]
    else:
        lines.append("| テーマ | 材料トレンド | data_quality |")
        lines.append("|---|---|:---:|")
        for _, row in themes_df.iterrows():
            lines.append(
                f"| {row.get('name_ja', row.get('theme'))} "
                f"| {row.get('materials_trend_note')} | {row.get('data_quality')} |"
            )
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# サマリー(冒頭に配置。詳細な根拠は「保有銘柄別 詳細」章を参照)
# ---------------------------------------------------------------------------

def _section_conclusion(
    records: list[DecisionRecord], notifications: list[Notification] | None = None,
) -> list[str]:
    lines: list[str] = ["## 📋 サマリー", ""]
    if not records:
        lines += ["*判断データなし*", ""]
        return lines

    n_bull = sum(1 for r in records if r.active_scenario == "bull")
    n_bear = sum(1 for r in records if r.active_scenario == "bear")
    n_neutral = len(records) - n_bull - n_bear
    attention = [r for r in records if r.action in ("一部利確", "売却")]

    lines.append(
        f"- 現在地の内訳: 強気{n_bull}銘柄 / 中立{n_neutral}銘柄 / 弱気{n_bear}銘柄"
        f"(全{len(records)}銘柄)"
    )
    if attention:
        names = "、".join(f"{r.target}({r.action})" for r in attention)
        lines.append(f"- ⚠️ 要注意判断: {names}")
    else:
        lines.append("- 一部利確・売却の判断は本日ありません")
    if notifications:
        lines.append(f"- 🔔 本日の通知: {len(notifications)}件(詳細は次章)")
    lines.append("- 各銘柄の判断根拠(テーマスコア内訳・シグナル・テクニカル・リスク・"
                 "成立条件の中身)は「保有銘柄別 詳細」章にまとめて記載")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def generate_decision_report(as_of: date | None = None) -> str:
    """private/decision_report.md を生成してレポート文字列を返す。"""
    d = as_of or date.today()

    records = load_decisions(d, PRIVATE_DECISIONS_DIR)
    prev = load_previous(d, PRIVATE_DECISIONS_DIR)
    theme_scores_df = _load_csv("theme_scores.csv")
    acc_df = _load_csv("prediction_accuracy.csv")
    signals_df = _load_csv("portfolio_signal_scores.csv", PRIVATE_DIR)
    tech_df = _load_csv("technical_scores.csv", PRIVATE_DIR)
    ds_df = _load_csv("dip_sell_scores.csv", PRIVATE_DIR)
    risk_df = _load_csv("risk_scores.csv", PRIVATE_DIR)
    allocation_df = _load_csv("allocation.csv", PRIVATE_DIR)
    discovery_companies_df = _load_csv("discovery_companies.csv")
    discovery_themes_df = _load_csv("discovery_themes.csv")
    notifications = _load_active_notifications()

    lines: list[str] = []
    lines.extend(_section_header(d))
    lines.extend(_section_conclusion(records, notifications))
    lines.extend(_section_notifications(notifications))
    lines.extend(_section_risk(risk_df))
    lines.extend(
        _section_holdings_detail(
            records, theme_scores_df, signals_df, tech_df, ds_df, risk_df, notifications,
        )
    )
    lines.extend(_section_theme_scores(theme_scores_df))
    lines.extend(_section_early_signal(theme_scores_df))
    lines.extend(_section_change_log(records, prev))
    lines.extend(_section_prediction_accuracy(acc_df))
    lines.extend(_section_allocation(allocation_df))
    lines.extend(_section_discovery(discovery_companies_df, discovery_themes_df))

    lines += ["---", f"*生成: {datetime.now().isoformat()} | 非公開レポート、公開禁止*"]

    report = "\n".join(lines)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PRIVATE_DIR / "decision_report.md"
    output_path.write_text(report, encoding="utf-8")
    logger.info("decision_report.md saved (private, %d chars)", len(report))
    return report
