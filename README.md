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
```

## 実行方法

```bash
# Step1: データ取得（毎日実行、約1〜2分）
python -m src.main

# テスト
python -m pytest

# Lint / 型チェック
ruff check src tests
mypy src
```

## プロジェクト構成

```
src/
  config.py          # 銘柄・指標・データ品質タクソノミー定義（二重スコアの背骨）
  data_sources/      # Step1: データ取得 (yfinance/coingecko/xrpl/defillama/fred)
  features/          # Step2: 特徴量生成 (YoY/MoM/MA/Zスコア)
  validation/        # Step2: ラグ相関・イベントスタディ・有効性ランク
  scoring/           # Step3: Hard/Extendedスコア・XRP実需・ロック需要スコア
  reporting/         # Step4: daily_report.md 生成
  dashboard/         # Step4: plotly + PWA (GitHub Pages公開)
data/raw/            # 生データスナップショット (JSON, タイムスタンプ付き)
data/processed/      # 処理済みデータ (Parquet)
outputs/             # レポート・CSV・グラフ
tests/               # pytest テスト
.github/workflows/   # GitHub Actions (毎日自動実行 → Pages公開)
methodology.md       # 統計設計の詳細（ラグ相関・多重検定・ランク基準）
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

詳細な統計手法は [methodology.md](methodology.md) を参照。
