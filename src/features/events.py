"""閾値イベント検出 — Zスコア超過イベントを定義しイベント日リストを生成。

methodology.md §3 に対応。
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class ThresholdEventDetector:
    """指標ZスコアのThreshold超過をイベントとして定義。"""

    def __init__(
        self,
        threshold: float = 1.5,
        direction: str = "above",   # "above" | "below"
        cooldown_days: int = 14,    # イベント間の最小間隔（重複カウント防止）
    ) -> None:
        self.threshold = threshold
        self.direction = direction
        self.cooldown_days = cooldown_days

    def detect(self, zscore: pd.Series) -> pd.DatetimeIndex:
        """Zスコア系列からイベント日を抽出。

        Args:
            zscore: 日次Zスコア系列

        Returns:
            イベント日 DatetimeIndex
        """
        z = zscore.dropna().sort_index()

        if self.direction == "above":
            mask = z >= self.threshold
        elif self.direction == "below":
            mask = z <= -self.threshold
        else:
            raise ValueError(f"direction must be 'above' or 'below', got {self.direction!r}")

        raw_dates = z[mask].index

        # クールダウン適用（連続シグナルを1イベントに圧縮）
        events: list[pd.Timestamp] = []
        last: pd.Timestamp | None = None
        for d in raw_dates:
            if last is None or (d - last).days >= self.cooldown_days:
                events.append(d)
                last = d

        logger.debug(
            "ThresholdEventDetector: raw=%d → after_cooldown=%d (thr=%.1f dir=%s)",
            len(raw_dates),
            len(events),
            self.threshold,
            self.direction,
        )
        return pd.DatetimeIndex(events)
