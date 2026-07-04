"""Layer2意思決定エンジンのデータクラス。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["新規買い", "追加買い", "保有継続", "一部利確", "売却"]
ScenarioType = Literal["bull", "neutral", "bear"]


@dataclass
class ConditionDef:
    """シナリオ1条件の定義(config/scenarios/<theme>.yaml から読み込む)。"""

    condition_id: str
    desc: str
    indicator: str      # indicators.csv の key
    feature: str         # "level" | "zscore" | "yoy" | "mom" | "dz"
    op: str               # ">" | ">=" | "<" | "<=" | "abs_lt"
    threshold: float
    weight: float = 1.0


@dataclass
class Scenario:
    """1シナリオ(bull/neutral/bear)の条件セット。"""

    scenario_type: ScenarioType
    conditions: list[ConditionDef] = field(default_factory=list)


@dataclass
class ThemeScenarios:
    """1テーマのbull/neutral/bearシナリオ定義。"""

    theme: str
    bull: Scenario
    neutral: Scenario
    bear: Scenario


@dataclass
class ConditionStatus:
    """1条件の評価結果。"""

    condition_id: str
    desc: str
    indicator_key: str
    measured_value: float | None
    threshold: float
    met: bool | None            # None = 指標取得不可(観測不能)
    data_quality: str
    as_of: str


@dataclass
class ScenarioAssessment:
    """1シナリオの評価結果(成立条件・成立率・未成立条件)。"""

    theme: str
    scenario_type: ScenarioType
    fulfillment_rate: float      # Σ(met条件のweight) / Σ(観測可能条件のweight)
    conditions: list[ConditionStatus] = field(default_factory=list)
    unmet: list[ConditionStatus] = field(default_factory=list)
    unobservable: list[ConditionStatus] = field(default_factory=list)


@dataclass
class DecisionRecord:
    """L2の最終出力。必須表示項目を型で強制する。"""

    decision_id: str            # "dec_{as_of}_{target}"
    as_of: str
    target: str
    theme: str
    action: str                  # Action の値(Literalは型ヒントのみ、実行時はstr)
    active_scenario: str         # 現在地(最も成立率が高いシナリオ)
    scenario_assessments: list[ScenarioAssessment] = field(default_factory=list)
    reason: str = ""              # 判断理由
    prev_decision_id: str | None = None
    change_reason: str | None = None    # 変更理由(diff自動生成、変更なしならNone)
    theme_score: float | None = None
    confidence: float = 0.0
    evidence_indicators: list[str] = field(default_factory=list)   # L5記帳用


@dataclass
class DecisionChange:
    """前回との差分。"""

    target: str
    theme: str
    field: str            # "action" | "active_scenario"
    prev_value: str
    curr_value: str
