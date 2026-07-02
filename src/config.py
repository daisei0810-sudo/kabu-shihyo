"""中央設定 — 二重スコア(Hard/Extended)の背骨となるデータ品質タクソノミー、
銘柄ユニバース、レイヤー定義、指標カタログを定義する。

設計原則:
  - すべての指標は DataQuality を必ず持つ。verified 以外は信頼度重み(confidence_weight)を掛ける。
  - 推測でスコアを「断定」しない。unavailable は weight=0.0 で「取得不可」として表示のみ。
  - 重みの初期値はここで定義し、validation の結果(有効性ランク)で後段が上書きできる。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# データ品質タクソノミー
# ---------------------------------------------------------------------------
class DataQuality(str, Enum):
    """指標の取得品質。Hard/Extended スコアの算入可否と信頼度重みを規定する。"""

    VERIFIED = "verified"        # 無料APIで一次データを直接取得 → Hard/Extended 両方
    PROXY = "proxy"              # 関連株価/ETF等で代理 → Extended のみ
    ESTIMATED = "estimated"      # ニュース/イベント頻度からの推定 → Extended のみ
    UNAVAILABLE = "unavailable"  # 無料では取得不可 → スコアに使わず「取得不可」表示のみ


# data_quality → 信頼度重みの初期値(0.0-1.0)。validation 後にランクで調整される。
DEFAULT_CONFIDENCE_WEIGHT: dict[DataQuality, float] = {
    DataQuality.VERIFIED: 1.0,
    DataQuality.PROXY: 0.5,
    DataQuality.ESTIMATED: 0.3,
    DataQuality.UNAVAILABLE: 0.0,
}

# ダッシュボード表示用バッジ
QUALITY_BADGE: dict[DataQuality, str] = {
    DataQuality.VERIFIED: "🟢",
    DataQuality.PROXY: "🟡",
    DataQuality.ESTIMATED: "🟠",
    DataQuality.UNAVAILABLE: "⚪",
}


class Layer(str, Enum):
    """先行指標を束ねるテーマ・レイヤー。"""

    CRYPTO_XRP = "crypto_xrp"                 # 1. 暗号資産 / XRP・XRPL
    AI_DATACENTER = "ai_datacenter"           # 2. AIデータセンター / 光通信
    SEMICAP = "semicap"                       # 3. 半導体装置 / HBM / CoWoS / WFE
    ROBOTICS_FA = "robotics_fa"               # 4. ロボティクス / FA / 減速機 / サーボ
    EV_PHYSICAL_AI = "ev_physical_ai"         # 5. EV / Physical AI / Tesla / Optimus
    QUANTUM = "quantum"                       # 6. 量子コンピュータ
    CHINA_AI = "china_ai"                     # 7. 中国AI・Physical AI
    POLICY = "policy"                         # 8. 政策・補助金・輸出規制・IPO


class DataSource(str, Enum):
    """無料データソース。"""

    YFINANCE = "yfinance"
    COINGECKO = "coingecko"
    XRPL = "xrpl"            # 公開rippledノード / XRPScan
    DEFILLAMA = "defillama"
    FRED = "fred"
    NEWS = "news"           # イベント推定用(estimated)
    NONE = "none"           # unavailable


# ---------------------------------------------------------------------------
# 銘柄(資産)ユニバース
# ---------------------------------------------------------------------------
class Instrument(BaseModel):
    """価格時系列を持つ監視対象資産。"""

    key: str                       # システム内部キー
    name_ja: str                   # 表示名(日本語)
    layer: Layer
    ticker: str | None = None      # yfinance ティッカー (Noneなら価格取得不可)
    coingecko_id: str | None = None
    held: bool = False             # ユーザー保有銘柄か
    data_quality: DataQuality = DataQuality.VERIFIED
    note: str = ""


# 保有銘柄 + レイヤー代表/proxy銘柄。
# held=True がユーザー保有。価格取得不可の私企業は ticker=None, UNAVAILABLE。
INSTRUMENTS: list[Instrument] = [
    # --- Crypto / XRP ---
    Instrument(key="xrp", name_ja="XRP", layer=Layer.CRYPTO_XRP,
               ticker="XRP-USD", coingecko_id="ripple", held=True),
    Instrument(key="qnt_token", name_ja="Quant Network (QNT トークン)", layer=Layer.CRYPTO_XRP,
               ticker="QNT-USD", coingecko_id="quant-network", held=True,
               note="※暗号資産。量子計算企業 Quantinuum とは別物。要ユーザー確認。"
                    " Step2検証対象外(2026-07-02判断): XRPのような固有オンチェーン先行指標"
                    "(RLUSD供給等)がQNTには存在せず、使えるのは価格系列のみで"
                    "『価格で価格を予測』の自己相関になるためドメイン論理が無い。"),
    # --- AI データセンター / 光通信 ---
    Instrument(key="fujikura", name_ja="フジクラ", layer=Layer.AI_DATACENTER,
               ticker="5803.T", held=True),
    Instrument(key="murata", name_ja="村田製作所", layer=Layer.AI_DATACENTER,
               ticker="6981.T", held=True),
    Instrument(key="sumitomo_electric", name_ja="住友電工", layer=Layer.AI_DATACENTER,
               ticker="5802.T"),
    Instrument(key="furukawa_electric", name_ja="古河電工", layer=Layer.AI_DATACENTER,
               ticker="5801.T"),
    Instrument(key="nvidia", name_ja="NVIDIA", layer=Layer.AI_DATACENTER, ticker="NVDA"),
    # --- 半導体装置 / HBM / CoWoS ---
    Instrument(key="lasertec_rorze", name_ja="ローツェ", layer=Layer.SEMICAP,
               ticker="6323.T", held=True),
    Instrument(key="kioxia", name_ja="キオクシア", layer=Layer.SEMICAP,
               ticker="285A.T", held=True),
    Instrument(key="advantest", name_ja="アドバンテスト", layer=Layer.SEMICAP, ticker="6857.T"),
    Instrument(key="towa", name_ja="TOWA", layer=Layer.SEMICAP, ticker="6315.T"),
    Instrument(key="kokusai_electric", name_ja="KOKUSAI ELECTRIC", layer=Layer.SEMICAP,
               ticker="6525.T"),
    Instrument(key="shibaura", name_ja="芝浦メカトロニクス", layer=Layer.SEMICAP, ticker="6590.T"),
    # --- ロボティクス / FA ---
    Instrument(key="harmonic", name_ja="ハーモニック・ドライブ・システムズ",
               layer=Layer.ROBOTICS_FA, ticker="6324.T", held=True),
    Instrument(key="fanuc", name_ja="ファナック", layer=Layer.ROBOTICS_FA,
               ticker="6954.T", held=True),
    Instrument(key="yaskawa", name_ja="安川電機", layer=Layer.ROBOTICS_FA,
               ticker="6506.T", held=True),
    Instrument(key="nabtesco", name_ja="ナブテスコ", layer=Layer.ROBOTICS_FA, ticker="6268.T"),
    # --- EV / Physical AI ---
    Instrument(key="tesla", name_ja="Tesla", layer=Layer.EV_PHYSICAL_AI,
               ticker="TSLA", held=True),
    Instrument(key="spacex", name_ja="SpaceX", layer=Layer.EV_PHYSICAL_AI,
               ticker=None, held=True, data_quality=DataQuality.UNAVAILABLE,
               note="非上場。評価額/資金調達/ロックアップの無料時系列なし → 取得不可。"),
    # --- 量子 ---
    Instrument(key="quantinuum", name_ja="Quantinuum", layer=Layer.QUANTUM,
               ticker=None, held=True, data_quality=DataQuality.PROXY,
               note="非上場。親会社 Honeywell(HON) を代理指標とする。"),
    Instrument(key="honeywell", name_ja="Honeywell (Quantinuum 代理)", layer=Layer.QUANTUM,
               ticker="HON", data_quality=DataQuality.PROXY),
    Instrument(key="ionq", name_ja="IonQ", layer=Layer.QUANTUM, ticker="IONQ"),
    Instrument(key="dwave", name_ja="D-Wave", layer=Layer.QUANTUM, ticker="QBTS"),
    Instrument(key="rigetti", name_ja="Rigetti", layer=Layer.QUANTUM, ticker="RGTI"),
    Instrument(key="ibm", name_ja="IBM", layer=Layer.QUANTUM, ticker="IBM"),
]

# proxy/指数バスケット(個別株でない先行指標用)
INDEX_TICKERS: dict[str, str] = {
    "sox": "^SOX",     # フィラデルフィア半導体指数
    "smh": "SMH",      # 半導体ETF
    "soxx": "SOXX",
    "vix": "^VIX",     # 恐怖指数 (市場リスク判定)
    "usdjpy": "JPY=X", # ドル円 (日本株グロースのバリュエーション)
    "us10y": "^TNX",   # 米10年金利 (割引率)
}


# ---------------------------------------------------------------------------
# 先行指標カタログ
# ---------------------------------------------------------------------------
class Indicator(BaseModel):
    """検証・スコアリング対象の先行指標。

    Step2(validation/run_validation.py)のデータ読み込みをデータ駆動にするための
    メタデータ(parquet_stem/column/loader/step2_verifiable/freq)を持つ。
    単純なparquet読み込みは parquet_stem+column、ピアバスケット等の動的生成は
    loader で名前解決する(run_validation.py の SPECIAL_LOADERS 参照)。
    """

    key: str
    name_ja: str
    layer: Layer
    source: DataSource
    data_quality: DataQuality
    # この指標が効くと想定する対象資産(検証で確かめる)
    targets: list[str] = Field(default_factory=list)
    note: str = ""

    # --- Step2ローダー用メタデータ ---
    parquet_stem: str | None = None      # 例 "price_index_sox"(拡張子・ディレクトリ抜き)
    column: str | None = None            # 例 "Close"
    loader: str | None = None            # 例 "peer_basket:optical"(特殊ローダー名)
    step2_verifiable: bool = True        # False なら Step2の統計検証をスキップ(四半期capex等)
    freq: str = "daily"                  # "daily" | "monthly"(月次は日次へffill変換)

    @property
    def confidence_weight(self) -> float:
        """data_quality 由来の初期信頼度重み。validation 後にランクで上書きされうる。"""
        return DEFAULT_CONFIDENCE_WEIGHT[self.data_quality]


# MVP(Step1)= Crypto/XRP・AIデータセンター・半導体装置の3領域を中心に定義していたが、
# Step2改善(2026-07-02)でrobotics_fa/ev_physical_ai/quantumへドメイン論理のある
# 指標のみ慎重に拡張。ドメイン論理が説明できない指標(価格の自己相関になるもの)は
# 追加しない方針を徹底している(詳細はINDICATORS末尾のコメント参照)。
INDICATORS: list[Indicator] = [
    # ===== XRP / XRPL =====
    Indicator(key="xrp_price", name_ja="XRP価格", layer=Layer.CRYPTO_XRP,
              source=DataSource.COINGECKO, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              parquet_stem="price_xrp", column="Close"),
    Indicator(key="rlusd_supply", name_ja="RLUSD発行残高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="RLUSD発行体の gateway_balances(hexキー)。発行体は確定済み。"
                   "日次1点ずつ蓄積中のため現状は30行未満でStep2は自動スキップされる。",
              parquet_stem="xrpl_rlusd_supply", column="rlusd_supply"),
    Indicator(key="xrpl_tx_count", name_ja="XRPLトランザクション数", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="日次1点ずつ蓄積中のため現状は30行未満でStep2は自動スキップされる。",
              parquet_stem="xrpl_network_stats", column="txn_count"),
    Indicator(key="xrpl_success_payments", name_ja="XRP成功Payment数", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="専用fetcher未実装(取得元データなし)。"),
    Indicator(key="amm_tvl", name_ja="AMM TVL", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              parquet_stem="defillama_xrpl_tvl", column="tvl_usd"),
    Indicator(key="amm_xrp_balance", name_ja="AMM内XRP残高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="ロック需要スコアの中核(amm_info の XRP側残高)。"
                   "日次1点ずつ蓄積中のため現状は30行未満でStep2は自動スキップされる。",
              parquet_stem="xrpl_amm_XRP_RLUSD", column="xrp_balance"),
    Indicator(key="xrpl_dex_volume", name_ja="XRPL DEX出来高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="専用fetcher未実装(取得元データなし)。"),
    Indicator(key="xrp_pair_volume", name_ja="XRP建てペア出来高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="専用fetcher未実装(取得元データなし)。"),
    Indicator(key="stablecoin_tvl", name_ja="Stablecoin TVL", layer=Layer.CRYPTO_XRP,
              source=DataSource.DEFILLAMA, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              parquet_stem="defillama_stablecoin_tvl", column="stablecoin_tvl_usd"),
    Indicator(key="etf_flows", name_ja="ETF資金流入", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"],
              note="日次クリーンな無料APIなし。ニュース推定(estimated)に降格可。"),
    Indicator(key="exchange_xrp_balance", name_ja="取引所XRP残高", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"],
              note="ラベル付き網羅データが無料で揃わない → 取得不可。"),
    Indicator(key="whale_wallets", name_ja="クジラウォレット動向", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"]),
    Indicator(key="lending_collateral", name_ja="Lending/Collateral利用量", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"]),
    Indicator(key="institutional_defi", name_ja="Institutional DeFi", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"]),
    Indicator(key="permissioned_dex", name_ja="Permissioned DEX", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"]),
    Indicator(key="rwa_collateral", name_ja="RWA担保利用", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"]),
    Indicator(key="mmf_tokenization", name_ja="MMF/Treasury Tokenization", layer=Layer.CRYPTO_XRP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE, targets=["xrp"]),

    # ===== AI データセンター / 光通信 =====
    # nvidia_revenue/hyperscaler_capexは四半期5行のみ。7ラグ×6ホライゾン=42通りの相関を
    # 5行から統計的に主張するのは完全な過適合(実効独立サンプルガード<10に必ず抵触)。
    # Step2の統計的検証対象からは正式に除外し(step2_verifiable=False)、
    # Step3のExtendedスコア(capex_trend.py)専用データとして扱う。
    Indicator(key="nvidia_revenue", name_ja="NVIDIA売上・ガイダンス", layer=Layer.AI_DATACENTER,
              source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
              targets=["nvidia", "fujikura"],
              note="四半期5行のみ→Step2統計検証は不可能(過適合)。Extendedスコア専用。",
              parquet_stem="capex_nvda", column="capex", step2_verifiable=False),
    Indicator(key="hyperscaler_capex", name_ja="Hyperscaler CAPEX", layer=Layer.AI_DATACENTER,
              source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
              targets=["fujikura", "murata"],
              note="MSFT/GOOGL/AMZN/META capex 合算。四半期5行のみ→Step2統計検証は不可能"
                   "(過適合)。Extendedスコア専用。",
              parquet_stem="capex_hyperscaler_total", column="hyperscaler_capex_total",
              step2_verifiable=False),
    Indicator(key="optical_module_demand", name_ja="光モジュール需要", layer=Layer.AI_DATACENTER,
              source=DataSource.YFINANCE, data_quality=DataQuality.PROXY,
              targets=["fujikura", "murata"],
              note="フジクラ/住友/古河/村田の株価バスケットで代理(自己除外)。"
                   "murataはhyperscaler_capexが検証不可のため、これが唯一の"
                   "Step2検証可能な先行指標候補(ただしproxy=価格自己相関の側面あり)。",
              loader="peer_basket:optical"),
    Indicator(key="optical_price_leadtime", name_ja="光トランシーバー価格・納期",
              layer=Layer.AI_DATACENTER, source=DataSource.NONE,
              data_quality=DataQuality.UNAVAILABLE, targets=["fujikura"]),

    # ===== 半導体装置 / HBM / CoWoS / WFE =====
    Indicator(key="sox_index", name_ja="SOX指数(WFEサイクル代理)", layer=Layer.SEMICAP,
              source=DataSource.YFINANCE, data_quality=DataQuality.PROXY,
              targets=["lasertec_rorze", "kioxia"],
              note="kioxiaは価格履歴373行(2024-12上場)と短く、長ラグは実効サンプルガードで"
                   "C降格の見込み(履歴不足を隠さず表示)。",
              parquet_stem="price_index_sox", column="Close"),
    Indicator(key="tsmc_capex", name_ja="TSMC CAPEX", layer=Layer.SEMICAP,
              source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
              targets=["lasertec_rorze"],
              note="四半期5行のみ→Step2統計検証は不可能(過適合)。Extendedスコア専用。",
              parquet_stem="capex_tsm", column="capex", step2_verifiable=False),
    Indicator(key="hbm_price", name_ja="HBM価格・需給", layer=Layer.SEMICAP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["kioxia"]),
    Indicator(key="cowos_utilization", name_ja="CoWoS稼働率", layer=Layer.SEMICAP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["lasertec_rorze"]),
    Indicator(key="bb_ratio", name_ja="半導体装置 BBレシオ", layer=Layer.SEMICAP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["lasertec_rorze"], note="SEMI BBレシオ生値は無料API無し。"),

    # ===== ロボティクス / FA (2026-07-02 Step2拡張で新規追加) =====
    # 産業用ロボット・FA需要の本質は製造業設備投資サイクル。ドメイン論理の強さ順に:
    # ISM PMI(教科書的先行指標だがFRED_API_KEY必要) > 為替(輸出採算、同時性強め)
    # > ピアバスケット(価格自己相関の側面あり、補助指標C想定)。
    # 中国製造業PMI(本来最重要)は無料クリーン時系列が無く取得不可として明示するのみ。
    Indicator(key="robotics_peer_basket", name_ja="ロボティクス・ピアバスケット(自己除外)",
              layer=Layer.ROBOTICS_FA, source=DataSource.YFINANCE,
              data_quality=DataQuality.PROXY, targets=["harmonic", "fanuc", "yaskawa"],
              note="対象自身を除いたharmonic/fanuc/yaskawa/nabtescoの株価バスケット。"
                   "セクター共通因子が先行しうるか検証するが、価格自己相関の側面が強く"
                   "B以上は期待薄(C想定)。",
              loader="peer_basket:robotics"),
    Indicator(key="usdjpy_level", name_ja="ドル円(輸出採算)", layer=Layer.ROBOTICS_FA,
              source=DataSource.YFINANCE, data_quality=DataQuality.PROXY,
              targets=["fanuc", "yaskawa", "harmonic"],
              note="ファナック/安川は輸出比率が高く、円安が採算改善→株価に先行しうる。"
                   "ただし為替は同時性が強く純先行は弱い(C想定)。",
              parquet_stem="price_index_usdjpy", column="Close"),
    Indicator(key="ism_mfg_pmi", name_ja="ISM製造業PMI", layer=Layer.ROBOTICS_FA,
              source=DataSource.FRED, data_quality=DataQuality.VERIFIED,
              targets=["fanuc", "yaskawa", "harmonic"],
              note="設備投資・FA需要の教科書的先行指標(50割れ/回復が数ヶ月先行)。"
                   "FRED_API_KEY未設定時はデータなしで自動スキップ。月次(月60行程度)のため"
                   "日次へffill変換して検証。ドメイン論理は最強だが月次制約でB〜C想定。",
              parquet_stem="fred_ism_mfg_pmi", column="ism_mfg_pmi", freq="monthly"),
    Indicator(key="durable_goods_orders", name_ja="米耐久財受注", layer=Layer.ROBOTICS_FA,
              source=DataSource.FRED, data_quality=DataQuality.VERIFIED,
              targets=["fanuc", "yaskawa"],
              note="耐久財受注(コア資本財)は設備投資の先行指標。ロボット需要は"
                   "耐久財投資の川下需要。FRED_API_KEY未設定時は自動スキップ。月次。",
              parquet_stem="fred_durable_goods_orders", column="durable_goods_orders",
              freq="monthly"),
    Indicator(key="china_mfg_pmi", name_ja="中国製造業PMI(取得不可)", layer=Layer.ROBOTICS_FA,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["fanuc", "yaskawa"],
              note="ファナック・安川の中国売上比率を踏まえると本来最重要の先行指標だが、"
                   "財新(Caixin)中国PMI等の無料クリーン時系列が存在しないため取得不可。"),

    # ===== EV / Physical AI (2026-07-02 Step2拡張で新規追加) =====
    # Teslaの真の先行指標(週次納車推定・中国保険登録台数・FSD採用率)は無料時系列が
    # 存在しない。金利感応度のみドメイン論理があるため1本追加するが、このレイヤーは
    # 「無料データで有効な先行指標をほぼ作れない」というのが正直な結論。
    # NVIDIA/SOX/VIXをTesla proxyにすることは検討したが、業種テーマ連想による
    # 株価間相関(自己相関)であり需要の先行指標ではないため追加しない。
    Indicator(key="us10y_yield", name_ja="米10年金利(割引率)", layer=Layer.EV_PHYSICAL_AI,
              source=DataSource.YFINANCE, data_quality=DataQuality.PROXY,
              targets=["tesla"],
              note="長期キャッシュフロー期待の大きいグロース株で割引率感応度が高い。"
                   "金利上昇がバリュエーション圧縮に先行しうるが、Tesla固有材料"
                   "(納車台数/FSD/Optimus)の影響が支配的なためC想定。",
              parquet_stem="price_index_us10y", column="Close"),

    # ===== 量子 (2026-07-02 Step2拡張で新規追加) =====
    # Quantinuumは非上場のためHoneywell(HON)を代理指標とする(config既存の設計)。
    # 量子ピュアプレイ(ionq/dwave/rigetti/ibm)バスケットが先行しうるか検証するが、
    # HONの量子事業比率は売上のごく一部(航空宇宙・オートメーションが主力)であり、
    # 相関がほぼ出ない/出ても意味が薄い可能性が高い(D〜C想定)。
    # 【循環参照回避】バスケット構成にhoneywell自身は含めない。
    Indicator(key="quantum_peer_basket", name_ja="量子ピュアプレイ・バスケット(HON除外)",
              layer=Layer.QUANTUM, source=DataSource.YFINANCE,
              data_quality=DataQuality.PROXY, targets=["quantinuum"],
              note="ionq/dwave/rigetti/ibmのバスケット(honeywell除外、循環参照回避)。"
                   "検証対象はHoneywell株価(Quantinuumの代理)。HONの量子事業比率は"
                   "売上のごく一部のため相関がほぼ出ない可能性が高い(D〜C想定)。"
                   "この検証自体が『Quantinuumの投資判断をHON株で近似することの"
                   "限界』を定量的に示す価値がある。",
              loader="peer_basket:quantum"),
]

# ロボティクス/量子ピアバスケットの構成銘柄(自己除外ロジックはoptical_module_demandと同じ
# _peer_basket_excluding() を再利用)。定義はここで一元管理し run_validation.py から参照する。
ROBOTICS_PEER_KEYS: list[str] = ["harmonic", "fanuc", "yaskawa", "nabtesco"]
QUANTUM_PEER_KEYS: list[str] = ["ionq", "dwave", "rigetti", "ibm"]  # honeywell除外(循環参照回避)

# Step2検証対象に価格データとして最低限必要な行数。Zスコア窓(252営業日)を確保するため
# 250行を下限とする(methodology.md §1のZスコア窓=最低252営業日に準拠)。
MIN_PRICE_ROWS: int = 250

# 検証対象価格が自身に無い銘柄 → 代理銘柄の価格で検証する(quantinuum非上場のため)。
PRICE_PROXY: dict[str, str] = {"quantinuum": "honeywell"}


# ---------------------------------------------------------------------------
# 検証・スコアリング パラメータ
# ---------------------------------------------------------------------------
LAG_DAYS: list[int] = [7, 14, 30, 60, 90, 120, 180]
FORWARD_RETURN_DAYS: list[int] = [1, 7, 30, 60, 90, 120]

# 有効性ランク基準(相関, 的中率)。A+は複数期間での再現性も別途要件。
RANK_THRESHOLDS: dict[str, dict[str, float]] = {
    "A+": {"corr": 0.65, "hit": 0.70},
    "A": {"corr": 0.50, "hit": 0.65},
    "B": {"corr": 0.35, "hit": 0.60},
    # C = 補助指標, D = 不採用
}

# XRPロック需要スコアの段階閾値
XRP_LOCK_DEMAND_STAGES: list[tuple[float, str]] = [
    (30.0, "未発生"),
    (50.0, "初動"),
    (70.0, "加速"),
    (90.0, "本格化"),
    (100.1, "需給ショック"),
]

# ディレクトリ
DATA_RAW = "data/raw"
DATA_PROCESSED = "data/processed"
OUTPUTS = "outputs"
CHARTS = "outputs/charts"

# 材料DB(Phase5) — 正本は MATERIALS_DUMP_DIR 配下の JSONL。
# MATERIALS_DB は実行毎に JSONL から再構築される揮発キャッシュ(gitignore対象)。
MATERIALS_DB = "data/materials.db"
MATERIALS_DUMP_DIR = "data/materials"

# データ取得履歴の最大遡及(暦日)
HISTORY_DAYS = 365 * 5


def held_instruments() -> list[Instrument]:
    """ユーザー保有銘柄のみ返す。"""
    return [i for i in INSTRUMENTS if i.held]


def indicators_for_layer(layer: Layer) -> list[Indicator]:
    """指定レイヤーの指標を返す。"""
    return [ind for ind in INDICATORS if ind.layer == layer]
