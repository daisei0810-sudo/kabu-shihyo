"""config/indicators.csv から config/scenarios/<theme>.yaml を機械的に生成する。

indicator_scorecard.csv(2026-07-04時点)を見ると、全指標が統計検証でC/Dランク
(実効独立サンプル<10のため未実証)。この状態で"+30%以上"のような具体的な閾値を
手で作ると、統計的裏付けの無い投資ルールを捏造することになる
(methodology.md/config.pyが徹底している「推測で断定しない」方針に反する)。

そのため、各指標の勢いZスコア(dz、features/engineer.pyが既に定義する
「21日変化率のZスコア」)の符号のみを機械的な条件として使う:
  bull: dz > 0    (勢いが平均より強い)
  bear: dz < 0    (勢いが平均より弱い)
  neutral: |dz| < 0.5  (強い方向感なし)

weightは各指標のdata_quality由来confidence_weight(config.DEFAULT_CONFIDENCE_WEIGHT)
をそのまま流用する(verified=1.0, proxy=0.5, estimated=0.3)。

再生成方法:
  cd "C:\\Users\\daisei\\株指標ツール"
  python config/scenarios/generate.py

これは統計的に検証されたシナリオ条件ではなく、指標の勢いを機械的に可視化する
「叩き台」である。indicators.csvの検証ランクがA/Bへ改善したら、その指標を
中心に人手で閾値を調整することを想定している。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import yaml  # noqa: E402

from src.config import DEFAULT_CONFIDENCE_WEIGHT, INDICATORS, DataQuality  # noqa: E402

OUT_DIR = Path(__file__).parent
NEUTRAL_BAND = 0.5


def _condition(ind_key: str, scenario: str, op: str, threshold: float, weight: float) -> dict:
    return {
        "id": f"{ind_key}_dz_{scenario}",
        "desc": f"{ind_key} の勢い(21日変化率Zスコア)が{op}{threshold}",
        "indicator": ind_key,
        "feature": "dz",
        "op": op,
        "threshold": threshold,
        "weight": round(weight, 2),
    }


# Layer6(risk)のカテゴリはテーマによって構造的に計算可能かどうかが決まる
# (src/risk/detectors.pyのTHEME_PEER_BASKETS/capex_cut対象テーマと一致させる)。
# regulation/dilution/customer_churnはmaterials依存で理論上どのテーマでも
# 機構自体は動くため全テーマ共通(現状データが薄くunavailableになりやすいが
# 正直にそのまま条件として残す)。risk_score>=60を「悪化」とみなすbear専用条件。
UNIVERSAL_RISK_CATEGORIES: list[tuple[str, float]] = [
    ("regulation", 0.3), ("dilution", 0.3), ("customer_churn", 0.3),
]
THEME_SPECIFIC_RISK_CATEGORIES: dict[str, list[tuple[str, float]]] = {
    "ai_datacenter": [("capex_cut", 1.0), ("competition_loss", 0.5)],
    "robotics_fa": [("competition_loss", 0.5)],
    "quantum": [("competition_loss", 0.5)],
}
RISK_BEAR_THRESHOLD = 60.0


def _risk_condition(category: str, weight: float) -> dict:
    return {
        "id": f"risk_{category}_bear",
        "desc": f"Layer6リスクカテゴリ「{category}」のスコアが{RISK_BEAR_THRESHOLD:.0f}以上(悪化)",
        "indicator": f"risk:{category}",
        "feature": "level",
        "op": ">=",
        "threshold": RISK_BEAR_THRESHOLD,
        "weight": round(weight, 2),
    }


def main() -> None:
    by_theme: dict[str, list] = {}
    for ind in INDICATORS:
        if ind.data_quality == DataQuality.UNAVAILABLE:
            continue  # 恒常的に観測不能な指標は条件に含めない(常にunobservableになるだけ)
        by_theme.setdefault(ind.layer.value, []).append(ind)

    n_written = 0
    for theme, inds in sorted(by_theme.items()):
        bull, neutral, bear = [], [], []
        for ind in inds:
            w = DEFAULT_CONFIDENCE_WEIGHT[ind.data_quality]
            bull.append(_condition(ind.key, "bull", ">", 0.0, w))
            neutral.append(_condition(ind.key, "neutral", "abs_lt", NEUTRAL_BAND, w))
            bear.append(_condition(ind.key, "bear", "<", 0.0, w))

        # Layer6リスクカテゴリはbearシナリオのみに追加(design§4.7: risk悪化=弱気材料)。
        risk_categories = UNIVERSAL_RISK_CATEGORIES + THEME_SPECIFIC_RISK_CATEGORIES.get(theme, [])
        for category, weight in risk_categories:
            bear.append(_risk_condition(category, weight))

        doc = {
            "theme": theme,
            "_generated_by": "config/scenarios/generate.py (dz符号ベースの機械生成、"
                              "統計未実証の暫定シナリオ)",
            "scenarios": {
                "bull": {"conditions": bull},
                "neutral": {"conditions": neutral},
                "bear": {"conditions": bear},
            },
        }
        path = OUT_DIR / f"{theme}.yaml"
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        n_written += 1
        print(f"wrote {path} ({len(inds)} indicators)")

    print(f"done: {n_written} theme scenario files")


if __name__ == "__main__":
    main()
