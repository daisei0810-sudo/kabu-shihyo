# config/

## rss_sources.csv

企業IR・政府機関のRSS/AtomフィードURLを登録する設定ファイル。**意図的に空の状態で
コミットされている** — 未検証のURLをコードに埋め込むと、取得0件のまま気づかれずに
運用され続けるリスクがあるため。実際に動作確認したフィードURLのみを追加すること。

列:

| 列名 | 説明 |
|---|---|
| `source_id` | 一意なID(例: `nvidia_ir`, `meti_press`) |
| `url` | RSS/AtomフィードのURL |
| `source_type` | `company_ir` / `gov` / `exchange` / `wire` / `trade_press` 等(`src/materials/taxonomy.py` の `SourceType` 参照) |
| `display_name` | 表示名 |
| `is_customer_official` | `true`/`false`。顧客側公式発表なら`true`(source_rankがAに固定される) |

追加例:

```csv
source_id,url,source_type,display_name,is_customer_official
example_gov,https://example.go.jp/rss/press.xml,gov,Example省 プレスリリース,false
```

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
