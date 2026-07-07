# 先行指標監視システム

ニュースになる前に株価・価格変動の初動を検知する先行指標監視システム。

## 思想

単なるニュース監視ではなく、**ニュース化される前に効き始める先行指標**を検知する。  
推測でスコアを「断定」しない。データが取れない指標は「取得不可/信頼度低」と正直に明示する。

**二重スコア設計:**
- 🟢 **Hardスコア** … `verified`（無料APIで直接取得）指標のみで算出
- 🟡 **Extendedスコア** … `proxy`（代理）/`estimated`（推定）も信頼度重み付きで加算し、confidence% を明示

## セットアップ

```bash
pip install -r requirements.txt
```

### 環境変数（任意・設定すると指標が増える）

```bash
# FRED APIキー（無料: https://fred.stlouisfed.org/docs/api/api_key.html）
$env:FRED_API_KEY = "your_key_here"

# CoinGecko Demo APIキー（無料: https://www.coingecko.com/en/api）
$env:COINGECKO_API_KEY = "your_key_here"

# SEC EDGAR Fair Accessポリシー用の連絡先付きUser-Agent（Step5で使用）
$env:SEC_EDGAR_USER_AGENT = "kabu-shihyo-tool your-email@example.com"

# EDINET（日本の開示システム）APIキー（無料: https://api.edinet-fsa.go.jp で登録）
# 保有銘柄の大半（フジクラ/ローツェ/キオクシア等）は日本上場のためSEC EDGARでは
# カバーできず、これが実質的な材料取得源になる（動作確認済み。詳細: config/README.md）
$env:EDINET_API_KEY = "your_key_here"
```

## 実行方法

```bash
# Step1: データ取得（毎日実行、約1〜2分）
python -m src.main

# Step5: 材料取込（SEC EDGAR + RSS + 手動入力。--step all には未含有）
python -m src.main --step 5

# Step7: 予測台帳（Investment OS Layer5。--step all に含む）
python -m src.main --step 7

# Step8: テーマスコアリング（Investment OS Layer4、6軸。--step all に含む）
python -m src.main --step 8

# Step9: 意思決定エンジン（Investment OS Layer2。非公開出力のため --step all には未含有）
python -m src.main --step 9

# Step10: リスクエンジン（Investment OS Layer6、下落検知。--step all に含む）
python -m src.main --step 10

# Step11: 資金配分エンジン（Investment OS Layer9。非公開出力のため --step all には未含有）
python -m src.main --step 11

# Step12: 新規発掘エンジン（Investment OS Layer7/8。保有銘柄を含まないため公開。--step all に含む）
python -m src.main --step 12

# テスト
python -m pytest

# Lint / 型チェック
ruff check src tests
mypy src
```

## プロジェクト構成

```
src/
  config.py          # 銘柄・指標・データ品質タクソノミー定義（二重スコアの背骨。
                      # INSTRUMENTS/INDICATORSはregistry経由でCSVから読み込み）
  registry/          # 銘柄・指標・テーママスタのCSVローダー(Investment OS Layer1/Layer3)
  data_sources/      # Step1: データ取得 (yfinance/coingecko/xrpl/defillama/fred)
  features/          # Step2: 特徴量生成 (YoY/MoM/MA/Zスコア)
  validation/        # Step2: ラグ相関・イベントスタディ・有効性ランク
  scoring/           # Step3: Hard/Extendedスコア・XRP実需・ロック需要スコア
  reporting/         # Step4: daily_report.md(公開) / Step9: decision_report.md(非公開)
  dashboard/         # Step4: plotly + PWA (GitHub Pages公開。集計指標のみ)
  materials/         # Step5: 材料ID・重複検知・鮮度・ソースランク・因果グラフ(Phase5基盤)
  prediction/        # Step7: 予測台帳・事後評価・指標重み自動更新(Investment OS Layer5、最重要レイヤー)
  decision/          # Step9: 意思決定エンジン(Investment OS Layer2、シナリオ判定。非公開)
  risk/              # Step10: リスクエンジン(Investment OS Layer6、下落検知)
  allocation/        # Step11: 資金配分エンジン(Investment OS Layer9。非公開)
  discovery/         # Step12: 新規発掘エンジン(Investment OS Layer7/8。保有銘柄を含まないため公開)
data/raw/            # 生データスナップショット (JSON, タイムスタンプ付き)
data/processed/      # 処理済みデータ (Parquet)
data/materials/      # 材料DB正本 (JSONL。SQLiteキャッシュはdata/materials.db、gitignore対象)
data/materials_manual/pending.csv  # Reuters/Bloomberg等(source_rank=B)の手動投入用
config/instruments.csv・indicators.csv・themes.csv  # 銘柄・指標・テーママスタ(CSVが正本)
config/structural_scores.csv       # テーマ構造変化スコアの手動評価(月次入力、空でコミット)
config/scenarios/*.yaml            # Layer2シナリオ条件定義(config/scenarios/generate.pyで再生成可)
config/allocation_policy.yaml      # Layer9配分ポリシー(上限/下限%・現金下限・相関ペナルティ)
config/holdings.example.csv        # Layer9現在配分の入力テンプレート(実データはprivate/holdings.csv)
config/rss_sources.csv             # 企業IR・政府機関RSSの検証済みURL登録(空でコミット)
outputs/             # レポート・CSV・グラフ(公開、GitHub Pagesへデプロイ。集計指標のみ)
private/             # 保有銘柄ごとの投資判断・予測台帳・通知等(gitignore対象、§8確定事項)。
                      # 専用リポジトリ daisei0810-sudo/kabu-shihyo-private で永続化(下記参照)
tests/               # pytest テスト
.github/workflows/   # GitHub Actions (毎日自動実行 → Pages公開)
methodology.md       # 統計設計の詳細（ラグ相関・多重検定・ランク基準）
docs/investment_os_design.md  # Investment OS(Layer0〜10)全体設計・実装フェーズ計画
```

## データソースと品質

| バッジ | 品質 | 内容 | スコアでの扱い |
|-------|------|------|---------------|
| 🟢 | `verified` | 無料APIで直接取得 | Hard / Extended 両方 |
| 🟡 | `proxy` | 代理指標（関連株価等） | Extended のみ |
| 🟠 | `estimated` | イベント推定 | Extended のみ |
| ⚪ | `unavailable` | 無料では取得不可 | 表示のみ（スコア非算入） |

### 取得中の指標（Step1完了時点）

**株価 (yfinance・verified)**  
フジクラ・ローツェ・ファナック・安川電機・村田製作所・ハーモニック・ドライブ・キオクシア・Tesla・NVIDIA + SOX/SMH/SOXX

**暗号資産**  
XRP / QNT価格 (yfinance), CoinGecko現在値スナップショット

**CAPEX (yfinance・verified・四半期)**  
NVDA / MSFT / GOOGL / AMZN / META / TSM (Hyperscaler合算含む)

**XRPL オンチェーン**  
XRPL Ledger stats (XRPScan), RLUSD supply snapshot (⚠️ アドレス要確認)

**DeFi TVL (DefiLlama・verified)**  
XRPL DeFi TVL (828日分), XRPL Stablecoin TVL (453日分)

**マクロ (FRED)**  
→ `FRED_API_KEY` をセットすると ISM PMI・鉱工業生産・耐久財受注・FFレート等を取得

### 取得不可（⚪ unavailable）

SpaceX評価額・HBM現物価格・CoWoS稼働率・BBレシオ生値・取引所XRP残高・クジラウォレット・Institutional DeFi・RWA担保等

## Opus設計事項の解決状況（2026-06-29）

1. ✅ **RLUSD発行体アドレス** = `rMxCKbEDwqr76QuheSUMdEGf4B9xJ8m5De`（オンチェーン確定。
   gateway_balancesで発行残高≈8.1億、Domain=ripple.com を確認）
2. ✅ **XRP/RLUSD AMM** = 通貨コードを40桁hex(`524C5553440...`)で符号化して解決。
   AMM内XRP残高≈190万XRP（ロック需要スコアの中核データ）を取得可能に
3. ⚠️ **XRPL 日次Tx統計の歴史データ** = 無料では取得不可。日次スナップショットを
   蓄積して時系列化する方針（GitHub Actionsで毎日1点ずつ追記）

### 検証で判明した重要な統計的事実（Step2）
利用可能な無料データ（1〜5年の履歴）では、**長期ラグ(120-180日)予測でA/Bランクに
達する指標はゼロ**。当初A判定だった `amm_tvl→XRP` は見せかけ相関（レベル相関0.86／
変化率相関0.04、単一強気相場の共通トレンド）と判明しCへ降格。詳細は methodology.md §2。

## Step ロードマップ

- [x] **Step0**: 基盤（ディレクトリ・config・型定義）
- [x] **Step1**: データ取得（yfinance/coingecko/xrpl/defillama/fred）
- [x] **Step2**: 先行指標の有効性検証（見せかけ相関対策・非重複窓・実効サンプルガード）
- [x] **Step3**: Early Signal Layer 確定・Hard/Extended スコアリング・XRP実需/ロック需要スコア
- [x] **Step4**: daily_report.md・Plotly PWA ダッシュボード・GitHub Actions 毎日自動更新
- [x] **Step5**: 材料取込基盤（SEC EDGAR全文検索 + RSS + 手動入力 → material_id・重複検知・
  鮮度スコア・ソースランク → `data/materials/*.jsonl`）。`--step all` には未含有（日次自動実行への
  組み込みは実運用確認後）
- [x] 押し目・売り時判定（簡易版・暫定）: 既存テクニカル指標+Hard/Extendedスコアのみで近似。
  材料データ反映後に本実装へ置き換え予定
- [x] **Step7**: 予測台帳（Investment OS Layer5、最重要レイヤー）— 日々の投資判断を記帳し、
  3/6/12ヶ月後に実際の株価で答え合わせする。現状はStep3のoutlook/actionをそのまま記帳する
  最小版（`--step all` に含む）。詳細: `docs/investment_os_design.md`
- [x] **Step8**: テーマスコアリング（Investment OS Layer4）— 構造変化30/需給25/業績20/
  バリュエーション10/資金流入10/政策追い風5の6軸ルーブリックで各テーマを採点
  （`--step all` に含む、`outputs/theme_scores.csv`）
- [x] **Step9**: 意思決定エンジン（Investment OS Layer2）— `config/scenarios/*.yaml` の
  強気/中立/弱気シナリオを実データで評価し、成立条件・現在地・成立率・未成立条件・
  判断理由・変更理由を備えたDecisionRecordを生成。売買判断そのものは既存の
  スコアベース判定（実績あり）を変えず、L2語彙への変換と開示情報の付加のみ行う。
  出力は保有情報を含むため非公開（`private/decision_report.md`）
- [x] **Step10**: リスクエンジン（Investment OS Layer6、下落検知）— 保有銘柄ごとに
  regulation/tech_defeat/dilution/competition_loss/capex_cut/customer_churnの
  6カテゴリを評価。個別銘柄の悪化理由は非公開（`private/risk_scores.csv`）、
  テーマ集約のrisk_level(0-3)のみ公開（`outputs/risk_level_by_theme.csv`）。
  L2のbearシナリオ条件（`risk:<category>`形式）にも統合済み（`--step all` に含む）
- [x] **Step11**: 資金配分エンジン（Investment OS Layer9）— `raw_i = theme_score_i ×
  (1 − risk_haircut_i)` を相関の高いテーマペアにペナルティを掛けてから
  min/max配分にクリップ・正規化（平均分散最適化は入力の信頼度が低いため不採用）。
  推奨配分・現在配分（`private/holdings.csv`から）・差分はいずれも保有資産構成を
  示すため非公開（`private/allocation.csv`、`--step all` には未含有）
- [x] **指標重み自動更新**（Investment OS Layer5）— 評価済み予測の的中率から
  `indicator_weights.csv`のlearned_multiplierを指数移動・有界(0.25-2.0)で更新
  （評価サンプルn<10は更新しない実効サンプルガード）。ScoreEngineが起動時に
  自動読み込みし既存のHard/Extendedスコアへ反映（Step7に統合、`outputs/indicator_weights.csv`
  は保有銘柄情報を含まないため公開。現状は評価サンプルがほぼ0件のため実質no-op、
  データが貯まり次第自動的に効き始める）
- [x] **Step12**: 新規発掘エンジン（Investment OS Layer7/8）— L7は非保有銘柄
  （`held=False`）をテーマスコア×テーマ内相対モメンタム（自分の直近65営業日騰落率 −
  同テーマ平均）でランキング。`expected_value`は個社期待値モデルが未整備のため
  現状はtheme_scoreをそのまま採用（捏造回避）。L8は`themes.csv`の`status=watch`
  テーマ（現状バイオのみ）をmaterialsのキーワード出現件数トレンドで追跡（TAM・成長率は
  無料データソースがないため「未整備」と正直に返す）。いずれも保有銘柄の判断を
  含まないため公開（`outputs/discovery_companies.csv`, `outputs/discovery_themes.csv`、
  `--step all` に含む）

## 非公開データ（`private/`）の運用について

保有銘柄ごとのスコア・投資判断・予測台帳・通知・押し目売り時判定・リスク詳細・
資金配分は売買判断そのものであり、本リポジトリが public かつ GitHub Pages で
公開されるため、`private/`（gitignore対象）に分離している
（`portfolio_signal_scores.csv`・`technical_scores.csv`・`dip_sell_scores.csv`・
`decisions/`・`predictions/`・`notifications/`・`history/decisions/`・
`risk_scores.csv`・`allocation.csv`・`holdings.csv`・`decision_report.md`）。
公開される `outputs/` には集計指標（サイクルスコア・実需指数・テーマスコア・
XRP集計スコア・risk_level_by_theme・indicator_weights等、個別銘柄の判断を
含まない）のみを出力する。

`private/` は日次自動実行では専用の非公開リポジトリ
[daisei0810-sudo/kabu-shihyo-private](https://github.com/daisei0810-sudo/kabu-shihyo-private)
へ自動コミット・pushされる（`.github/workflows/daily.yml`）。有効化するには
GitHub Secretsに `PRIVATE_REPO_PAT`（上記リポジトリへの書き込み権限を持つ
Personal Access Token）を登録すること。未設定の場合、`private/`は各CI実行内の
揮発ディレクトリとして動作し、判断変更ログ等の「前回比較」機能はその回だけ
無効になるが、処理自体はクラッシュしない。ローカル手動実行では常に正常に蓄積される。

## Investment OS 化について

本ツールは「先行指標監視システム」から、AI/Physical AI/量子/電力/XRPなど巨大投資
サイクルの先行検知〜資金配分〜売買判断までを支援する「投資OS」へ拡張中。
全体構想・レイヤー設計・実装フェーズ計画は [docs/investment_os_design.md](docs/investment_os_design.md)
を参照。

詳細な統計手法は [methodology.md](methodology.md) を参照。
