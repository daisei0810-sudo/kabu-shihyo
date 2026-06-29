"""Plotly ダッシュボード生成 + PWA アセット出力。

outputs/index.html    … メインダッシュボード (plotly 埋め込み)
outputs/manifest.json … PWA マニフェスト
outputs/sw.js         … サービスワーカー (キャッシュファースト)

iPhone での使い方:
  Safari で outputs を GitHub Pages で開く → 「ホーム画面に追加」→ PWA として起動
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from src.config import OUTPUTS

logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(OUTPUTS)

# plotly.js CDN (バージョン固定でキャッシュ安定)
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

_OUTLOOK_COLOR: dict[str, str] = {
    "強気": "#00cc66",
    "中立-強気": "#99cc00",
    "中立": "#aaaaaa",
    "中立(要確認)": "#ddaa00",
    "中立-弱気": "#dd7700",
    "弱気": "#cc3333",
    "不明": "#555555",
}


def _outlook_color(outlook: str) -> str:
    for key, col in _OUTLOOK_COLOR.items():
        if key in outlook:
            return col
    return "#555555"


def _load_csv(name: str) -> pd.DataFrame:
    path = OUTPUT_DIR / name
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("load failed: %s: %s", name, exc)
        return pd.DataFrame()


def _fmt(val: object, fmt: str = ".0f") -> str:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f"{f:{fmt}}" if not pd.isna(f) else "--"
    except (TypeError, ValueError):
        return "--"


# ---------------------------------------------------------------------------
# チャート生成
# ---------------------------------------------------------------------------


def _make_portfolio_table(signals_df: pd.DataFrame) -> str:
    """ポートフォリオシグナルテーブル → plotly div 文字列。"""
    if signals_df.empty or "target" not in signals_df.columns:
        return "<p style='color:#888'>シグナルデータなし (Step3 未実行)</p>"
    df = signals_df[~signals_df["target"].str.contains("demand", na=False)].copy()

    if df.empty:
        return "<p style='color:#888'>シグナルデータなし (Step3 未実行)</p>"

    names    = df.get("name_ja",        df["target"]).tolist()
    hards    = [_fmt(v) for v in df.get("hard_score",      pd.Series())]
    exts     = [_fmt(v) for v in df.get("extended_score",  pd.Series())]
    confs    = [f"{float(v)*100:.0f}%" if pd.notna(v) else "--"
                for v in df.get("confidence_pct", pd.Series())]
    outlooks = df.get("outlook", pd.Series(["--"]*len(df))).tolist()
    actions  = df.get("action",  pd.Series(["--"]*len(df))).tolist()

    cell_colors = [[_outlook_color(str(o))] * 6 for o in outlooks]
    # transpose: [col][row]
    col_colors = list(map(list, zip(*cell_colors, strict=False)))

    fig = go.Figure(data=[go.Table(
        header=dict(
            values=["<b>銘柄</b>", "<b>Hard</b>", "<b>Extended</b>",
                    "<b>Confidence</b>", "<b>Outlook</b>", "<b>Action</b>"],
            fill_color="#1a2a3a",
            font=dict(color="white", size=12),
            align="left",
            height=32,
        ),
        cells=dict(
            values=[names, hards, exts, confs, outlooks, actions],
            fill_color=col_colors,
            font=dict(color="white", size=11),
            align=["left", "right", "right", "center", "left", "left"],
            height=28,
        ),
    )])
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=max(200, len(df) * 30 + 50),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="portfolio-table")


def _make_xrp_gauges(signals_df: pd.DataFrame) -> str:
    """XRPロック需要 + 総合実需ゲージ → plotly div 文字列。"""
    if signals_df.empty or "target" not in signals_df.columns:
        return "<p style='color:#888'>XRPスコアデータなし (Step3 未実行)</p>"
    ld_row = signals_df[signals_df["target"] == "xrp_lock_demand"]
    rd_row = signals_df[signals_df["target"] == "xrp_real_demand"]

    def get_score(row: pd.DataFrame) -> float | None:
        if row.empty:
            return None
        v = row.iloc[0].get("hard_score")
        try:
            f = float(v)  # noqa: PGH003
            return None if pd.isna(f) else f
        except (TypeError, ValueError):
            return None

    def get_name(row: pd.DataFrame, default: str) -> str:
        if row.empty:
            return default
        return str(row.iloc[0].get("name_ja", default))

    ld_score = get_score(ld_row)
    rd_score = get_score(rd_row)
    ld_name  = get_name(ld_row, "XRPロック需要")
    rd_name  = get_name(rd_row, "XRP総合実需")

    fig = go.Figure()

    for _i, (score, name, col, xdom) in enumerate([
        (ld_score, ld_name, "#7eb3ff", [0, 0.48]),
        (rd_score, rd_name, "#7fffb0", [0.52, 1.0]),
    ]):
        fig.add_trace(go.Indicator(
            mode="gauge+number",
            value=score if score is not None else 0,
            title={"text": name, "font": {"size": 13}},
            number={"suffix": "/100", "font": {"size": 20}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": "#aaa"},
                "bar": {"color": col, "thickness": 0.25},
                "bgcolor": "#1a2a3a",
                "bordercolor": "#334",
                "steps": [
                    {"range": [0,  30], "color": "#111827"},
                    {"range": [30, 50], "color": "#1a2035"},
                    {"range": [50, 70], "color": "#203040"},
                    {"range": [70, 90], "color": "#2a3f52"},
                    {"range": [90, 100], "color": "#344f64"},
                ],
                "threshold": {
                    "line": {"color": "gold", "width": 3},
                    "value": score if score is not None else 0,
                },
            },
            domain={"x": xdom, "y": [0, 1]},
        ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e0e0e0"},
        height=240,
        margin=dict(l=16, r=16, t=48, b=8),
    )

    # 段階ラベル注記
    if not ld_row.empty:
        note = str(ld_row.iloc[0].get("name_ja", ""))
        fig.add_annotation(
            text=note, x=0.24, y=-0.05, xref="paper", yref="paper",
            showarrow=False, font=dict(size=10, color="#aaa"),
        )

    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="xrp-gauges")


def _make_scorecard_table(sc_df: pd.DataFrame) -> str:
    """指標スコアカードテーブル → plotly div 文字列。"""
    if sc_df.empty:
        return "<p style='color:#888'>indicator_scorecard.csv なし (Step2 未実行)</p>"

    _RANK_COLOR = {
        "A+": "#00cc66", "A": "#66cc00", "B": "#cccc00",
        "C": "#cc6600", "D": "#666666",
    }

    ranks     = sc_df.get("rank", pd.Series()).tolist()
    inds      = sc_df.get("indicator", pd.Series()).tolist()
    targets   = sc_df.get("target",    pd.Series()).tolist()
    statcorrs = [_fmt(v, ".3f") for v in sc_df.get("spearman_r_stationary", pd.Series())]
    lvcorrs   = [_fmt(v, ".3f") for v in sc_df.get("spearman_r_level",      pd.Series())]
    hitrates  = [f"{float(v)*100:.0f}%" if pd.notna(v) else "--"
                 for v in sc_df.get("hit_rate", pd.Series())]
    eff_ns    = [_fmt(v, ".1f") for v in sc_df.get("effective_n", pd.Series())]
    notes     = [str(v)[:50] for v in sc_df.get("confidence_note", pd.Series())]

    rank_colors = [[_RANK_COLOR.get(str(r), "#333")] for r in ranks]
    col_colors = list(
        map(list, zip(*([rank_colors[i]] * 8 for i in range(len(ranks))), strict=False))
    )

    fig = go.Figure(data=[go.Table(
        header=dict(
            values=["<b>ランク</b>", "<b>指標</b>", "<b>対象</b>",
                    "<b>変化相関</b>", "<b>レベル相関</b>",
                    "<b>的中率</b>", "<b>実効N</b>", "<b>コメント</b>"],
            fill_color="#1a2a3a",
            font=dict(color="white", size=11),
            align="left",
            height=30,
        ),
        cells=dict(
            values=[ranks, inds, targets, statcorrs, lvcorrs, hitrates, eff_ns, notes],
            fill_color=col_colors,
            font=dict(color="white", size=10),
            align=["center", "left", "left", "right", "right", "right", "right", "left"],
            height=24,
        ),
    )])
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=max(200, len(sc_df) * 26 + 50),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id="scorecard-table")


# ---------------------------------------------------------------------------
# PWA アセット
# ---------------------------------------------------------------------------

_MANIFEST_TEMPLATE = {
    "name": "先行指標監視ダッシュボード",
    "short_name": "株指標",
    "description": "ニュース化前の初動を検知する先行指標監視システム",
    "start_url": "./",
    "display": "standalone",
    "theme_color": "#0e1117",
    "background_color": "#0e1117",
    "lang": "ja",
    "icons": [
        {
            "src": (
                "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
                " viewBox='0 0 100 100'>"
                "<rect width='100' height='100' rx='20' fill='%230e1117'/>"
                "<text y='72' font-size='72' text-anchor='middle' x='50'>📊</text></svg>"
            ),
            "sizes": "any",
            "type": "image/svg+xml",
        },
    ],
}

_SW_JS = """\
const CACHE = 'kabu-v1';
const ASSETS = ['./', './index.html', './manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});
"""

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="株指標">
  <meta name="theme-color" content="#0e1117">
  <title>先行指標監視ダッシュボード</title>
  <link rel="manifest" href="./manifest.json">
  <script src="{plotly_cdn}"></script>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0e1117;color:#e0e0e0;font-family:system-ui,-apple-system,sans-serif;
          padding:8px 12px;max-width:1200px;margin:0 auto}}
    h1{{color:#7eb3ff;font-size:1.1rem;margin:8px 0 4px}}
    h2{{color:#b8d4ff;font-size:0.95rem;border-bottom:1px solid #2a3a4a;
        margin:16px 0 8px;padding-bottom:4px}}
    .meta{{color:#666;font-size:0.75rem;margin:2px 0 8px}}
    .warn{{color:#ffa500;font-size:0.8rem;padding:6px 10px;
           background:#2a1a00;border-radius:4px;margin:8px 0}}
    .section{{margin:0 0 16px}}
    .badges{{color:#888;font-size:0.75rem;line-height:1.8}}
  </style>
</head>
<body>
<h1>📊 先行指標監視ダッシュボード</h1>
<div class="meta">生成: {timestamp} | Hard/Extended スコア 0–100</div>
<div class="warn">
  ⚠️ 現在の有効指標はすべて C/D ランク（履歴不足）。スコアは参考値。
  データ蓄積により精度が向上します。
</div>

<div class="section">
  <h2>ポートフォリオ シグナル</h2>
  {portfolio_div}
</div>

<div class="section">
  <h2>XRP 需要スコア</h2>
  {xrp_div}
</div>

<div class="section">
  <h2>指標スコアカード</h2>
  {scorecard_div}
</div>

<div class="section">
  <div class="badges">
    🟢 verified = Hard/Extended 算入 &nbsp;|&nbsp;
    🟡 proxy = Extended のみ &nbsp;|&nbsp;
    🟠 estimated = Extended のみ &nbsp;|&nbsp;
    ⚪ unavailable = 取得不可・スコア非算入
  </div>
</div>

<script>
  if ('serviceWorker' in navigator) {{
    window.addEventListener('load', function() {{
      navigator.serviceWorker.register('./sw.js').catch(function() {{}});
    }});
  }}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def build_dashboard() -> None:
    """ダッシュボード HTML と PWA アセットを outputs/ に保存。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    signals_df = _load_csv("portfolio_signal_scores.csv")
    sc_df      = _load_csv("indicator_scorecard.csv")

    portfolio_div = _make_portfolio_table(signals_df)
    xrp_div       = _make_xrp_gauges(signals_df)
    scorecard_div = _make_scorecard_table(sc_df)

    html = _HTML_TEMPLATE.format(
        plotly_cdn=PLOTLY_CDN,
        timestamp=now.strftime("%Y-%m-%d %H:%M"),
        portfolio_div=portfolio_div,
        xrp_div=xrp_div,
        scorecard_div=scorecard_div,
    )

    # index.html (GitHub Pages のルートとして配信)
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    logger.info("index.html saved (%d bytes)", len(html))

    # manifest.json
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(_MANIFEST_TEMPLATE, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("manifest.json saved")

    # sw.js
    (OUTPUT_DIR / "sw.js").write_text(_SW_JS, encoding="utf-8")
    logger.info("sw.js saved")
