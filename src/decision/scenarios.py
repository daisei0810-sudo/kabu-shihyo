"""config/scenarios/<theme>.yaml の読み込み。

判定ルールをコードから分離する(docs/investment_os_design.md §4.3)。
現状の内容は `config/scenarios/generate.py` が indicators.csv から機械的に
生成したもの(dz特徴量の符号ベース)。全指標が統計検証でC/D ランク
(未実証)のため、恣意的な閾値("+30%"等)は手で作らず、既存の
FeatureEngineer が既に定義するZスコア系特徴量の符号のみを使う。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.decision.models import ConditionDef, Scenario, ThemeScenarios

SCENARIOS_DIR = Path("config/scenarios")


def _parse_conditions(raw: list[dict]) -> list[ConditionDef]:
    return [
        ConditionDef(
            condition_id=c["id"],
            desc=c.get("desc", c["id"]),
            indicator=c["indicator"],
            feature=c.get("feature", "level"),
            op=c["op"],
            threshold=float(c["threshold"]),
            weight=float(c.get("weight", 1.0)),
        )
        for c in raw
    ]


def load_scenarios(theme: str, scenarios_dir: Path = SCENARIOS_DIR) -> ThemeScenarios | None:
    """テーマのシナリオ定義を読み込む。ファイルが無ければNone(シナリオ未整備)。"""
    path = scenarios_dir / f"{theme}.yaml"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw or "scenarios" not in raw:
        return None

    scenarios = raw["scenarios"]
    return ThemeScenarios(
        theme=raw.get("theme", theme),
        bull=Scenario("bull", _parse_conditions(scenarios.get("bull", {}).get("conditions", []))),
        neutral=Scenario(
            "neutral", _parse_conditions(scenarios.get("neutral", {}).get("conditions", []))
        ),
        bear=Scenario("bear", _parse_conditions(scenarios.get("bear", {}).get("conditions", []))),
    )


def available_scenario_themes(scenarios_dir: Path = SCENARIOS_DIR) -> list[str]:
    """シナリオ定義ファイルが存在するテーマキーの一覧。"""
    if not scenarios_dir.exists():
        return []
    return sorted(p.stem for p in scenarios_dir.glob("*.yaml"))
