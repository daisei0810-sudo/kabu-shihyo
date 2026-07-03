# Investment OS 実装設計ドキュメント

作成日: 2026-07-03 / 設計: Claude Fable 5 / 対象リポジトリ: `C:\Users\daisei\株指標ツール`

---

## 1. 現状リポジトリの調査結果（要約)

### 1.1 全体像

既存システムは「先行指標監視システム」として Step0〜Step5 まで実装済み。思想的な核は以下で、これは Investment OS の Layer0/Layer3/Layer5 の思想と高い互換性がある。

- **二重スコア設計**（Hard = verified のみ / Extended = proxy・estimated を信頼度重み付き加算、confidence% 併記)
- **推測で断定しない**（unavailable は weight=0 で表示のみ)
- **統計的誠実さ**（見せかけ相関排除、FDR補正、実効サンプルガード、walk-forward 再現性 — `methodology.md` §2-4)

### 1.2 主要モジュールと役割

| モジュール | 役割 | 主要インターフェース |
|---|---|---|
| `src/config.py` | 銘柄ユニバース (`INSTRUMENTS` 24銘柄、`held` フラグ)、指標カタログ (`INDICATORS` 33件)、`DataQuality`/`Layer`/`DataSource` enum、`Indicator.confidence_weight` | `Instrument`/`Indicator` (pydantic BaseModel) |
| `src/data_sources/` | Step1 データ取得 (yfinance/coingecko/xrpl/defillama/fred/sec_edgar/edinet/rss) | `base.py` の Fetcher 基底クラス、`data/raw/` スナップショット + `data/processed/*.parquet` |
| `src/features/` | YoY/MoM/MA/Zスコア (`FeatureEngineer`)、閾値イベント (`ThresholdEventDetector`) | pandas Series in/out |
| `src/validation/` | ラグ相関 (BH補正付き)、イベントスタディ (非重複窓+ブートストラップ)、有効性ランク A+〜D (`IndicatorRanker`) | `outputs/lag_correlation_matrix.csv`, `event_study_results.csv`, `indicator_scorecard.csv` |
| `src/scoring/` | `ScoreEngine` (scorecard 準拠の Hard/Extended 集約)、`components.py` (加重平均+confidence の汎用パターン)、cycle_scores / demand_index / xrp_scores / technicals / dip_sell / collapse_watch / capex_trend / score_history | `PortfolioScorer.run() -> PortfolioResult`、`_map_decision(score, confidence, momentum) -> (outlook, action, note)` |
| `src/notifications/` | 通知パイプライン、**`decision_history.py` (前回判断とのdiff)**、**`backtest_eval.py` (通知の1w/1m/3m事後検証)**、suppressor、confidence | `outputs/notifications/*.jsonl`、`outputs/history/decisions/YYYY-MM-DD.csv` |
| `src/materials/` | 材料DB (SQLite `data/materials.db` + JSONL正本)、material_id、重複検知、鮮度スコア、ソースランクA-D、**因果グラフ (causal_graph_nodes/edges テーブル)** | `Material`/`MaterialDraft`/`CausalNode`/`CausalEdge` dataclass |
| `src/reporting/daily_report.py` | `outputs/*.csv` を読んで Markdown 生成（**既にCSV経由でロジックと分離されている**) | `generate_daily_report() -> str` |
| `src/dashboard/builder.py` | Plotly PWA (GitHub Pages) | 同上、CSV読み取りのみ |
| `src/main.py` | ステップオーケストレーション (`--step 1/2/3/4/5/6/all`) | CLI |

### 1.3 重要な発見

1. **Layer10 の分離要件は既に事実上満たされている**。`daily_report.py` と `dashboard/builder.py` は `outputs/*.csv` の読み取りのみで判定ロジックを import していない。この「アーティファクト(CSV/JSONL)を層間契約とする」パターンを全レイヤーに一般化すればよい。
2. **Layer5 の原型が2つ存在する**。(a) `validation/` = 指標→価格の統計的検証、(b) `notifications/backtest_eval.py` = 通知(判断)単位の 1w/1m/3m 事後検証（`Backtest` dataclass に excess_return・false_positive_flag 等が既にある）。ユーザー要求の 3/6/12ヶ月検証・重み自動更新は (b) の拡張 + (a) のランクを重みへ接続することで実現できる。
3. **判断差分の原型も存在する**。`decision_history.py` が `outputs/history/decisions/YYYY-MM-DD.csv` にスナップショットを取り `diff_decisions()` で差分検知している。Layer2 の「前回判定との差分自動生成」はこれの拡張。
4. **欠落しているテーマ**: `Layer` enum に**電力**が無い（crypto_xrp / ai_datacenter / semicap / robotics_fa / ev_physical_ai / quantum / china_ai / policy の8種）。電力レイヤーとその指標（Utility CAPEX 等）は新規。バイオも無い（優先度低なので後回しで可）。
5. **銘柄マスタはコード内ハードコード** (`config.py` の `INSTRUMENTS`)。保有9銘柄のうちハーモニック・ドライブも held=True で登録済み（ユーザーのLayer1リストには無い→確認事項）。`key="lasertec_rorze"` は名称が紛らわしい（実体はローツェ 6323.T で正しい）。
6. **Layer4 の採点ルーブリック（構造変化30/需給25/業績20/バリュエーション10/資金流入10/政策5）は未実装**。ただし部品は揃っている: 需給→`demand_index`/`cycle_scores`、業績→`capex_trend`+yfinance四半期、政策→`materials` のトピック分類、構造変化→`causal_graph`。
7. Layer7/8/9（発掘・配分）は完全新規。

---

## 2. Layer0〜10 と既存資産のマッピング表

| Layer | 状態 | 既存資産 | 再利用/拡張方針 |
|---|---|---|---|
| **L0 Philosophy** | △ 暗黙 | `methodology.md` §0、`config.py` 冒頭の設計原則 | 新規 `config/philosophy.yaml` に「狙う/避ける/優先順位」を宣言的に記述し、L7/L8のフィルタ・L4の重み根拠として機械参照可能にする。ロジック不要、小規模 |
| **L1 Vision** | ○ 大部分あり | `INSTRUMENTS` (held=True 9銘柄+ピア15銘柄) | 銘柄マスタを `config/instruments.csv` へ外部化（§6）。テーマ一覧に「電力」を追加 |
| **L2 Decision Engine** | △ 原型あり | `portfolio._map_decision`、`dip_sell.py`、`decision_history.py` (diff)、`notifications/detectors.detect_decision_changes` | 新規 `src/decision/` に統合。シナリオ（強気/中立/弱気）＋成立条件テーブルを新設し、既存の score→action マッピングを条件評価型へ昇格。diff機構は再利用 |
| **L3 Indicator Dictionary** | ○ 骨格あり | `Indicator` モデル (data_quality/confidence_weight/freq/loader)、`indicator_scorecard.csv` (rank/実測フラグ) | `Indicator` に重要度・観測性・鮮度SLA・代替指標を属性追加し `config/indicators.csv` へ外部化。電力/Physical AI/量子の指標を追加登録 |
| **L4 Scoring Engine** | △ 部品あり・ルーブリック無し | `ScoreEngine`、`components.py`、`cycle_scores`、`demand_index`、`capex_trend` | 新規 `src/scoring/theme_score.py` が既存コンポーネントを6軸ルーブリックに再構成。既存スコアは軸の入力として温存 |
| **L5 Prediction Validation** | △ 原型2系統 | `validation/`（指標検証）、`backtest_eval.py`（判断検証 1w/1m/3m）、`score_history.py` | **新規 `src/prediction/`**。予測台帳(JSONL)＋3/6/12ヶ月バッチ評価＋指標重み自動更新。`Backtest` の設計思想・ホライゾン機構を流用（§4.6で詳述） |
| **L6 Risk Engine** | △ 一部あり | `collapse_watch.py`（CAPEX削減/金利/VIX/SOX/光通信の劣化検知）、`dip_sell.sell_score`、materials のトピック分類 | 新規 `src/risk/`。collapse_watch を「AIサイクル専用」から「テーマ×リスクカテゴリの行列」へ一般化。規制/希薄化/競争敗北は materials 由来イベントで検知 |
| **L7 New Investment Discovery** | ✕ 新規 | 非保有ピア銘柄15件が既にユニバースに存在、`indicator_loader.peer_basket_excluding` | 新規 `src/discovery/companies.py`。既存スコアリングを非保有銘柄に適用しランキング |
| **L8 New Theme Discovery** | ✕ 新規 | `materials/ingest.py` (RSS/EDGAR/EDINET)、`config/rss_sources.csv` | 新規 `src/discovery/themes.py`。RSSソースに arXiv/政府予算/VC系を追加し、materials のテーマ別出現頻度トレンドで候補化。定性入力（手動）併用 |
| **L9 Capital Allocation** | ✕ 新規 | 価格 parquet（相関計算可能）、テーマスコア | 新規 `src/allocation/`。ルールベース配分（スコア比例＋リスクヘアカット＋上下限） |
| **L10 Report Template** | ○ 分離済み | `daily_report.py`、`dashboard/`、通知テンプレート（§18項目: info_as_of/freshness等は `Notification` に既存） | テンプレートを「Early Signal→構造変化→判断→アクション」の必須章立てに再構成。**CSV/JSONL契約の読み取り専用は維持** |

**結論: 作り直しは不要。** 追加すべき新パッケージは `src/decision/`、`src/prediction/`、`src/risk/`、`src/discovery/`、`src/allocation/` の5つと、設定の外部化のみ。

---

## 3. 全体アーキテクチャ

### 3.1 レイヤー間契約の原則（既存パターンの一般化）

- 各レイヤーは **独立パッケージ** とし、他レイヤーの Python モジュールを import しない（共有するのは `src/registry/`（マスタ）と `src/common/`（型・IO）のみ）。
- レイヤー間の受け渡しは **型付きアーティファクト（outputs/ 配下の CSV / JSONL + スキーマ定義）** で行う。これは daily_report が既に採用している方式。
- 各レイヤーは `run(as_of: date) -> ArtifactSet` という統一シグネチャのエントリポイントを持ち、`src/main.py` の `--step` に登録する。将来レイヤーごと差し替え可能。
- 正本は JSONL（materials と同方式）、集計キャッシュは CSV/SQLite。SQLite は `data/materials.db` を拡張せず、新規 `data/investment_os.db` に分離（materials は L6/L8 の入力源として独立性を保つ）。

### 3.2 ディレクトリ配置案（最終形）

```
config/
  philosophy.yaml        # L0: 狙う/避ける/優先順位（宣言）
  instruments.csv        # L1: 銘柄マスタ（§6）
  themes.csv             # テーマ（サイクル）マスタ
  indicators.csv         # L3: 指標辞書（属性拡張版）
  scenarios/             # L2: テーマ別シナリオ定義 (yaml)
    ai_datacenter.yaml, physical_ai.yaml, quantum.yaml, power.yaml, xrp.yaml ...
src/
  registry/              # マスタのローダー（config.py の pydantic 検証を移設・共有）
    instruments.py, themes.py, indicators.py
  data_sources/          # (既存) L3 の収集実体。電力系ソース追加
  features/, validation/ # (既存) L3/L5 の統計基盤
  scoring/               # (既存+) L4。theme_score.py を追加
  decision/              # 新規 L2
    scenarios.py, engine.py, diff.py
  prediction/            # 新規 L5（最重要）
    ledger.py, evaluator.py, attribution.py, weight_updater.py
  risk/                  # 新規 L6
    engine.py, detectors.py
  discovery/             # 新規 L7/L8
    companies.py, themes.py
  allocation/            # 新規 L9
    engine.py
  reporting/, dashboard/ # (既存+) L10。テンプレート再構成
  notifications/, materials/  # (既存) L6/L10 の部品
outputs/
  theme_scores.csv, risk_scores.csv, decisions/…, allocation.csv,
  discovery_companies.csv, discovery_themes.csv
data/
  predictions/           # L5 正本 (JSONL, git管理)
    predictions.jsonl, evaluations.jsonl, weight_history.jsonl
  investment_os.db       # L5/L2 のSQLiteキャッシュ (gitignore)
```

### 3.3 データフロー全体図

```
[L3 収集]  data_sources → data/raw → data/processed/*.parquet
                └ materials/ingest → data/materials.db (材料・因果グラフ)
     │
[L3 検証]  features + validation → indicator_scorecard.csv (rank A+〜D)
     │                                      ▲
     │                     ┌────────────────┘ 重み上書き
[L5 学習]  prediction/weight_updater → outputs/indicator_weights.csv
     │
[L4 採点]  scoring/theme_score (6軸ルーブリック)
             ├ 入力: parquet特徴量, scorecard, indicator_weights, materials集計
             └ 出力: theme_scores.csv (テーマ×6軸×Hard/Extended×confidence)
     │
[L6 リスク] risk/engine → risk_scores.csv (テーマ×銘柄×リスクカテゴリ)
[L7/L8 発掘] discovery → discovery_companies.csv / discovery_themes.csv
     │
[L9 配分]  allocation/engine (theme_scores + risk + 相関) → allocation.csv
     │
[L2 判定]  decision/engine (scenarios/*.yaml を theme_scores/risk で評価)
             ├ 出力: outputs/decisions/YYYY-MM-DD.jsonl (判断レコード)
             ├ 差分: decision/diff (前回比較 → 変更理由)
             └ 副作用: prediction/ledger へ予測を自動記帳 ★L5の入口
     │
[L10 出力] reporting/daily_report + dashboard
             └ 入力は outputs/*.csv, decisions/*.jsonl のみ（ロジック非依存）
```

---

## 4. 各レイヤー詳細設計

### 4.1 Layer0: Philosophy（`config/philosophy.yaml`）

```yaml
seek:        [ten_bagger, winner_takes_most, network_effect, platform, infrastructure, bottleneck]
avoid:       [mature_market, commodity_competition, transient_boom]
priority:    [structural_change, supply_demand, earnings]   # 株価は入力にしない
price_is_output: true
```

用途: L7/L8 のスクリーニングフィルタ（例: `avoid` タグの付いた候補は自動除外）、L4 の軸重み（構造変化30点が最大である根拠）のドキュメント化。ロジックは持たない。

### 4.2 Layer1 + 銘柄マスタ（§6 と統合、後述）

### 4.3 Layer2: Investment Decision Engine（`src/decision/`）

**シナリオ定義（`config/scenarios/<theme>.yaml`）** — 判定ルールをコードから分離:

```yaml
theme: ai_datacenter
scenarios:
  bull:
    conditions:
      - id: hyperscaler_capex_up
        desc: "ハイパースケーラーCAPEX YoY +30%以上"
        indicator: hyperscaler_capex   # indicators.csv の key
        feature: yoy                   # features/engineer の特徴量名
        op: ">="
        threshold: 0.30
        weight: 0.3
      - id: optical_leadtime
        desc: "光モジュール需要バスケットが200MA上"
        indicator: optical_module_demand
        feature: ma200_dev
        op: ">"
        threshold: 0.0
        weight: 0.2
      # ...
  neutral: { conditions: [...] }
  bear:    { conditions: [...] }
```

**主要型・シグネチャ:**

```
@dataclass ConditionStatus:
    condition_id, desc, indicator_key, measured_value, threshold,
    met: bool | None            # None = 指標取得不可
    data_quality, as_of

@dataclass ScenarioAssessment:
    theme, scenario_type        # bull / neutral / bear
    fulfillment_rate: float     # Σ(met条件のweight)/Σ(観測可能条件のweight)
    conditions: list[ConditionStatus]
    unmet: list[ConditionStatus]        # 未成立条件（必須表示項目）
    unobservable: list[ConditionStatus]

@dataclass DecisionRecord:                       # L2 の最終出力（必須表示項目を全て型で強制）
    decision_id, as_of, target, theme
    action: Literal["新規買い","追加買い","保有継続","一部利確","売却"]
    active_scenario: str                # 現在地（どのシナリオに最も近いか）
    scenario_assessments: list[ScenarioAssessment]   # 成立条件・成立率・未成立条件
    reason: str                         # 判断理由
    prev_decision_id: str | None
    change_reason: str | None           # 変更理由（diff自動生成、変更なしなら None）
    theme_score: float; risk_score: float; confidence: float
    evidence_indicators: list[str]      # L5 記帳用

decision/engine.py:
    def decide(theme_scores, risk_scores, scenarios, allocation, as_of) -> list[DecisionRecord]
decision/diff.py:   # 既存 notifications/decision_history.py を移設・拡張
    def diff(prev: list[DecisionRecord], curr: list[DecisionRecord]) -> list[DecisionChange]
```

**L2⇔L10 の境界**: `decide()` は `outputs/decisions/YYYY-MM-DD.jsonl` に DecisionRecord を書き出して終了。レポートは一切生成しない。文言テンプレート（「スコア高(95)・上昇モメンタム」等）も DecisionRecord の構造化フィールドから L10 側が組み立てる。既存 `_map_decision()` のロジックは decision/engine.py へ移設し、シナリオ成立率を加味した判定に拡張する。

**副作用**: 判定確定時に `prediction/ledger.record_prediction()` を呼び、L5 に自動記帳する（唯一許可するレイヤー間直接呼び出し。嫌なら decisions JSONL を L5 が読む pull 型でも可 — 記帳漏れ防止の観点で push 型を推奨）。

### 4.4 Layer3: Indicator Dictionary（`config/indicators.csv` + `src/registry/indicators.py`）

既存 `Indicator` pydantic モデルに以下を追加し、定義を CSV へ外部化（`config.py` の INDICATORS はローダー呼び出しに置換、後方互換の re-export を残す）:

| 追加カラム | 型 | 意味 |
|---|---|---|
| `importance` | 1-5 | 投資重要度（L10 表示必須項目） |
| `observability` | direct / proxy / manual / none | 観測性 |
| `freshness_sla_days` | int | この日数を超えたら「鮮度低下」フラグ（L10のデータ鮮度表示に使用） |
| `fallback_indicator` | key or 空 | 代替指標（取得失敗時に scoring が自動フォールバック） |
| `theme` | themes.csv の key | Layer enum に **`power`** を追加 |

信頼度 = 既存 `data_quality`、実測率 = scorecard の実測結果と `data/processed` の存在チェックから `registry/indicators.py` が算出（`measured_rate = 直近30日で実データが取れた日数/30`）。

**新規指標の登録（電力・Physical AI・量子の不足分）**: Utility CAPEX（FRED/yfinance 電力会社CAPEX＝verified）、Transformer Lead Time（unavailable→estimated: materials イベント頻度）、Data Center Power Demand（proxy: 電力株バスケット+FRED発電量）、Humanoid出荷/受注残（estimated: materials）、Logical Qubit / Error Rate（manual: 手動入力CSV `data/materials_manual/` の既存機構を流用）。**取得不可のものは unavailable として正直に登録する**（既存思想の維持）。

### 4.5 Layer4: Scoring Engine（`src/scoring/theme_score.py`）

**出力スキーマ `outputs/theme_scores.csv`:**

```
theme, as_of,
structural_change (0-30), supply_demand (0-25), earnings (0-20),
valuation (0-10), fund_flow (0-10), policy_tailwind (0-5),
total (0-100), hard_total, extended_total,
confidence_pct, data_coverage_pct, change_1d, change_1w, change_1m, note
```

**各軸の入力（既存部品の再構成）:**

| 軸 | 配点 | 入力 |
|---|---|---|
| 構造変化 | 30 | causal_graph のエッジ強度 + materials の構造イベント（新市場・プラットフォーム採用）+ **手動評価（当面）**。confidence低め明示 |
| 需給 | 25 | 既存 `demand_index` / `cycle_scores` / XRPロック需要スコアをテーマ別に集約 |
| 業績 | 20 | 既存 `capex_trend` + yfinance 四半期売上/ガイダンス（新規小規模フェッチ） |
| バリュエーション | 10 | yfinance の PER/PSR パーセンタイル（新規、`normalizer.percentile_rank_score` 再利用） |
| 資金流入 | 10 | 出来高トレンド + ETF flows（大半 unavailable → proxy、confidence 減点） |
| 政策追い風 | 5 | materials の政策トピック件数トレンド（estimated） |

各軸は既存 `components.aggregate_components()`（加重平均+confidence 伝播）をそのまま使う。Hard/Extended の二重スコアも各軸で維持する。

### 4.6 Layer5: Prediction Validation Engine（最重要 / `src/prediction/`）

#### (a) 予測台帳の永続化

materials と同じ「**JSONL 正本（git管理）+ SQLite キャッシュ**」方式。`data/predictions/predictions.jsonl` に追記、`data/investment_os.db` の `predictions` テーブルへミラー（`materials/db.py` の `dump_to_jsonl`/`rebuild_from_jsonl`/`verify_roundtrip` パターンを踏襲）。

```
predictions テーブル / JSONL:
  prediction_id       TEXT PK   -- "pred_{as_of}_{target}_{hash}"
  created_at, as_of   TEXT      -- 予測日
  source_layer        TEXT      -- "decision"(L2) | "theme_score"(L4) | "risk"(L6) | "manual"
  theme, target       TEXT      -- テーマ / 銘柄key（テーマ予測は target=NULL）
  judgment            TEXT      -- 新規買い/追加買い/保有継続/一部利確/売却 or 強気/中立/弱気
  expected_direction  INTEGER   -- +1 / 0 / -1（答え合わせの符号）
  score_at_prediction REAL, confidence_at_prediction REAL
  evidence_json       TEXT      -- [{indicator_key, feature, value, zscore, weight, data_quality}] 根拠指標を凍結
  scenario_id         TEXT, fulfillment_rate REAL
  baseline_price      REAL, benchmark_key TEXT   -- 例 sox / topix / btc
  status              TEXT      -- open / partially_evaluated / closed

evaluations テーブル / evaluations.jsonl:
  evaluation_id  TEXT PK
  prediction_id  TEXT FK
  horizon        TEXT      -- "3m" | "6m" | "12m"（既存Backtestの"1w/1m/3m"を拡張定数化）
  due_date, evaluated_at TEXT
  actual_return, benchmark_return, excess_return, max_drawdown REAL
  direction_hit  INTEGER   -- sign(excess_return) == expected_direction
  status         TEXT      -- pending / evaluated / skipped_no_data
```

#### (b) 3/6/12ヶ月バッチの実行方式

既存 `backtest_eval.py` の機構をそのまま一般化する:
1. `ledger.record_prediction()` 時に horizon ごとの `evaluations` 行を `pending` + `due_date` 付きで先行生成（既存 `create_pending_backtests` と同型）。
2. 日次実行（`--step 7` として main.py に登録、GitHub Actions `daily.yml` に追加）で `evaluator.evaluate_due()` が `due_date <= today AND status=pending` を抽出し、`data/processed/price_*.parquet` から baseline/実績価格を引いて評価（既存 `evaluate_due_backtests` の `_price_at_or_before` を再利用）。非上場銘柄（SpaceX/Quantinuum）は proxy 価格（HON等）で評価し `benchmark_is_approximate=True`（既存フィールド流用）。
3. 冪等性: `evaluation_id` で upsert（既存 `store.upsert_backtests` パターン）。実行漏れがあっても翌日拾われる。

#### (c) 指標重み自動更新（`weight_updater.py`）

```
入力: evaluations (evaluated), predictions.evidence_json, indicator_scorecard.csv
出力: outputs/indicator_weights.csv (indicator_key, base_weight, learned_multiplier,
      effective_weight, n_evaluations, hit_rate, avg_excess_when_cited, updated_at)
      + data/predictions/weight_history.jsonl（監査用の全履歴）
```

アルゴリズム案（過学習ガードは methodology.md の思想を踏襲）:
1. **帰属**: 各評価済み予測について、根拠指標 i の寄与を `contribution_i = weight_i × sign(zscore_i) × sign(excess_return)`（当たれば正）で集計。
2. **指標別成績**: `hit_rate_i = P(contribution_i > 0)`、引用回数 `n_i`。
3. **更新則（指数移動・有界）**: `multiplier_i ← clip( multiplier_i × (1 + η·2·(hit_rate_i − 0.5)), 0.25, 2.0 )`、η=0.1。**`n_i < 10` の指標は更新しない**（実効サンプルガードの流儀）。
4. **ゲート**: 有効重み = `DEFAULT_CONFIDENCE_WEIGHT × rank係数(scorecard) × multiplier`。scorecard で D ランク（不採用）の指標は multiplier に関わらず 0。つまり**統計検証（L3/validation）が門番、実績学習（L5）は微調整**という二段構え。
5. `ScoreEngine`（L4）は起動時に `indicator_weights.csv` があれば重みを上書き読み込み（config.py docstring に「validation の結果で後段が上書きできる」と既に明記されている接続点）。

#### (d) 分析出力

- `outputs/prediction_accuracy.csv`: 指標別・テーマ別・ホライゾン別の勝率/平均超過リターンランキング（既存 `summarize_backtests` の拡張）。
- 「効いた指標/ノイズだった指標」= hit_rate と avg_excess の2軸表を L10 レポートに1章追加。
- テーマ別勝率集計: `GROUP BY theme, horizon`。

#### (e) 移行

既存 `outputs/notifications/backtests.jsonl`（1w/1m/3m）は通知検証としてそのまま残し、L5 は判断(DecisionRecord)単位の新台帳で開始。将来統合可能なよう `Backtest` と `evaluations` のカラム名を揃える。

### 4.7 Layer6: Risk Engine（`src/risk/`）

`collapse_watch.py` の `WatchItem`（name/deteriorated/value_note/available）パターンを「テーマ×リスクカテゴリ」行列へ一般化:

```
risk/detectors.py: カテゴリ別検知器（統一シグネチャ detect(theme, ctx) -> RiskItem）
  - regulation      : materials の政策/規制トピック（source_rank A/B のみ判断に使用 — 既存 can_affect_decision 流用）
  - tech_defeat     : 競合指標の相対モメンタム（例: 量子 = 保有proxy vs IONQ/IBM の相対パフォーマンス）
  - dilution        : EDINET/EDGAR の増資・新株予約権 filing 検知（materials トピック分類に追加）
  - competition_loss: ピアバスケット相対シェア（peer_basket_excluding 再利用）
  - capex_cut       : 既存 collapse_watch._check_hyperscaler_capex を移設
  - customer_churn  : materials の顧客関連イベント（estimated）

出力 outputs/risk_scores.csv:
  theme, target, category, risk_score(0-100), deteriorated(bool), evidence, data_quality, as_of
  + テーマ集約 risk_level(0-3)   # collapse_level の一般化
```

売却判定への接続: L2 の bear シナリオ条件に `risk.category` を参照する条件型を追加（例: `risk: dilution >= 60 → 一部利確条件`）。risk_score も L5 に予測として記帳する（下落予測の検証）。

### 4.8 Layer7/8: Discovery（`src/discovery/`）

**L7 companies.py**: ユニバース = `instruments.csv` の `held=False` 銘柄（既に15銘柄ある）+ 追加候補。各銘柄に (1) 所属テーマの theme_score、(2) テーマ内相対モメンタム/出来高、(3) philosophy.yaml フィルタ（ボトルネック性 = 手動タグ）、(4) リスク を合成し `outputs/discovery_companies.csv`（company, theme, thesis, expected_value, risks, current_position, rank）を出力。thesis/expected_value は当面手動タグ+テンプレート文。

**L8 themes.py**: 入力 = materials のテーマ別出現件数トレンド（新テーマは `themes.csv` に `status=watch` で登録）+ `config/rss_sources.csv` へ arXiv・政府予算・VCニュースのRSS追加 + 手動入力（`data/materials_manual/pending.csv` の既存経路）。出力 `outputs/discovery_themes.csv`（theme_name, tam_estimate, growth_rate, feasibility, candidates, time_horizon, data_quality）。**定量データが薄い領域なので estimated/manual 中心と正直に明示**。

### 4.9 Layer9: Capital Allocation（`src/allocation/engine.py`）

平均分散最適化は入力（期待リターン推定）の信頼度が低いため採用せず、**ルールベース+相関ペナルティ**で開始:

```
入力: theme_scores.csv, risk_scores.csv(テーマ集約), 相関行列(price parquetの90日リターンから算出),
      config/allocation_policy.yaml (min/max配分, 現金下限, リバランス閾値)
手順: raw_i = theme_score_i × (1 − risk_haircut_i)
      → 相関の高いテーマペアに集中ペナルティ → min/max クリップ → 正規化(現金枠を残す)
出力 outputs/allocation.csv:
  theme, recommended_pct, current_pct(※要ユーザー入力), diff_pct, rationale, confidence, as_of
```

現在配分は `config/holdings.csv`（銘柄、株数 or 金額、テーマ）をユーザーが手動保守する前提（証券口座連携はスコープ外）。

### 4.10 Layer10: Report Template Engine（`src/reporting/` 拡張）

- **入力契約**: `outputs/*.csv`, `outputs/decisions/*.jsonl`, `data/predictions/` の読み取り専用。判定モジュール import 禁止を `tests/test_layer_boundaries.py`（import グラフ検査）で機械的に強制。
- **章立てを要件通りに再構成**: ①ヘッダ（レポート日/分析時刻/データ鮮度/信頼度/実測率 — 既存 `_section_data_quality` 拡張）→ ②Early Signal Layer（材料→先行指標→需給→構造変化の因果順、materials と causal_graph から。**ニュース単体の羅列セクションは廃止**し、必ず紐づく指標/判断を併記）→ ③テーマスコア6軸 → ④リスク → ⑤投資判断（DecisionRecord の必須6項目: 成立条件/現在地/成立率/未成立条件/判断理由/変更理由）→ ⑥判断変更ログ+前回差分 → ⑦予測検証成績（L5） → ⑧配分提案 → ⑨発掘ランキング → ⑩最終結論。
- 実装: `daily_report.py` の `_section_*` 関数群のパターンを維持し、セクションを差し替え/追加。dashboard も同様。

---

## 5. 実装フェーズ提案

| フェーズ | 内容 | 理由 |
|---|---|---|
| **P1（最優先・1〜2週目）** | ① `src/registry/` + `config/instruments.csv`・`indicators.csv`・`themes.csv` 外部化（電力テーマ・電力指標追加含む） ② **L5 予測台帳（ledger + pending evaluations 生成 + 日次評価バッチ）** — 現行の outlook/action スナップショットをそのまま記帳する最小版 | **L5 は履歴が資産**。3ヶ月後の答え合わせは今日記帳を始めないと3ヶ月遅れる。ロジックが未完成でも「現行判定を予測として記録」は今すぐ可能。マスタ外部化は全レイヤーの前提 |
| **P2（3〜5週目）** | ① L4 `theme_score.py`（6軸ルーブリック） ② L2 `src/decision/`（scenarios.yaml + DecisionRecord + diff 移設） ③ L10 レポート章立て再構成 | 判断の「型」を先に固めると L5 の記帳内容が豊かになる（根拠指標の凍結）。L10 は L2 の出力スキーマ確定後でないと書けない |
| **P3（6〜8週目）** | ① L6 risk/（collapse_watch 一般化 + materials 連動、`--step 5` の日次組み込み） ② L9 allocation ③ L5 重み自動更新（評価データが貯まり始めた頃に有効化。それまでは multiplier=1 固定で回す） | 重み更新は評価サンプル n≥10 が必要なので後段で自然 |
| **P4（9週目〜）** | L7/L8 discovery、L8 用 RSS ソース拡充、バイオテーマ追加 | 探索系は既存資産への依存が最も大きく、土台完成後が効率的 |

---

## 6. 銘柄マスタ設計（Layer1）

`config/instruments.csv`（`src/registry/instruments.py` が pydantic `Instrument` で検証してロード。既存 `config.INSTRUMENTS` は互換 re-export として残し、参照箇所の書き換えを不要にする）:

```
key, name_ja, name_en, theme(themes.csvのkey), ticker, coingecko_id,
held(bool), held_since(date), data_quality, proxy_key(非上場の代理銘柄key),
philosophy_tags("bottleneck;platform"等), benchmark_key, note
```

- **銘柄追加 = CSV 1行追加**で L3収集（yfinance stem 自動命名）・L4採点・L2判定・L5記帳・L10表示まで自動伝播する、がゴール。
- 非上場（SpaceX/Quantinuum）は既存パターン踏襲: `ticker=None` + `proxy_key`（Quantinuum→honeywell）。SpaceX は proxy 不在のため unavailable のまま、materials（資金調達ニュース）と L5 の評価スキップ（`skipped_no_data`）で扱う。
- 現マスタとの差分注意: ハーモニック・ドライブが held=True で存在（ユーザーの保有リストに無い）、`lasertec_rorze` という key 名が紛らわしい（実体はローツェで正しい）。CSV 化時に key リネームすると parquet ファイル名 (`price_lasertec_rorze.parquet`) との整合が壊れるため、**key は据え置き + display 名で吸収**を推奨。

---

## 7. 実装開始にあたり確認すべき論点

1. **保有銘柄リストの不整合**: ハーモニック・ドライブ（config では held=True）と QNT トークン（held=True）は今回の Layer1 リストに無い。保有継続か外すか。
2. **保有数量・取得単価**: Layer9 の「現在配分との差分」表示には保有金額が必要。`config/holdings.csv` を手動保守する運用でよいか、金額を git に置いてよいか（プライバシー）。
3. **構造変化30点の初期評価方法**: 完全自動化は不可能（materials だけでは弱い）。テーマごとの手動評価（0-30を月次でユーザーが更新、estimated 扱い）＋材料イベントによる自動加減点、というハイブリッドで開始してよいか。
4. **L2→L5 の記帳方式**: push型（decision が ledger を直接呼ぶ）か pull型（L5 が decisions JSONL を読む）か。設計上は pull が疎結合だが記帳漏れリスクがある。推奨は push（本文 §4.3）。
5. **予測評価のベンチマーク**: 超過リターンの基準（日本株=TOPIX? 半導体=SOX? XRP=BTC?）をテーマ別にどう定めるか。`themes.csv` に benchmark_key を持たせる案で進めてよいか。
6. **重み自動更新の適用範囲**: scorecard で D ランク（統計的不採用）の指標は実績が良くても復活させない設計（統計検証が門番）でよいか。
7. **バイオテーマ**: 優先度低とのことだが、themes.csv に `status=watch` で枠だけ作るか、完全に後回しか。
8. **Quantinuum の代理評価**: HON は Quantinuum 比重が小さく代理として弱い。IPO 観測が出た場合のマスタ更新手順（proxy→verified への昇格）を運用ルール化するか。
9. **GitHub Actions（公開Pages）に投資判断・配分を載せてよいか**: 現在ダッシュボードは Pages 公開。L9 配分や保有情報はプライベートリポジトリ/ローカル出力に分離すべきか。
10. **`--step 5`（材料取込）の日次自動化**: README に「実運用確認後に組み込み」とある。L6/L8 は材料が動力源なので、P3 で daily.yml に組み込む前提でよいか。

---

## 8. 確定事項（2026-07-04 ユーザー回答）

| 論点 | 決定 |
|---|---|
| 1. 保有銘柄の不整合 | **ハーモニック・ドライブは保有継続（held=True維持、Layer1リストに追加）**。QNT はトークンではなくティッカー（Quantinuum 相当のエントリ）であり、保有として維持する |
| 2. 保有数量・金額 | **金額は持たない。現在配分は比率(%)のみ手動管理**（`config/holdings.csv` は theme/instrument, current_pct のみ。金額ベースの差分計算はスコープ外） |
| 3. 構造変化30点 | **ハイブリッドで開始**: テーマごとに月次でユーザーが0-30点を手動評価（estimated扱い・confidence明示）+ 材料イベントで自動加減点 |
| 9. 公開範囲 | **配分・売買判断・保有情報は公開Pagesに載せない**。テーマスコア等は公開ダッシュボード可。L9配分・L2判断はローカル/非公開出力（`outputs/private/` 等、Pagesデプロイ対象から除外）に分離 |

以下は設計書の推奨案をそのまま採用（ユーザー異議なし・実装担当はこれに従う）:

| 論点 | 採用する推奨案 |
|---|---|
| 4. L2→L5 記帳方式 | **push型**（decision/engine が prediction/ledger.record_prediction() を直接呼ぶ。記帳漏れ防止優先） |
| 5. 予測評価ベンチマーク | `themes.csv` に `benchmark_key` を持たせテーマ別に定義（日本株=TOPIX、半導体=SOX、XRP=BTC等） |
| 6. 重み自動更新の範囲 | scorecard Dランク指標は実績が良くても復活させない（統計検証が門番、L5は微調整） |
| 7. バイオテーマ | `themes.csv` に `status=watch` で枠のみ作成、指標実装はP4以降 |
| 8. Quantinuum 代理評価 | HON proxy + `benchmark_is_approximate=True`。IPO観測時に proxy→verified へ昇格する運用（マスタCSV更新のみで対応可能な設計とする） |
| 10. step5 日次自動化 | P3 で daily.yml に組み込む |
