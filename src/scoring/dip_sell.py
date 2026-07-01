"""押し目・売り時判定エンジン(簡易版) — Phase8 暫定実装。

指示書「AI・半導体・量子・ロボティクス投資監視システム 最終統合指示書」§4 Phase4 の
本実装は材料データ(§6 material_id・受注残変化・ガイダンス修正等、Phase6完了後)を
前提とするため、この簡易版は既存データ(Hard/Extendedスコア + テクニカル指標
RSI/MA乖離)のみで dip_score / sell_score / hold_score を近似する。

位置づけ・制約:
  - ガイダンス修正・受注残変化・HBM ASP・CoWoSリードタイム等の材料イベントは未反映。
  - Phase6(ニュース・材料監視)完了後、材料データを組み込んだ本実装に置き換える前提。
  - すべての結果に provisional=True を付与し、暫定値であることを明示する。
  - confidence_pct が低い銘柄はスコアを保守化する(上限キャップ)。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.scoring.engine import AssetScore
from src.scoring.technicals import TechnicalResult

# テクニカルアウトルック → 基礎 dip_score / sell_score (0-100)
_DIP_BASE: dict[str, float] = {
    "強い押し目候補": 90.0,
    "押し目候補": 65.0,
    "中立": 40.0,
    "過熱警戒": 15.0,
    "強い過熱警戒": 5.0,
}
_SELL_BASE: dict[str, float] = {
    "強い過熱警戒": 90.0,
    "過熱警戒": 65.0,
    "中立": 30.0,
    "押し目候補": 10.0,
    "強い押し目候補": 5.0,
}

CONFIDENCE_CAP_THRESHOLD = 0.30  # これ未満は保守化
CONFIDENCE_CAP_VALUE = 60.0


@dataclass
class DipSellResult:
    """1銘柄の押し目・売り時判定(簡易版)。"""

    target: str
    name_ja: str
    dip_score: float | None
    sell_score: float | None
    hold_score: float | None
    decision: str              # 強い押し目/押し目候補/保有継続/過熱警戒/売り時候補/不明
    recommended_action: str
    reason: str
    provisional: bool = True   # Phase6(材料監視)完了までは常にTrue


def calculate_dip_score(tech: TechnicalResult, asset: AssetScore | None) -> float | None:
    """テクニカル + Hard/Extendedスコアから押し目スコア(0-100)を算出。"""
    base = _DIP_BASE.get(tech.tech_outlook)
    if base is None:
        return None
    score = base
    if asset is not None and asset.extended_score is not None:
        if asset.extended_score < 30:
            score -= 20  # ファンダ自体が弱い可能性 → 単純な押し目とは言えない
        elif asset.extended_score >= 70:
            score += 5   # ファンダ良好な状態での株価調整は押し目としての信頼度が上がる
    if asset is not None and asset.confidence_pct < CONFIDENCE_CAP_THRESHOLD:
        score = min(score, CONFIDENCE_CAP_VALUE)
    return max(0.0, min(100.0, round(score, 1)))


def calculate_sell_score(tech: TechnicalResult, asset: AssetScore | None) -> float | None:
    """テクニカル + Hard/Extendedスコアから売り時スコア(0-100)を算出。"""
    base = _SELL_BASE.get(tech.tech_outlook)
    if base is None:
        return None
    score = base
    if asset is not None and asset.extended_score is not None and asset.extended_score >= 80:
        score += 10  # 既に高水準でかつ過熱 → 利確検討シグナルを強化
    if asset is not None and asset.confidence_pct < CONFIDENCE_CAP_THRESHOLD:
        score = min(score, CONFIDENCE_CAP_VALUE)
    return max(0.0, min(100.0, round(score, 1)))


def calculate_hold_score(dip: float | None, sell: float | None) -> float | None:
    """dip_score・sell_score の残余として保有継続の妥当性を近似(0-100)。"""
    if dip is None and sell is None:
        return None
    d = dip or 0.0
    s = sell or 0.0
    return max(0.0, round(100.0 - max(d, s), 1))


def classify_ticker_action(
    dip: float | None, sell: float | None
) -> tuple[str, str, str]:
    """dip_score/sell_score → (判定, 推奨アクション, 理由)。"""
    if dip is None or sell is None:
        return "不明", "要確認", "テクニカルデータ不足"
    if dip >= 75:
        return "強い押し目", "分割買い増し候補", f"dip_score={dip:.0f}(暫定・簡易判定)"
    if dip >= 55:
        return "押し目候補", "押し目買い検討", f"dip_score={dip:.0f}(暫定・簡易判定)"
    if sell >= 75:
        return "売り時候補", "一部利確検討", f"sell_score={sell:.0f}(暫定・簡易判定)"
    if sell >= 55:
        return "過熱警戒", "新規買い見送り", f"sell_score={sell:.0f}(暫定・簡易判定)"
    return "保有継続", "様子見", f"dip={dip:.0f} / sell={sell:.0f}(中立域・暫定)"


def compute_dip_sell(
    technicals: list[TechnicalResult],
    asset_scores: dict[str, AssetScore],
) -> list[DipSellResult]:
    """保有銘柄全体の押し目・売り時判定(簡易版)を計算して返す。"""
    results: list[DipSellResult] = []
    for t in technicals:
        asset = asset_scores.get(t.target)
        dip = calculate_dip_score(t, asset)
        sell = calculate_sell_score(t, asset)
        hold = calculate_hold_score(dip, sell)
        decision, action, reason = classify_ticker_action(dip, sell)
        results.append(DipSellResult(
            target=t.target,
            name_ja=t.name_ja,
            dip_score=dip,
            sell_score=sell,
            hold_score=hold,
            decision=decision,
            recommended_action=action,
            reason=reason,
        ))
    return results
