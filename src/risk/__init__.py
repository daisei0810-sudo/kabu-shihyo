"""Investment OS Layer6(Risk Engine) — 上昇検知ではなく下落検知を目的とする。

`scoring/collapse_watch.py`(AIサイクル専用・市場全体の6項目監視)の手法を
「テーマ×リスクカテゴリ」の行列へ一般化する(docs/investment_os_design.md §4.7)。

保有銘柄ごとのリスク詳細(private/risk_scores.csv)は個別企業の懸念材料を含み、
どの保有銘柄をどの理由で警戒しているかを示すため非公開とする(§8確定事項)。
テーマ集約のrisk_level(0-3、具体的な理由は含まない)のみ`outputs/risk_level_by_theme.csv`
として公開する(collapse_watch.csvと同じ扱い)。
"""
