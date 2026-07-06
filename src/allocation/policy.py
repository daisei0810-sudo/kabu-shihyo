"""config/allocation_policy.yaml の読み込み。"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.allocation.models import AllocationPolicy

DEFAULT_POLICY_PATH = Path("config/allocation_policy.yaml")


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> AllocationPolicy:
    """ポリシーYAMLを読み込む。ファイルが無ければ既定値(AllocationPolicyのdefault)を返す。"""
    if not path.exists():
        return AllocationPolicy()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    defaults = AllocationPolicy()
    return AllocationPolicy(
        min_pct=float(raw.get("min_pct", defaults.min_pct)),
        max_pct=float(raw.get("max_pct", defaults.max_pct)),
        cash_floor_pct=float(raw.get("cash_floor_pct", defaults.cash_floor_pct)),
        correlation_penalty_threshold=float(
            raw.get("correlation_penalty_threshold", defaults.correlation_penalty_threshold)
        ),
        correlation_penalty_factor=float(
            raw.get("correlation_penalty_factor", defaults.correlation_penalty_factor)
        ),
        rebalance_threshold_pct=float(
            raw.get("rebalance_threshold_pct", defaults.rebalance_threshold_pct)
        ),
    )
