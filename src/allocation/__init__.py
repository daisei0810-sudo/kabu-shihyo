"""Investment OS Layer9(Capital Allocation Engine)。

期待リターン推定の信頼度が低いため平均分散最適化は採用せず、ルールベース+
相関ペナルティで開始する(docs/investment_os_design.md §4.9)。

推奨配分(recommended_pct)・現在配分(current_pct)・差分(diff_pct)はいずれも
保有資産構成そのものを示すため非公開とする(§8確定事項)。出力は
private/allocation.csv(gitignore対象)。
"""
