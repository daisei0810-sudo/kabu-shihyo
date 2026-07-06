"""Layer6リスクカテゴリ別検知器(統一シグネチャ detect(theme, target, ctx) -> RiskItem)。

真に計算可能なもの(capex_cut/competition_loss)は実データで判定する。
materials依存のもの(regulation/dilution/customer_churn)は、現状
materials.related_tickers紐付けが未整備(theme_score.py §4.5と同じ制約)のため
実質unavailableになりやすいが、機構自体は実装しておく(材料取込パイプラインの
改善で自動的に効き始める設計)。tech_defeatは無料データで competition_loss と
明確に異なる指標を作れないため、恒常的unavailableの正直なプレースホルダーとする。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, Layer
from src.indicator_loader import OPTICAL_PEERS, QUANTUM_PEERS, ROBOTICS_PEERS, peer_basket_excluding
from src.risk.models import RiskItem
from src.risk.taxonomy import (
    CUSTOMER_CHURN_KEYWORDS,
    DILUTION_KEYWORDS,
    MATERIALS_LOOKBACK_DAYS,
    MOMENTUM_DETERIORATION_THRESHOLD,
    MOMENTUM_MA_WINDOW,
    REGULATION_KEYWORDS,
    RiskCategory,
)

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)

# テーマ → 対応するピアバスケット(indicator_loader.pyの既存定義を再利用)。
# semicap/crypto_xrp/ev_physical_ai/china_ai/policy/power/bioは多銘柄ピア
# バスケットが未定義のためcompetition_loss算出不可(unavailable)。
THEME_PEER_BASKETS: dict[str, list[str]] = {
    Layer.AI_DATACENTER.value: OPTICAL_PEERS,
    Layer.ROBOTICS_FA.value: ROBOTICS_PEERS,
    Layer.QUANTUM.value: QUANTUM_PEERS,
}


def _load_close(key: str, processed_dir: Path) -> pd.Series | None:
    path = processed_dir / f"price_{key}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        if "Close" not in df.columns:
            return None
        s = df["Close"].dropna()
        return s if len(s) > 0 else None
    except Exception as exc:
        logger.warning("price load failed key=%s: %s", key, exc)
        return None


def _ma_deviation(series: pd.Series, window: int) -> float | None:
    clean = series.dropna()
    if len(clean) < window:
        return None
    ma = clean.rolling(window).mean()
    c, m = float(clean.iloc[-1]), float(ma.iloc[-1])
    if pd.isna(c) or pd.isna(m) or m == 0:
        return None
    return (c - m) / m * 100


def _score_from_deviation(dev: float, threshold: float) -> float:
    """乖離率を0-100リスクスコアへ線形マップ(閾値到達で50、その2倍悪化で100)。"""
    if dev >= 0:
        return 0.0
    ratio = dev / threshold  # threshold=負値、devも負値 → 悪化するほど大きい正の比
    return float(min(100.0, max(0.0, ratio * 50.0)))


def detect_capex_cut(
    theme: str, target: str, as_of: date, processed_dir: Path = PROCESSED_DIR,
) -> RiskItem:
    """ハイパースケーラーCAPEXの減速(既存collapse_watch._check_hyperscaler_capexの移設)。

    ai_datacenterテーマのみ対象(他テーマは対応するCAPEXデータソースが無いため
    unavailable)。
    """
    if theme != Layer.AI_DATACENTER.value:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.CAPEX_CUT.value,
            risk_score=None, deteriorated=None,
            evidence="このテーマに対応するCAPEXデータソースなし", data_quality="unavailable",
            as_of=as_of.isoformat(),
        )

    path = processed_dir / "capex_hyperscaler_total.parquet"
    if not path.exists():
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.CAPEX_CUT.value,
            risk_score=None, deteriorated=None,
            evidence="データなし", data_quality="unavailable", as_of=as_of.isoformat(),
        )
    try:
        df = pd.read_parquet(path)
        s = df["hyperscaler_capex_total"].dropna()
    except Exception as exc:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.CAPEX_CUT.value,
            risk_score=None, deteriorated=None,
            evidence=f"読込失敗: {exc}", data_quality="unavailable", as_of=as_of.isoformat(),
        )
    if len(s) < 2:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.CAPEX_CUT.value,
            risk_score=None, deteriorated=None,
            evidence="四半期データ不足", data_quality="unavailable", as_of=as_of.isoformat(),
        )
    prev, curr = float(s.iloc[-2]), float(s.iloc[-1])
    if prev == 0:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.CAPEX_CUT.value,
            risk_score=None, deteriorated=None,
            evidence="前期値が0", data_quality="unavailable", as_of=as_of.isoformat(),
        )
    qoq = (curr - prev) / abs(prev)
    deteriorated = qoq < 0
    risk_score = float(min(100.0, max(0.0, -qoq * 200.0))) if deteriorated else 0.0
    return RiskItem(
        theme=theme, target=target, category=RiskCategory.CAPEX_CUT.value,
        risk_score=risk_score, deteriorated=deteriorated,
        evidence=f"ハイパースケーラーCAPEX QoQ={qoq:+.0%}(マイナスで悪化)",
        data_quality="verified", as_of=as_of.isoformat(),
    )


def detect_competition_loss(
    theme: str, target: str, as_of: date, processed_dir: Path = PROCESSED_DIR,
) -> RiskItem:
    """ピアバスケット比の相対モメンタム劣化(peer_basket_excluding再利用)。"""
    peers = THEME_PEER_BASKETS.get(theme)
    target_series = _load_close(target, processed_dir)
    if not peers or target_series is None:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.COMPETITION_LOSS.value,
            risk_score=None, deteriorated=None,
            evidence="対応するピアバスケットまたは自社価格データなし",
            data_quality="unavailable", as_of=as_of.isoformat(),
        )

    basket = peer_basket_excluding(target, peers, processed_dir)
    if basket is None:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.COMPETITION_LOSS.value,
            risk_score=None, deteriorated=None,
            evidence="ピア構成銘柄データなし", data_quality="unavailable",
            as_of=as_of.isoformat(),
        )

    target_norm = target_series / float(target_series.iloc[0])
    relative = (target_norm / basket).dropna()
    dev = _ma_deviation(relative, MOMENTUM_MA_WINDOW)
    if dev is None:
        return RiskItem(
            theme=theme, target=target, category=RiskCategory.COMPETITION_LOSS.value,
            risk_score=None, deteriorated=None,
            evidence=f"データ不足({MOMENTUM_MA_WINDOW}日分未満)", data_quality="proxy",
            as_of=as_of.isoformat(),
        )
    deteriorated = dev < MOMENTUM_DETERIORATION_THRESHOLD
    risk_score = _score_from_deviation(dev, MOMENTUM_DETERIORATION_THRESHOLD)
    return RiskItem(
        theme=theme, target=target, category=RiskCategory.COMPETITION_LOSS.value,
        risk_score=risk_score, deteriorated=deteriorated,
        evidence=(
            f"対ピア相対モメンタム(25日MA乖離)={dev:+.1f}%"
            f"(閾値{MOMENTUM_DETERIORATION_THRESHOLD:+.0f}%)"
        ),
        data_quality="proxy", as_of=as_of.isoformat(),
    )


def detect_tech_defeat(theme: str, target: str, as_of: date) -> RiskItem:
    """技術的敗北の直接指標は無料データでは作れない(competition_lossと重複するため
    別軸の判定を作らず、恒常的unavailableとして正直に明示する)。
    """
    return RiskItem(
        theme=theme, target=target, category=RiskCategory.TECH_DEFEAT.value,
        risk_score=None, deteriorated=None,
        evidence="無料データで競合技術との直接比較指標を作れないため未実装"
                  "(競合ピア相対パフォーマンスはcompetition_lossを参照)",
        data_quality="unavailable", as_of=as_of.isoformat(),
    )


def _materials_keyword_risk(
    theme: str,
    target: str,
    category: RiskCategory,
    materials_conn: sqlite3.Connection | None,
    keywords: tuple[str, ...],
    as_of: date,
    require_source_rank_ab: bool = False,
) -> RiskItem:
    """materialsのキーワード一致件数からestimatedリスクスコアを算出する共通ロジック。"""
    if materials_conn is None:
        return RiskItem(
            theme=theme, target=target, category=category.value,
            risk_score=None, deteriorated=None,
            evidence="materials DB接続なし", data_quality="unavailable",
            as_of=as_of.isoformat(),
        )

    from src.materials.db import list_materials

    cutoff = as_of - timedelta(days=MATERIALS_LOOKBACK_DAYS)

    def _in_window(published_at: str | None) -> bool:
        if not published_at:
            return True
        try:
            return datetime.fromisoformat(published_at.split("T")[0]).date() >= cutoff
        except ValueError:
            return True

    materials = list_materials(materials_conn, target)
    if require_source_rank_ab:
        materials = [m for m in materials if m.source_rank in ("A", "B")]

    matched = [
        m for m in materials
        if _in_window(m.published_at) and any(kw in (m.title + m.summary) for kw in keywords)
    ]
    if not matched:
        return RiskItem(
            theme=theme, target=target, category=category.value,
            risk_score=None, deteriorated=None,
            evidence="該当材料なし(related_tickers紐付けが未整備の可能性あり)",
            data_quality="unavailable", as_of=as_of.isoformat(),
        )

    count = len(matched)
    risk_score = min(count, 5) / 5.0 * 100.0
    return RiskItem(
        theme=theme, target=target, category=category.value,
        risk_score=round(risk_score, 1), deteriorated=True,
        evidence=f"キーワード一致材料{count}件({MATERIALS_LOOKBACK_DAYS}日以内)",
        data_quality="estimated", as_of=as_of.isoformat(),
    )


def detect_regulation(
    theme: str, target: str, materials_conn: sqlite3.Connection | None, as_of: date,
) -> RiskItem:
    """規制・制裁リスク。source_rank A/Bのみ判断に使用(既存can_affect_decision方針を踏襲)。"""
    return _materials_keyword_risk(
        theme, target, RiskCategory.REGULATION, materials_conn, REGULATION_KEYWORDS, as_of,
        require_source_rank_ab=True,
    )


def detect_dilution(
    theme: str, target: str, materials_conn: sqlite3.Connection | None, as_of: date,
) -> RiskItem:
    """増資・新株予約権等による希薄化リスク。"""
    return _materials_keyword_risk(
        theme, target, RiskCategory.DILUTION, materials_conn, DILUTION_KEYWORDS, as_of,
    )


def detect_customer_churn(
    theme: str, target: str, materials_conn: sqlite3.Connection | None, as_of: date,
) -> RiskItem:
    """主要顧客の離脱・契約解除リスク。"""
    return _materials_keyword_risk(
        theme, target, RiskCategory.CUSTOMER_CHURN, materials_conn, CUSTOMER_CHURN_KEYWORDS, as_of,
    )
