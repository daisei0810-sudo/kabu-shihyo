"""Layer4 Scoring Engine — テーマ(サイクル)を6軸ルーブリックで採点する。

配点: 構造変化30 / 需給25 / 業績20 / バリュエーション10 / 資金流入10 / 政策追い風5。

各軸を`components.ComponentScore`として作り、軸自体を
`components.aggregate_components()`(重み=配点)へ渡すことで、既存の
「加重平均+confidence伝播」パターンをそのままテーマ合成にも適用する
(1軸の内部集計 → 6軸の合成、の2段構成)。

現時点でデータが揃っていない軸(バリュエーション=PER/PSR時系列未整備、
政策追い風=materials の related_tickers 紐付け未整備 等)は score=None の
まま正直に返す。confidenceが自然に下がることで「弱い/未整備」を可視化する
(既存方針: 推測でスコアを断定しない)。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, INSTRUMENTS, MATERIALS_DB, MATERIALS_DUMP_DIR, OUTPUTS, Layer
from src.scoring.capex_trend import capex_trend_score
from src.scoring.components import ComponentScore, aggregate_components
from src.scoring.cycle_scores import CycleScore, compute_cycle_scores
from src.scoring.normalizer import score_from_series
from src.scoring.score_history import compute_all_changes
from src.scoring.xrp_scores import XrpDemandResult, compute_xrp_lock_demand, compute_xrp_real_demand

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)
OUTPUT_DIR = Path(OUTPUTS)
STRUCTURAL_SCORES_CSV = Path("config/structural_scores.csv")

# テーマ → 対応する cycle_scores.py のキー(compute_cycle_scores()参照)。
# crypto_xrpはxrp_scores.py側の実需/ロック需要スコアを使うため対象外(別処理)。
# ev_physical_ai/china_ai/policy/power/bioは対応するサイクルスコアが未定義
# (無料データで有効な代理指標を作れないことが既に判明している領域、config.py参照)。
THEME_CYCLE_KEYS: dict[str, list[str]] = {
    Layer.AI_DATACENTER.value: ["ai_cycle", "optical"],
    Layer.SEMICAP.value: ["cowos", "hbm"],
    Layer.ROBOTICS_FA.value: ["robotics"],
    Layer.QUANTUM.value: ["quantum"],
}

# テーマ → 四半期CAPEX parquet(stem, column)。四半期データはcapex_trend_score専用。
THEME_CAPEX_STEMS: dict[str, list[tuple[str, str]]] = {
    Layer.AI_DATACENTER.value: [
        ("capex_hyperscaler_total", "hyperscaler_capex_total"),
        ("capex_nvda", "capex"),
    ],
    Layer.SEMICAP.value: [("capex_tsm", "capex")],
}

# 政策追い風(estimated)の判定に使うキーワード(§8政策トピックの簡易分類)。
# related_tickers紐付けが未整備の現状ではヒットしないことが多いが、
# 材料取込パイプラインの改善で自動的に効き始める設計。
POLICY_KEYWORDS: tuple[str, ...] = (
    "補助金", "規制", "輸出規制", "税制", "政策", "助成", "認可", "関税", "許可",
)
POLICY_LOOKBACK_DAYS = 180


@dataclass
class ThemeScoreResult:
    """1テーマの6軸スコア結果。"""

    theme: str
    name_ja: str
    as_of: str
    structural_change: ComponentScore   # 配点30
    supply_demand: ComponentScore       # 配点25
    earnings: ComponentScore            # 配点20
    valuation: ComponentScore           # 配点10
    fund_flow: ComponentScore           # 配点10
    policy_tailwind: ComponentScore     # 配点5
    total: float | None                 # 0-100 (配点通りの加重合成)
    confidence_pct: float
    data_coverage_pct: float
    change_1d: float | None = None
    change_1w: float | None = None
    change_1m: float | None = None
    note: str = ""


# ---------------------------------------------------------------------------
# 軸1: 構造変化 (config/structural_scores.csv の手動評価。§8確定事項のハイブリッド
#      運用のうち、materials由来の自動加減点は材料データが十分蓄積してから追加する)
# ---------------------------------------------------------------------------

def _load_structural_scores(path: Path = STRUCTURAL_SCORES_CSV) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("structural_scores.csv load failed: %s", exc)
        return pd.DataFrame()


def _structural_change_axis(theme: str, manual_df: pd.DataFrame) -> ComponentScore:
    row = manual_df[manual_df["theme"] == theme] if not manual_df.empty else pd.DataFrame()
    if row.empty:
        return ComponentScore(
            name="構造変化", score=None, weight=30.0, available=False,
            data_quality="unavailable",
            note="config/structural_scores.csv に手動評価が未登録(月次でユーザーが0-30点を入力)",
        )
    raw = float(row.iloc[0]["score"])
    updated_at = str(row.iloc[0].get("updated_at", ""))
    return ComponentScore(
        name="構造変化", score=round(raw / 30.0 * 100.0, 1), weight=30.0, available=True,
        data_quality="estimated",
        note=f"手動評価(config/structural_scores.csv, 更新日={updated_at})",
    )


# ---------------------------------------------------------------------------
# 軸2: 需給 (cycle_scores / XRP実需・ロック需要スコアの合成)
# ---------------------------------------------------------------------------

def _supply_demand_axis(
    theme: str,
    cycle_by_key: dict[str, CycleScore],
    xrp_real: XrpDemandResult | None,
    xrp_lock: XrpDemandResult | None,
) -> ComponentScore:
    if theme == Layer.CRYPTO_XRP.value:
        subs = []
        if xrp_real is not None:
            subs.append(ComponentScore(
                "XRP実需スコア", xrp_real.score, max(xrp_real.confidence_pct, 0.05),
                xrp_real.score is not None, "proxy", xrp_real.note,
            ))
        if xrp_lock is not None:
            subs.append(ComponentScore(
                "XRPロック需要スコア", xrp_lock.score, max(xrp_lock.confidence_pct, 0.05),
                xrp_lock.score is not None, "proxy", xrp_lock.note,
            ))
        if not subs:
            return ComponentScore("需給", None, 25.0, False, "unavailable", "XRP需給スコア未算出")
        agg = aggregate_components(subs, "需給(XRP)")
        return ComponentScore(
            "需給", agg.score, 25.0, agg.score is not None, "proxy",
            f"XRP実需/ロック需要の合成 conf={agg.confidence_pct:.0%}",
        )

    keys = THEME_CYCLE_KEYS.get(theme, [])
    subs = []
    for k in keys:
        cs = cycle_by_key.get(k)
        if cs is None:
            continue
        subs.append(ComponentScore(
            cs.name_ja, cs.score, max(cs.confidence_pct, 0.05),
            cs.score is not None, "proxy", cs.note,
        ))
    if not subs:
        return ComponentScore(
            "需給", None, 25.0, False, "unavailable", "対応するサイクルスコアなし",
        )
    agg = aggregate_components(subs, "需給")
    return ComponentScore(
        "需給", agg.score, 25.0, agg.score is not None, "proxy",
        f"サイクルスコア{len(subs)}種の合成 conf={agg.confidence_pct:.0%}",
    )


# ---------------------------------------------------------------------------
# 軸3: 業績 (四半期CAPEXのYoYトレンド)
# ---------------------------------------------------------------------------

def _earnings_axis(theme: str, processed_dir: Path = PROCESSED_DIR) -> ComponentScore:
    stems = THEME_CAPEX_STEMS.get(theme, [])
    subs: list[ComponentScore] = []
    for stem, column in stems:
        path = processed_dir / f"{stem}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            s, note = capex_trend_score(df[column])
            subs.append(ComponentScore(stem, s, 1.0, s is not None, "verified", note))
        except Exception as exc:
            logger.warning("capex load failed stem=%s: %s", stem, exc)
            subs.append(ComponentScore(stem, None, 1.0, False, "verified", f"読込失敗: {exc}"))
    if not subs:
        return ComponentScore("業績", None, 20.0, False, "unavailable", "四半期CAPEXデータなし")
    agg = aggregate_components(subs, "業績")
    return ComponentScore(
        "業績", agg.score, 20.0, agg.score is not None, "verified",
        f"CAPEX YoYトレンド{len(subs)}系列の合成",
    )


# ---------------------------------------------------------------------------
# 軸4: バリュエーション (PER/PSR時系列は未整備、次フェーズでdata_sources拡張)
# ---------------------------------------------------------------------------

def _valuation_axis() -> ComponentScore:
    return ComponentScore(
        "バリュエーション", None, 10.0, False, "unavailable",
        "PER/PSR時系列データ未整備(yfinance四半期売上等の新規フェッチが必要、P3以降で対応)",
    )


# ---------------------------------------------------------------------------
# 軸5: 資金流入 (出来高トレンド。ETF flowsは既存indicators.csv同様unavailable)
# ---------------------------------------------------------------------------

def _fund_flow_axis(theme: str, processed_dir: Path = PROCESSED_DIR) -> ComponentScore:
    instruments = [i for i in INSTRUMENTS if i.layer.value == theme and i.held and i.ticker]
    subs: list[ComponentScore] = []
    for inst in instruments:
        path = processed_dir / f"price_{inst.key}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            if "Volume" not in df.columns:
                continue
            vol = df["Volume"].dropna()
            if vol.empty:
                continue
            s, note = score_from_series(vol, float(vol.iloc[-1]))
            subs.append(ComponentScore(
                f"{inst.name_ja}出来高", s, 1.0, s is not None, "verified", note,
            ))
        except Exception as exc:
            logger.warning("volume load failed key=%s: %s", inst.key, exc)
    subs.append(ComponentScore(
        "ETF資金流入", None, 0.3, False, "unavailable", "日次クリーンな無料APIなし",
    ))
    if not any(c.available for c in subs):
        return ComponentScore("資金流入", None, 10.0, False, "unavailable", "出来高データなし")
    agg = aggregate_components(subs, "資金流入")
    n_avail = sum(1 for c in subs if c.available)
    return ComponentScore(
        "資金流入", agg.score, 10.0, agg.score is not None, "verified",
        f"出来高トレンド{n_avail}銘柄の合成、ETF flowsは取得不可",
    )


# ---------------------------------------------------------------------------
# 軸6: 政策追い風 (materialsの政策キーワード一致件数、estimated)
# ---------------------------------------------------------------------------

def load_materials_conn(
    db_path: str = MATERIALS_DB, dump_dir: str = MATERIALS_DUMP_DIR
) -> sqlite3.Connection | None:
    """materials.db をJSONL正本から再構築して接続を返す。データなしならNone。"""
    if not Path(dump_dir).exists():
        return None
    try:
        from src.materials.db import get_connection, rebuild_from_jsonl
        conn = get_connection(db_path)
        rebuild_from_jsonl(conn, dump_dir)
        return conn
    except Exception as exc:
        logger.warning("materials db load failed: %s", exc)
        return None


def _policy_tailwind_axis(
    theme: str, materials_conn: sqlite3.Connection | None, today: date | None = None,
) -> ComponentScore:
    instruments = [i for i in INSTRUMENTS if i.layer.value == theme]
    tickers = {i.key for i in instruments}
    if materials_conn is None or not tickers:
        return ComponentScore(
            "政策追い風", None, 5.0, False, "unavailable", "対象銘柄またはmaterials DB接続なし",
        )

    from src.materials.db import list_materials

    d = today or date.today()
    cutoff = d - timedelta(days=POLICY_LOOKBACK_DAYS)

    seen: dict[str, object] = {}
    for key in tickers:
        for m in list_materials(materials_conn, key):
            seen[m.material_id] = m

    def _in_window(published_at: str | None) -> bool:
        if not published_at:
            return True  # 発表日不明は除外しない(取りこぼし回避)
        try:
            return datetime.fromisoformat(published_at.split("T")[0]).date() >= cutoff
        except ValueError:
            return True

    matched = [
        m for m in seen.values()
        if _in_window(m.published_at)  # type: ignore[attr-defined]
        and any(kw in (m.title + m.summary) for kw in POLICY_KEYWORDS)  # type: ignore[attr-defined]
    ]
    if not matched:
        return ComponentScore(
            "政策追い風", None, 5.0, False, "unavailable",
            "政策関連材料なし(related_tickers紐付けが未整備の可能性あり)",
        )
    count = len(matched)
    score = min(count, 5) / 5.0 * 100.0
    return ComponentScore(
        "政策追い風", round(score, 1), 5.0, True, "estimated",
        f"政策キーワード一致材料{count}件({POLICY_LOOKBACK_DAYS}日以内、キーワード頻度からの推定)",
    )


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------

def compute_theme_score(
    theme: str,
    name_ja: str,
    manual_df: pd.DataFrame,
    cycle_by_key: dict[str, CycleScore],
    xrp_real: XrpDemandResult | None,
    xrp_lock: XrpDemandResult | None,
    materials_conn: sqlite3.Connection | None,
    as_of: date | None = None,
    processed_dir: Path = PROCESSED_DIR,
) -> ThemeScoreResult:
    """1テーマの6軸スコアを計算する。"""
    d = as_of or date.today()

    structural = _structural_change_axis(theme, manual_df)
    supply_demand = _supply_demand_axis(theme, cycle_by_key, xrp_real, xrp_lock)
    earnings = _earnings_axis(theme, processed_dir)
    valuation = _valuation_axis()
    fund_flow = _fund_flow_axis(theme, processed_dir)
    policy = _policy_tailwind_axis(theme, materials_conn, d)

    axes = [structural, supply_demand, earnings, valuation, fund_flow, policy]
    agg = aggregate_components(axes, theme)

    label = f"theme_{theme}"
    changes = compute_all_changes(label, agg.score) if agg.score is not None else {
        "change_1d": None, "change_1w": None, "change_1m": None,
    }

    return ThemeScoreResult(
        theme=theme,
        name_ja=name_ja,
        as_of=d.isoformat(),
        structural_change=structural,
        supply_demand=supply_demand,
        earnings=earnings,
        valuation=valuation,
        fund_flow=fund_flow,
        policy_tailwind=policy,
        total=agg.score,
        confidence_pct=agg.confidence_pct,
        data_coverage_pct=agg.data_coverage_pct,
        change_1d=changes["change_1d"],
        change_1w=changes["change_1w"],
        change_1m=changes["change_1m"],
        note=agg.note,
    )


def compute_all_theme_scores(as_of: date | None = None) -> list[ThemeScoreResult]:
    """themes.csv の全テーマについて6軸スコアを計算する。"""
    from src.registry.themes import load_themes

    manual_df = _load_structural_scores()
    cycle_by_key = {cs.key: cs for cs in compute_cycle_scores()}
    try:
        xrp_real = compute_xrp_real_demand()
    except Exception as exc:
        logger.warning("xrp_real_demand failed: %s", exc)
        xrp_real = None
    try:
        xrp_lock = compute_xrp_lock_demand()
    except Exception as exc:
        logger.warning("xrp_lock_demand failed: %s", exc)
        xrp_lock = None
    materials_conn = load_materials_conn()

    results = [
        compute_theme_score(
            t.key, t.name_ja, manual_df, cycle_by_key, xrp_real, xrp_lock,
            materials_conn, as_of,
        )
        for t in load_themes()
    ]

    if materials_conn is not None:
        materials_conn.close()
    return results


def save_theme_scores_csv(results: list[ThemeScoreResult]) -> None:
    """outputs/theme_scores.csv へ保存する。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        rows.append({
            "theme": r.theme,
            "name_ja": r.name_ja,
            "as_of": r.as_of,
            "structural_change": r.structural_change.score,
            "supply_demand": r.supply_demand.score,
            "earnings": r.earnings.score,
            "valuation": r.valuation.score,
            "fund_flow": r.fund_flow.score,
            "policy_tailwind": r.policy_tailwind.score,
            "total": r.total,
            "confidence_pct": r.confidence_pct,
            "data_coverage_pct": r.data_coverage_pct,
            "change_1d": r.change_1d,
            "change_1w": r.change_1w,
            "change_1m": r.change_1m,
            "note": r.note,
        })
    path = OUTPUT_DIR / "theme_scores.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("saved: %s (%d rows)", path, len(rows))
