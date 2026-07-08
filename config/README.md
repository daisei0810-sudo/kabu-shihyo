# config/

## instruments.csv / indicators.csv / themes.csv (Investment OS Layer1/Layer3マスタ)

`docs/investment_os_design.md` のマスタ外部化(フェーズP1)。正本はこの3ファイルで、
`src/config.py` の `INSTRUMENTS`/`INDICATORS` は起動時に `src/registry/*.py` 経由で
これらを読み込むだけになった(コード内に静的リストは残っていない)。

- **instruments.csv**: 銘柄マスタ(Layer1)。列は `key,name_ja,layer,ticker,
  coingecko_id,held,data_quality,proxy_key,note`。`proxy_key` は非上場銘柄の
  価格代理(例: `quantinuum` → `honeywell`)。**銘柄追加はこのCSVへ1行足すだけ**で
  yfinance取得・スコアリング・予測台帳まで自動的に伝播する。
- **indicators.csv**: 指標辞書(Layer3)。列は `key,name_ja,layer,source,
  data_quality,targets(;区切り),note,parquet_stem,column,loader,
  step2_verifiable,freq`。重要度・観測性・鮮度SLAは手動採点せず
  `src/registry/indicators.py` が `data_quality`/`freq` から機械的に導出する
  (推測で断定しない、という既存方針を踏襲)。
- **themes.csv**: テーマ(サイクル)マスタ。列は `key,name_ja,status,
  benchmark_key,note`。`status=watch` は優先度低で監視のみ(例: バイオ)。
  `benchmark_key` はLayer5(予測検証)が超過リターンを計算する際のベンチマーク指数。

## ism_pmi_manual.csv (ISM製造業PMIの手動更新)

FRED(`ISMMAN`/`NAPM`)・DBnomics(`ISM/pmi/pm`)いずれも無料での自動取得が2025年後半
以降不可能と判明した(ISM社のライセンス制限。DBnomicsは2025-09分以降のデータが
実体経済的にあり得ない一桁台に破損しており、同時期にISM社側の提供が止まったとみられる。
詳細: `docs/investment_os_design.md` §12)。ISM公式の無料月次プレスリリース
(https://www.ismworld.org/、毎月第1営業日に前月分公表)から手入力する運用に切替。

列は `month(YYYY-MM),value,updated_at,note`。2020-05〜2025-08分はDBnomicsの
破損前の実データで検証済みの上で事前投入済み。**実数値そのものであり代理指標では
ないため`data_quality=verified`扱い**(`config/indicators.csv`の`ism_mfg_pmi`行)。

更新を忘れると`outputs/daily_report.md`の「手動更新指標の鮮度」節に自動で警告が
表示される(`src/data_sources/ism_pmi_manual.py`の`staleness_note()`、ISMの公表
サイクルから何ヶ月遅れているかを機械的に算出)。運用: 毎月ISM公式サイトで新しい
数値を確認し、このCSVへ1行追記するだけでよい。

なお`config/indicators.csv`には自動取得できる補助シグナルとして
`us_mfg_confidence_oecd`(OECD製造業景況感指数、FRED経由)も別指標として併存させて
いる。スケール・調査主体が異なる別物であり、ISM PMIの代用と偽装しない。

## structural_scores.csv (Investment OS Layer4構造変化スコアの手動評価)

テーマの構造変化スコア(0-30点、6軸ルーブリックの最大配点軸)の手動評価。
列は `theme,score,updated_at,note`。materials由来の自動加減点だけでは
「巨大サイクルの構造変化」という定性的判断を捕捉しきれないため、月次で
ユーザーが更新するハイブリッド運用(§8確定事項)。rss_sources.csvと同様、
**意図的に空の状態でコミット**されている(未評価テーマはunavailableとして
正直に表示され、捏造しない)。

## scenarios/*.yaml (Investment OS Layer2シナリオ定義)

テーマごとの強気/中立/弱気シナリオの成立条件(`config/scenarios/<theme>.yaml`)。
`config/scenarios/generate.py` が `indicators.csv` から機械的に生成する
(現状は全指標が統計的に未実証・C/Dランクのため、dz(勢いZスコア)の符号のみを
条件にした暫定版。指標の検証ランクがA/Bへ改善したら人手で閾値を調整する想定)。
Layer6(risk_scores)の判定も `risk:<category>` 形式でbearシナリオに含まれる。

再生成方法:
```
python "C:\Users\daisei\株指標ツール\config\scenarios\generate.py"
```

## allocation_policy.yaml (Investment OS Layer9配分ポリシー)

資金配分エンジンのルール(1テーマの上限/下限%・現金下限%・相関ペナルティ閾値)。
投資方針そのもの(ルール)であり実際の保有比率は含まないため公開している。

## holdings.example.csv (Investment OS Layer9現在配分テンプレート)

`private/holdings.csv`(gitignore対象、非公開)のスキーマ例。列は `theme,
current_pct` のみ(§8確定事項により金額・株数は持たない)。実際の保有比率は
このファイルをコピーして `private/holdings.csv` に手動入力する。

## rss_sources.csv

企業IR・政府機関のRSS/AtomフィードURLを登録する設定ファイル。**未検証のURLは
登録しない方針**(取得0件のまま気づかれずに運用され続けるリスクがあるため)。
実際に`--step 5`で取得成功を確認したフィードURLのみを追加すること。

列:

| 列名 | 説明 |
|---|---|
| `source_id` | 一意なID(例: `nvidia_ir`, `meti_press`) |
| `url` | RSS/AtomフィードのURL |
| `source_type` | `company_ir` / `gov` / `exchange` / `wire` / `trade_press` 等(`src/materials/taxonomy.py` の `SourceType` 参照) |
| `display_name` | 表示名 |
| `is_customer_official` | `true`/`false`。顧客側公式発表なら`true`(source_rankがAに固定される) |

登録済み例(2026-07-08動作確認済み):

```csv
source_id,url,source_type,display_name,is_customer_official
fujikura_prtimes,https://prtimes.jp/companyrdf.php?company_id=56990,company_ir,フジクラ(PR TIMES),true
```

**PR TIMES企業別RSSの見つけ方**: `https://prtimes.jp/main/html/searchrlp/company_id/<ID>`
形式の企業ページを開き、「RSSを購読する」リンクのURLをコピーする
(`https://prtimes.jp/companyrdf.php?company_id=<ID>` 形式)。WebSearchで見つけた
`companyrss/<ID>` 等の推測パターンは実在せず404だった(2026-07-08確認)。企業ページの
実際のリンクから取得すること。他の保有銘柄(村田製作所・ファナック・安川電機・
ローツェ・キオクシア・ハーモニックドライブ)も同様の手順でPR TIMES企業ページを
探せば見つけられる可能性が高い。

**政府機関ドメイン(meti.go.jp等)は原則ブロックされる**: Bot対策(WAF)により、
User-Agentを送っていても403 Forbiddenで拒否されることを複数パターンで確認した
(2026-07-08)。ブラウザで手動確認したURLでなければ機能しない可能性が高い。

URLを追加したら `python -m src.main --step 5` で実際に取得できるか確認すること。
0件が続く場合はURLが変わった可能性が高い。

## Reuters/Bloomberg等の大手報道(source_rank=B)について

無料APIが存在しないため自動取得できない。`data/materials_manual/pending.csv` に
手動で追記することで取り込む(`src/materials/manual_input.py` 参照)。

## EDINET(日本の開示システム)連携について

保有銘柄の大半(フジクラ・ローツェ・キオクシア・村田製作所・ハーモニックドライブ・
ファナック・安川電機)は日本上場企業であり、SEC EDGAR(米国上場企業のみ対象)では
一切カバーできない。この欠落を埋めるため `src/data_sources/edinet.py` を実装した。

**セットアップ:**
1. https://api.edinet-fsa.go.jp で無料の "Subscription-Key" を取得
2. 環境変数 `EDINET_API_KEY` に設定
3. `python -m src.main --step 5` を実行し、ログに `EDINET=N件`(N>0)と出るか確認

**動作確認済み(2026-07-02)**: 実際のAPIキーでフジクラ・村田製作所・ファナックの
臨時報告書等を正しく取得できることを確認済み。取込時、EDINETの提出者名
(例: "株式会社フジクラ")がconfig.INSTRUMENTSの企業名("フジクラ")と部分一致で
正しく紐付けられ、material_idに反映されることも確認済み(法人格接頭辞の付いた
日本語企業名を正しく正規化するよう material_id.py を修正済み)。

**related_tickers自動紐付け(2026-07-08)**: material_id生成時に使う正規化企業名
トークンが `config.INSTRUMENTS` 由来であれば、`material_id.py` の
`resolve_related_ticker()` がそのままそのトークンをinstruments.csvの`key`に逆引きし、
`related_tickers` へ自動セットする。`theme_score.py`の政策追い風軸・`risk/detectors.py`の
regulation/dilution/customer_churnカテゴリはこの`related_tickers`を頼りに材料を
検索するため、この紐付けが無いと永久にunavailableのままだった(P4後に修正)。
`_MANUAL_COMPANY_ALIASES`由来のトークン(TSMC/Micron等、instruments.csvに存在しない
企業)は対象外のままとなり、追跡していない企業へ誤って紐付けることはない。

**GitHub Actions(daily.yml)での自動実行について**: `EDINET_API_KEY`はリポジトリの
Secretsにも登録する必要がある(ローカルの環境変数とは別)。未登録の場合、Step5は
自動的にスキップされる(クラッシュはしない)。設定手順は `gh secret set EDINET_API_KEY`
または GitHub の Settings → Secrets and variables → Actions から追加する。
