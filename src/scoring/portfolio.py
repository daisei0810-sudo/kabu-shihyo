"""ポートフォリオ集計 + 投資判断マッピング。

methodology.md §5:
  スコア水準 + 変化速度(モメンタム) + confidence の3軸で判定。
  confidence が低い (< 0.30) シグナルは判定を1段保守化する。

投資判断マッピング:
  score ≥ 70 + conf ≥ 0.30 + 上昇モメンタム → 強気 / 追加
  score ≥ 70 + conf < 0.30                   → 中立(要確認) / 保有継続
  score ≥ 50                                 → 中立 / 保有継続
  score 30–50                                → 中立-弱気 / 利確検討
  score < 30 + 下落モメンタム                → 弱気 / 撤退候補
  score < 30                                 → 弱気 / 利確検討
  score = None                               → 不明 / 要確認
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import OUTPUTS, Layer, held_instruments
from src.scoring.collapse_watch import CollapseWatchResult, compute_collapse_watch
from src.scoring.cycle_scores import CycleScore, compute_cycle_scores
from src.scoring.demand_index import (
    DemandIndexResult,
    compute_ai_bubble_score,
    compute_divergence,
    compute_real_demand_index,
)
from src.scoring.dip_sell import DipSellResult, compute_dip_sell
from src.scoring.engine import AssetScore, ScoreEngine
from src.scoring.score_history import append_snapshot, compute_all_changes
from src.scoring.technicals import MacroResult, TechnicalResult, compute_macro, compute_technicals
from src.scoring.xrp_scores import XrpDemandResult, compute_xrp_lock_demand, compute_xrp_real_demand

logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(OUTPUTS)


@dataclass
class InvestmentSignal:
    """1銘柄の投資シグナル。"""

    target: str
    name_ja: str
    layer: str
    hard_score: float | None
    extended_score: float | None
    confidence_pct: float
    data_coverage_pct: float
    outlook: str       # 強気 / 中立 / 弱気 / 不明
    action: str        # 追加 / 保有継続 / 利確検討 / 撤退候補 / 要確認
    signal_note: str   # 判断根拠
    n_indicators: int


@dataclass
class PortfolioResult:
    """ポートフォリオ全体の集計結果。"""

    signals: list[InvestmentSignal] = field(default_factory=list)
    xrp_real_demand: XrpDemandResult | None = None
    xrp_lock_demand: XrpDemandResult | None = None
    portfolio_hard_avg: float | None = None
    portfolio_extended_avg: float | None = None
    technicals: list[TechnicalResult] = field(default_factory=list)
    macro: MacroResult | None = None
    dip_sell: list[DipSellResult] = field(default_factory=list)
    real_demand_index: DemandIndexResult | None = None
    ai_bubble_score: DemandIndexResult | None = None
    bubble_demand_divergence: float | None = None
    cycle_scores: list[CycleScore] = field(default_factory=list)
    collapse_watch: CollapseWatchResult | None = None


def _map_decision(
    score: float | None,
    confidence: float,
    momentum: float | None = None,
) -> tuple[str, str, str]:
    """(score, confidence, momentum) → (outlook, action, note)。

    momentum > 0 = 上昇、< 0 = 下落、None = 不明。
    confidence < 0.30 のとき1段保守化。
    """
    if score is None:
        return "不明", "要確認", "スコア算出不可(データ不足またはStep2未実行)"

    low_conf = confidence < 0.30
    rising = momentum is not None and momentum > 0
    falling = momentum is not None and momentum < 0

    if score >= 70:
        if low_conf:
            return (
                "中立(要確認)",
                "保有継続",
                f"スコア高({score:.0f})だが信頼度低({confidence:.0%}) → 1段保守化",
            )
        if rising:
            return (
                "強気",
                "追加",
                f"スコア高({score:.0f})・上昇モメンタム・confidence={confidence:.0%}",
            )
        return (
            "中立-強気",
            "保有継続(監視)",
            f"スコア高({score:.0f})・モメンタム不明 → 継続監視",
        )

    if score >= 50:
        if low_conf:
            return (
                "中立(要確認)",
                "保有継続",
                f"スコア中({score:.0f})・信頼度低({confidence:.0%})",
            )
        return (
            "中立",
            "保有継続",
            f"スコア中({score:.0f})・confidence={confidence:.0%}",
        )

    if score >= 30:
        return "中立-弱気", "利確検討", f"スコア低({score:.0f})・要警戒"

    if falling:
        return "弱気", "撤退候補", f"スコア低({score:.0f})・下落モメンタム"

    return "弱気", "利確検討", f"スコア低({score:.0f})"


class PortfolioScorer:
    """保有銘柄ポートフォリオ全体のスコアと投資判断を集計。"""

    def __init__(self, scorecard_path: str | None = None) -> None:
        self.engine = ScoreEngine(scorecard_path=scorecard_path)

    def run(self) -> PortfolioResult:
        """全保有銘柄のスコアを計算してポートフォリオ結果を返す。"""
        result = PortfolioResult()
        held = held_instruments()
        hard_scores: list[float] = []
        ext_scores: list[float] = []
        asset_scores: dict[str, AssetScore] = {}

        for inst in held:
            if inst.key == "xrp":
                continue  # XRP は専用スコアで別処理

            try:
                asset_score = self.engine.compute(inst.key)
                asset_scores[inst.key] = asset_score
                signal = self._to_signal(asset_score, inst.name_ja, inst.layer.value)
                result.signals.append(signal)
                if asset_score.hard_score is not None:
                    hard_scores.append(asset_score.hard_score)
                if asset_score.extended_score is not None:
                    ext_scores.append(asset_score.extended_score)
            except Exception as exc:
                logger.warning("score failed for %s: %s", inst.key, exc)
                result.signals.append(InvestmentSignal(
                    target=inst.key,
                    name_ja=inst.name_ja,
                    layer=inst.layer.value,
                    hard_score=None,
                    extended_score=None,
                    confidence_pct=0.0,
                    data_coverage_pct=0.0,
                    outlook="不明",
                    action="要確認",
                    signal_note=f"計算エラー: {exc}",
                    n_indicators=0,
                ))

        # --- XRP 専用スコア ---
        try:
            result.xrp_real_demand = compute_xrp_real_demand()
            result.xrp_lock_demand = compute_xrp_lock_demand()

            ld = result.xrp_lock_demand
            rd = result.xrp_real_demand
            outlook, action, note = _map_decision(ld.score, ld.confidence_pct)
            stage_label = f"ロック需要ステージ: {ld.stage}" if ld.stage else ""

            result.signals.append(InvestmentSignal(
                target="xrp",
                name_ja="XRP",
                layer=Layer.CRYPTO_XRP.value,
                hard_score=rd.score if rd is not None else None,
                extended_score=ld.score,
                confidence_pct=ld.confidence_pct,
                data_coverage_pct=ld.data_coverage_pct,
                outlook=outlook,
                action=action,
                signal_note=f"{stage_label} | {note}" if stage_label else note,
                n_indicators=sum(1 for c in ld.components if c.available),
            ))
            if ld.score is not None:
                ext_scores.append(ld.score)
        except Exception as exc:
            logger.warning("XRP score failed: %s", exc)

        # --- ポートフォリオ集計 ---
        result.portfolio_hard_avg = (
            round(sum(hard_scores) / len(hard_scores), 1) if hard_scores else None
        )
        result.portfolio_extended_avg = (
            round(sum(ext_scores) / len(ext_scores), 1) if ext_scores else None
        )

        # --- テクニカル指標 + マクロ ---
        try:
            result.technicals = compute_technicals()
        except Exception as exc:
            logger.warning("technicals failed: %s", exc)

        try:
            result.macro = compute_macro()
        except Exception as exc:
            logger.warning("macro failed: %s", exc)

        # --- 押し目・売り時判定(簡易版・Phase8暫定) ---
        try:
            result.dip_sell = compute_dip_sell(result.technicals, asset_scores)
        except Exception as exc:
            logger.warning("dip_sell failed: %s", exc)

        # --- 実需指数 + AIバブルスコア + 乖離(Phase7) ---
        try:
            result.real_demand_index = compute_real_demand_index()
            result.ai_bubble_score = compute_ai_bubble_score()
            result.bubble_demand_divergence = compute_divergence(
                result.real_demand_index, result.ai_bubble_score
            )
        except Exception as exc:
            logger.warning("demand_index/ai_bubble failed: %s", exc)

        # --- サイクルスコア群(Phase7) ---
        try:
            result.cycle_scores = compute_cycle_scores()
        except Exception as exc:
            logger.warning("cycle_scores failed: %s", exc)

        # --- AIサイクル崩壊先行警戒(§11, Phase7) ---
        try:
            result.collapse_watch = compute_collapse_watch()
        except Exception as exc:
            logger.warning("collapse_watch failed: %s", exc)

        return result

    def _to_signal(
        self, asset_score: AssetScore, name_ja: str, layer: str
    ) -> InvestmentSignal:
        score = (
            asset_score.extended_score
            if asset_score.extended_score is not None
            else asset_score.hard_score
        )
        outlook, action, note = _map_decision(score, asset_score.confidence_pct)

        return InvestmentSignal(
            target=asset_score.target,
            name_ja=name_ja,
            layer=layer,
            hard_score=asset_score.hard_score,
            extended_score=asset_score.extended_score,
            confidence_pct=asset_score.confidence_pct,
            data_coverage_pct=asset_score.data_coverage_pct,
            outlook=outlook,
            action=action,
            signal_note=note,
            n_indicators=asset_score.n_extended_indicators,
        )

    def save_csv(self, result: PortfolioResult) -> None:
        """ポートフォリオ結果を outputs/portfolio_signal_scores.csv に保存。"""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        rows = []

        for s in result.signals:
            rows.append({
                "target": s.target,
                "name_ja": s.name_ja,
                "layer": s.layer,
                "hard_score": s.hard_score,
                "extended_score": s.extended_score,
                "confidence_pct": s.confidence_pct,
                "data_coverage_pct": s.data_coverage_pct,
                "outlook": s.outlook,
                "action": s.action,
                "signal_note": s.signal_note,
                "n_indicators": s.n_indicators,
            })

        for label, demand_result in [
            ("xrp_real_demand", result.xrp_real_demand),
            ("xrp_lock_demand", result.xrp_lock_demand),
        ]:
            if demand_result is None:
                continue
            dr = demand_result
            name = (
                f"XRPロック需要スコア({dr.stage})"
                if label == "xrp_lock_demand" and dr.stage
                else "XRP総合実需スコア"
            )
            rows.append({
                "target": label,
                "name_ja": name,
                "layer": "crypto_xrp",
                "hard_score": dr.score,
                "extended_score": dr.score,
                "confidence_pct": dr.confidence_pct,
                "data_coverage_pct": dr.data_coverage_pct,
                "outlook": "",
                "action": "",
                "signal_note": dr.note,
                "n_indicators": sum(1 for c in dr.components if c.available),
            })

        df = pd.DataFrame(rows)
        path = OUTPUT_DIR / "portfolio_signal_scores.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("saved: %s (%d rows)", path, len(df))
        logger.info(
            "Portfolio avg — Hard: %s / Extended: %s",
            result.portfolio_hard_avg,
            result.portfolio_extended_avg,
        )

        # --- テクニカルスコア CSV ---
        if result.technicals:
            tech_rows = [
                {
                    "target": t.target,
                    "name_ja": t.name_ja,
                    "rsi": t.rsi,
                    "ma25_dev": t.ma25_dev,
                    "ma75_dev": t.ma75_dev,
                    "ma200_dev": t.ma200_dev,
                    "close": t.close,
                    "tech_outlook": t.tech_outlook,
                    "tech_note": t.tech_note,
                }
                for t in result.technicals
            ]
            tech_path = OUTPUT_DIR / "technical_scores.csv"
            pd.DataFrame(tech_rows).to_csv(tech_path, index=False, encoding="utf-8-sig")
            logger.info("saved: %s (%d rows)", tech_path, len(tech_rows))

        # --- 押し目・売り時判定 CSV (簡易版・Phase8暫定) ---
        if result.dip_sell:
            ds_rows = [
                {
                    "target": d.target,
                    "name_ja": d.name_ja,
                    "dip_score": d.dip_score,
                    "sell_score": d.sell_score,
                    "hold_score": d.hold_score,
                    "decision": d.decision,
                    "recommended_action": d.recommended_action,
                    "reason": d.reason,
                    "provisional": d.provisional,
                }
                for d in result.dip_sell
            ]
            ds_path = OUTPUT_DIR / "dip_sell_scores.csv"
            pd.DataFrame(ds_rows).to_csv(ds_path, index=False, encoding="utf-8-sig")
            logger.info("saved: %s (%d rows)", ds_path, len(ds_rows))

        # --- マクロ指標 CSV ---
        if result.macro:
            m = result.macro
            macro_path = OUTPUT_DIR / "macro_indicators.csv"
            pd.DataFrame([{
                "vix": m.vix,
                "vix_label": m.vix_label,
                "usdjpy": m.usdjpy,
                "usdjpy_trend": m.usdjpy_trend,
                "us10y": m.us10y,
                "us10y_trend": m.us10y_trend,
                "generated_at": datetime.now().isoformat(),
            }]).to_csv(macro_path, index=False, encoding="utf-8-sig")
            logger.info("saved: %s", macro_path)

        # --- 実需指数 + AIバブルスコア + 乖離 CSV(Phase7) ---
        if result.real_demand_index is not None or result.ai_bubble_score is not None:
            demand_rows = []
            for di in (result.real_demand_index, result.ai_bubble_score):
                if di is None:
                    continue
                append_snapshot(di.label, di.score, di.confidence_pct)
                changes = compute_all_changes(di.label, di.score)
                demand_rows.append({
                    "label": di.label,
                    "score": di.score,
                    "confidence_pct": di.confidence_pct,
                    "data_coverage_pct": di.data_coverage_pct,
                    "change_1d": changes["change_1d"],
                    "change_1w": changes["change_1w"],
                    "change_1m": changes["change_1m"],
                    "note": di.note,
                })
            demand_path = OUTPUT_DIR / "demand_index_scores.csv"
            pd.DataFrame(demand_rows).to_csv(demand_path, index=False, encoding="utf-8-sig")
            logger.info("saved: %s (%d rows)", demand_path, len(demand_rows))

            comp_rows = []
            for di in (result.real_demand_index, result.ai_bubble_score):
                if di is None:
                    continue
                for c in di.components:
                    comp_rows.append({
                        "label": di.label,
                        "component": c.name,
                        "score": c.score,
                        "weight": c.weight,
                        "available": c.available,
                        "data_quality": c.data_quality,
                        "note": c.note,
                    })
            if comp_rows:
                comp_path = OUTPUT_DIR / "demand_index_components.csv"
                pd.DataFrame(comp_rows).to_csv(comp_path, index=False, encoding="utf-8-sig")
                logger.info("saved: %s (%d rows)", comp_path, len(comp_rows))

            if result.bubble_demand_divergence is not None:
                logger.info(
                    "実需指数=%s AIバブルスコア=%s 乖離=%+.1f",
                    result.real_demand_index.score if result.real_demand_index else None,
                    result.ai_bubble_score.score if result.ai_bubble_score else None,
                    result.bubble_demand_divergence,
                )

        # --- サイクルスコア CSV(Phase7) ---
        if result.cycle_scores:
            cycle_rows = []
            for cs in result.cycle_scores:
                append_snapshot(f"cycle_{cs.key}", cs.score, cs.confidence_pct)
                changes = compute_all_changes(f"cycle_{cs.key}", cs.score)
                cycle_rows.append({
                    "key": cs.key,
                    "name_ja": cs.name_ja,
                    "score": cs.score,
                    "confidence_pct": cs.confidence_pct,
                    "n_constituents": cs.n_constituents,
                    "n_available": cs.n_available,
                    "reference_only": cs.reference_only,
                    "change_1d": changes["change_1d"],
                    "change_1w": changes["change_1w"],
                    "change_1m": changes["change_1m"],
                    "note": cs.note,
                })
            cycle_path = OUTPUT_DIR / "cycle_scores.csv"
            pd.DataFrame(cycle_rows).to_csv(cycle_path, index=False, encoding="utf-8-sig")
            logger.info("saved: %s (%d rows)", cycle_path, len(cycle_rows))

        # --- AIサイクル崩壊先行警戒 CSV(§11, Phase7) ---
        if result.collapse_watch is not None:
            cw = result.collapse_watch
            watch_rows = [
                {
                    "name": item.name,
                    "deteriorated": item.deteriorated,
                    "value_note": item.value_note,
                    "available": item.available,
                }
                for item in cw.items
            ]
            watch_path = OUTPUT_DIR / "collapse_watch.csv"
            pd.DataFrame(watch_rows).to_csv(watch_path, index=False, encoding="utf-8-sig")
            logger.info(
                "saved: %s | LEVEL%d (%d/%d項目悪化)",
                watch_path, cw.level, cw.n_deteriorated, cw.n_monitorable,
            )
            # 崩壊警戒LEVELの日次履歴(§17条件6「前回からLEVEL上昇」の検知に
            # notifications パッケージが利用する。scoring側はnotificationsを
            # importしない一方通行の依存だが、既存の score_history 機構を
            # そのまま呼ぶだけなので scoring の責務の範囲内。
            append_snapshot("collapse_level", float(cw.level), None)
