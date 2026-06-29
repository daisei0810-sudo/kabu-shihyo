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

    signals_df = _load_csv("portfolio_signal_scores.csv")
    sc_df      = _load_csv("indicator_scorecard.csv")

    if signals_df is not None and not signals_df.empty:
        lines.extend(_section_portfolio(signals_df))
        lines.extend(_section_xrp(signals_df))
    else:
        lines += ["*portfolio_signal_scores.csv なし (Step3 未実行)*", ""]

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
