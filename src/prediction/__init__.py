"""Investment OS Layer5(Prediction Validation Engine)— 最重要レイヤー。

日々の投資判断(現状はStep3 `portfolio_signal_scores.csv` のoutlook/action、
将来はLayer2 decision engineの `DecisionRecord`)を予測として記帳し、
3/6/12ヶ月後に実際の株価で答え合わせすることで「どの指標が効いたか」を
学習可能にする。

3ヶ月後の答え合わせは今日記帳を始めないと3ヶ月遅れるため、Layer2(シナリオ判定)
やLayer4(6軸ルーブリック)が未完成でも、現行の判断をそのまま記帳する最小版から
先に稼働させる(docs/investment_os_design.md §5 フェーズP1参照)。
"""
