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
