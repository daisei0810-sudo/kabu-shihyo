"""Layer5予測台帳のタクソノミー(ホライズン・方向マッピング)。

依存方向の制約(notifications/taxonomy.pyと同じ思想):
  prediction → scoring (可、読み取りのみ)
  scoring → prediction (禁止)
この制約により、既存のHard/Extended計算はpredictionパッケージの存在を
一切知らず、predictionパッケージが壊れても既存フローに影響しない。
"""

from __future__ import annotations

# Layer5が要求する事後検証ホライズン(§ 追跡項目: 3か月後/6か月後/12か月後)。
# notifications.taxonomy.BACKTEST_HORIZONS(1w/1m/3m、通知の短期反応検証用)とは
# 目的が異なる(投資判断そのものの中期的な正しさを検証する)ため意図的に別定義とする。
PREDICTION_HORIZONS: dict[str, int] = {"3m": 90, "6m": 180, "12m": 365}

# 現行 scoring/portfolio.py `_map_decision()` が返す action 文字列 → 期待方向(+1/0/-1)。
# Layer2(シナリオベースの意思決定エンジン、docs/investment_os_design.md §4.3)が
# 稼働するまでの暫定マッピング。score水準に基づくoutlook/actionの意味論(低スコア=
# 弱気)から方向を機械的に導出しており、恣意的な判断は加えていない。
ACTION_DIRECTION: dict[str, int] = {
    "追加": 1,
    "保有継続(監視)": 0,
    "保有継続": 0,
    "利確検討": -1,
    "撤退候補": -1,
    "要確認": 0,   # スコア算出不可 = 方向の主張なし
}

# Layer2(src.decision)の5分類語彙 → 期待方向。ACTION_DIRECTIONと役割は同じだが、
# Layer2稼働後は source_layer="decision" のPredictionがこちらを使う
# (§4.6(e): Backtest/evaluationsのカラム名は揃えたまま、判断語彙の移行のみ行う)。
L2_ACTION_DIRECTION: dict[str, int] = {
    "新規買い": 1,
    "追加買い": 1,
    "保有継続": 0,
    "一部利確": -1,
    "売却": -1,
}

# 「上昇/下落を言い当てたか」を評価できるのは方向性のある判断のみ。
# 中立(0)は的中/不的中の概念が無意味なため direction_hit は None とする。
DIRECTIONAL_JUDGMENTS: frozenset[str] = frozenset(
    k for k, v in ACTION_DIRECTION.items() if v != 0
) | frozenset(k for k, v in L2_ACTION_DIRECTION.items() if v != 0)
