"""Layer7 New Investment Discovery — 非保有銘柄を既存スコアリングでランキングする。

ユニバースは `instruments.csv` の `held=False` 銘柄(既にテーマ別に15銘柄程度が
登録済み)。個社ごとの期待リターンモデルは持たないため、`expected_value` は
現状 theme_score(6軸ルーブリックの合成値)をそのまま採用する — 個社の
ファンダメンタルズ差を表す指標ではなく「その企業が属するテーマの地合い」の
代理指標であることを明示する(推測で個社の期待値を捏造しない、という既存方針)。

差別化要素として「テーマ内相対モメンタム」(自分の価格騰落率 − 同テーマ全銘柄
平均の価格騰落率)を別カラムで併記し、theme_score が同じテーマ内での優劣は
モメンタムで判断できるようにする。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED, OUTPUTS, PRICE_PROXY, Instrument, load_instruments

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(DATA_PROCESSED)
OUTPUT_DIR = Path(OUTPUTS)
MOMENTUM_LOOKBACK_DAYS = 65  # 約3ヶ月営業日


@dataclass
class DiscoveryCompany:
    """非保有銘柄1件のランキング結果。"""

    company: str
    name_ja: str
    theme: str
    thesis: str
    expected_value: float | None    # 現状はtheme_scoreをそのまま採用(個社期待値モデル未整備)
    relative_momentum: float | None  # テーマ内相対モメンタム(%, 自分 − テーマ平均)
    risks: str
    current_position: str
    confidence_pct: float
    data_quality: str
    rank: int
    as_of: str


def _load_close(key: str, processed_dir: Path = PROCESSED_DIR) -> pd.Series | None:
    resolved = PRICE_PROXY.get(key, key)
    path = processed_dir / f"price_{resolved}.parquet"
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


def _momentum_pct(close: pd.Series, lookback: int = MOMENTUM_LOOKBACK_DAYS) -> float | None:
    """lookback営業日前比の騰落率(%)。データ不足時はNone。"""
    n = len(close)
    lb = min(lookback, n - 1)
    if lb < 20:
        return None
    prev = float(close.iloc[-(lb + 1)])
    curr = float(close.iloc[-1])
    if prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0


def _compute_theme_momentum(
    instruments: list[Instrument], processed_dir: Path = PROCESSED_DIR,
) -> tuple[dict[str, float], dict[str, float]]:
    """全銘柄(保有+非保有)の騰落率と、テーマ別平均騰落率を返す。

    テーマ内比較の母集団は保有/非保有を問わない(「その企業が業界内で相対的に
    強いか」を見たいのであって、ユーザーの保有状況とは無関係のため)。
    """
    momentum_by_key: dict[str, float] = {}
    by_theme: dict[str, list[float]] = {}
    for inst in instruments:
        close = _load_close(inst.key, processed_dir)
        if close is None:
            continue
        m = _momentum_pct(close)
        if m is None:
            continue
        momentum_by_key[inst.key] = m
        by_theme.setdefault(inst.layer.value, []).append(m)
    theme_avg = {theme: sum(vals) / len(vals) for theme, vals in by_theme.items() if vals}
    return momentum_by_key, theme_avg


def _load_csv(name: str, base_dir: Path = OUTPUT_DIR) -> pd.DataFrame:
    path = base_dir / name
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        logger.warning("load failed: %s: %s", name, exc)
        return pd.DataFrame()


def compute_discovery_companies(
    as_of: date | None = None,
    processed_dir: Path = PROCESSED_DIR,
    output_dir: Path = OUTPUT_DIR,
    instruments: list[Instrument] | None = None,
) -> list[DiscoveryCompany]:
    """非保有銘柄をテーマスコア×テーマ内相対モメンタムでランキングする。"""
    d = as_of or date.today()
    instruments = instruments if instruments is not None else load_instruments()
    candidates = [i for i in instruments if not i.held]

    momentum_by_key, theme_avg_momentum = _compute_theme_momentum(instruments, processed_dir)

    theme_scores_df = _load_csv("theme_scores.csv", output_dir)
    theme_row_by_theme: dict[str, pd.Series] = (
        {str(row["theme"]): row for _, row in theme_scores_df.iterrows()}
        if not theme_scores_df.empty else {}
    )
    risk_df = _load_csv("risk_level_by_theme.csv", output_dir)
    risk_row_by_theme: dict[str, pd.Series] = (
        {str(row["theme"]): row for _, row in risk_df.iterrows()}
        if not risk_df.empty else {}
    )

    results: list[DiscoveryCompany] = []
    for inst in candidates:
        theme = inst.layer.value
        theme_row = theme_row_by_theme.get(theme)
        total = (
            float(theme_row["total"]) if theme_row is not None and pd.notna(theme_row.get("total"))
            else None
        )
        conf = (
            float(theme_row["confidence_pct"])
            if theme_row is not None and pd.notna(theme_row.get("confidence_pct")) else 0.0
        )

        own_momentum = momentum_by_key.get(inst.key)
        peer_avg = theme_avg_momentum.get(theme)
        rel_momentum = (
            own_momentum - peer_avg if own_momentum is not None and peer_avg is not None else None
        )

        risk_row = risk_row_by_theme.get(theme)
        risk_level = int(risk_row["risk_level"]) if risk_row is not None else None

        thesis_parts: list[str] = []
        thesis_parts.append(
            f"テーマスコア{total:.0f}(confidence {conf:.0%})" if total is not None
            else "テーマスコア未算出"
        )
        thesis_parts.append(
            f"テーマ内相対モメンタム{rel_momentum:+.1f}%(直近{MOMENTUM_LOOKBACK_DAYS}営業日)"
            if rel_momentum is not None else "モメンタムデータ不足"
        )
        if risk_level is not None:
            thesis_parts.append(f"テーマrisk_level={risk_level}/3")

        results.append(DiscoveryCompany(
            company=inst.key,
            name_ja=inst.name_ja,
            theme=theme,
            thesis="、".join(thesis_parts),
            expected_value=total,
            relative_momentum=rel_momentum,
            risks=(
                f"テーマrisk_level={risk_level}/3(risk_level_by_theme.csv参照)"
                if risk_level is not None else "risk_level未算出(--step 10未実行)"
            ),
            current_position="非保有",
            confidence_pct=conf,
            data_quality="estimated" if total is not None else "unavailable",
            rank=0,
            as_of=d.isoformat(),
        ))

    # ランキング: expected_value(theme_score)降順、同点はrelative_momentum降順。
    # Noneは常に最下位(存在しない値を0扱いで有利にしない)。
    results.sort(
        key=lambda r: (
            r.expected_value is None,
            -(r.expected_value or 0.0),
            r.relative_momentum is None,
            -(r.relative_momentum or 0.0),
        )
    )
    for i, r in enumerate(results, start=1):
        r.rank = i

    return results
