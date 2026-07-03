"""予測台帳・事後評価のデータクラス(Investment OS Layer5)。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Prediction:
    """1銘柄×1日の投資判断を凍結した予測レコード。

    現状は Step3(`portfolio_signal_scores.csv`)の outlook/action をそのまま
    記帳する最小版(source_layer="portfolio_snapshot")。将来 Layer2
    decision engine が稼働したら source_layer="decision" のレコードに移行する
    (カラム構成は据え置きなので評価/集計コードの変更は不要)。
    """

    prediction_id: str          # "pred_{as_of}_{target}"
    created_at: str
    as_of: str                  # 予測日
    source_layer: str           # "portfolio_snapshot" | "decision"(将来のLayer2)
    theme: str | None           # テーマ(Layer)キー
    target: str                 # 銘柄key
    judgment: str               # 現行のaction文字列(例: "追加","保有継続","利確検討"...)
    expected_direction: int     # +1(上昇期待) / 0(中立) / -1(下落警戒)
    score_at_prediction: float | None
    confidence_at_prediction: float | None
    baseline_date: str
    baseline_price: float | None
    benchmark_key: str | None = None
    benchmark_is_approximate: bool = False
    evidence_json: str = "[]"   # 根拠指標のスナップショット(Layer2稼働後に充実させる)
    status: str = "open"        # "open" | "closed"(全horizon評価済み)


@dataclass
class Evaluation:
    """1予測×1ホライズンの事後評価レコード。"""

    evaluation_id: str          # "{prediction_id}_{horizon}"
    prediction_id: str
    horizon: str                 # "3m" | "6m" | "12m"
    due_date: str
    evaluated_at: str | None = None
    actual_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None
    max_drawdown: float | None = None
    direction_hit: bool | None = None   # expected_direction=0 の中立判断は評価対象外(None)
    status: str = "pending"      # "pending" | "evaluated" | "skipped_no_data"


@dataclass
class PredictionAccuracySummary:
    """事後検証の集計サマリー(daily_report表示用)。"""

    n_predictions: int
    n_pending_evaluations: int
    n_evaluated: int
    n_skipped: int
    hit_rate: float | None = None          # direction_hit が not None の評価のみで算出
    avg_excess_return: float | None = None
    next_due_date: str | None = None
