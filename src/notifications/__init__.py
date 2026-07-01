"""通知システム(Step6) — §13事後検証DB / §14自動学習(先送り) / §17通知ロジック / §18通知テンプレ。

依存方向の制約(materials.pyと同じ思想):
  notifications → scoring (可)
  notifications → materials (可、読み取りのみ)
  scoring → notifications (禁止)
  materials → notifications (禁止)

「通知」の実体は daily_report.md 冒頭の専用セクション表示である(実送信先が
存在しないため)。§14自動学習は通知が0件蓄積の現状では学習対象が無いため、
スタブすら作らずPhase10へ先送りしている(backtests.jsonlへのデータ蓄積のみ行う)。
"""

from src.notifications.models import Backtest, BacktestSummary, DecisionChange, Notification
from src.notifications.pipeline import run_notifications

__all__ = [
    "Notification",
    "Backtest",
    "BacktestSummary",
    "DecisionChange",
    "run_notifications",
]
