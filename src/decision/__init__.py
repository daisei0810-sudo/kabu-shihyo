"""Investment OS Layer2(Investment Decision Engine)。

config/scenarios/<theme>.yaml の強気/中立/弱気シナリオ条件を実データで評価し、
成立条件・現在地・成立率・未成立条件・判断理由・変更理由を必須項目として持つ
DecisionRecordを生成する(docs/investment_os_design.md §4.3)。

実際の売買アクション判定ロジックは変更しない: 既存
`src.scoring.portfolio._map_decision()`(スコア水準+モメンタム+confidenceに基づく
判定、既に運用実績あり)をそのまま呼び出し、その出力をLayer2の5分類語彙
(新規買い/追加買い/保有継続/一部利確/売却)へ変換するだけ。シナリオ成立率は
判断の構造化された開示情報として付加する(判断ロジックそのものは変えない=
副作用最小化)。

出力(DecisionRecord)は保有銘柄ごとの売買判断そのものであり、
docs/investment_os_design.md §8確定事項により公開リポジトリには置かない。
private/decisions/ (gitignore対象)へ保存する。
"""
