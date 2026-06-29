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
               note="※暗号資産。量子計算企業 Quantinuum とは別物。要ユーザー確認。"),
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
}


# ---------------------------------------------------------------------------
# 先行指標カタログ
# ---------------------------------------------------------------------------
class Indicator(BaseModel):
    """検証・スコアリング対象の先行指標。"""

    key: str
    name_ja: str
    layer: Layer
    source: DataSource
    data_quality: DataQuality
    # この指標が効くと想定する対象資産(検証で確かめる)
    targets: list[str] = Field(default_factory=list)
    note: str = ""

    @property
    def confidence_weight(self) -> float:
        """data_quality 由来の初期信頼度重み。validation 後にランクで上書きされうる。"""
        return DEFAULT_CONFIDENCE_WEIGHT[self.data_quality]


# MVP(Step1)= Crypto/XRP・AIデータセンター・半導体装置の3領域を中心に定義。
# 他レイヤーは Step4 で拡張(枠だけ用意)。
INDICATORS: list[Indicator] = [
    # ===== XRP / XRPL =====
    Indicator(key="xrp_price", name_ja="XRP価格", layer=Layer.CRYPTO_XRP,
              source=DataSource.COINGECKO, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
    Indicator(key="rlusd_supply", name_ja="RLUSD発行残高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="RLUSD発行体の gateway_balances(hexキー)。発行体は確定済み。"),
    Indicator(key="xrpl_tx_count", name_ja="XRPLトランザクション数", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
    Indicator(key="xrpl_success_payments", name_ja="XRP成功Payment数", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
    Indicator(key="amm_tvl", name_ja="AMM TVL", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
    Indicator(key="amm_xrp_balance", name_ja="AMM内XRP残高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"],
              note="ロック需要スコアの中核(amm_info の XRP側残高)。"),
    Indicator(key="xrpl_dex_volume", name_ja="XRPL DEX出来高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
    Indicator(key="xrp_pair_volume", name_ja="XRP建てペア出来高", layer=Layer.CRYPTO_XRP,
              source=DataSource.XRPL, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
    Indicator(key="stablecoin_tvl", name_ja="Stablecoin TVL", layer=Layer.CRYPTO_XRP,
              source=DataSource.DEFILLAMA, data_quality=DataQuality.VERIFIED, targets=["xrp"]),
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
    Indicator(key="nvidia_revenue", name_ja="NVIDIA売上・ガイダンス", layer=Layer.AI_DATACENTER,
              source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
              targets=["nvidia", "fujikura"], note="四半期・遅延あり。"),
    Indicator(key="hyperscaler_capex", name_ja="Hyperscaler CAPEX", layer=Layer.AI_DATACENTER,
              source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
              targets=["fujikura", "murata"], note="MSFT/GOOGL/AMZN/META capex 合算、四半期遅延。"),
    Indicator(key="optical_module_demand", name_ja="光モジュール需要", layer=Layer.AI_DATACENTER,
              source=DataSource.YFINANCE, data_quality=DataQuality.PROXY,
              targets=["fujikura"], note="フジクラ/住友/古河/村田の株価バスケットで代理。"),
    Indicator(key="optical_price_leadtime", name_ja="光トランシーバー価格・納期",
              layer=Layer.AI_DATACENTER, source=DataSource.NONE,
              data_quality=DataQuality.UNAVAILABLE, targets=["fujikura"]),

    # ===== 半導体装置 / HBM / CoWoS / WFE =====
    Indicator(key="sox_index", name_ja="SOX指数(WFEサイクル代理)", layer=Layer.SEMICAP,
              source=DataSource.YFINANCE, data_quality=DataQuality.PROXY,
              targets=["lasertec_rorze", "kioxia"]),
    Indicator(key="tsmc_capex", name_ja="TSMC CAPEX", layer=Layer.SEMICAP,
              source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
              targets=["lasertec_rorze"], note="TSM 四半期 capex、遅延あり。"),
    Indicator(key="hbm_price", name_ja="HBM価格・需給", layer=Layer.SEMICAP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["kioxia"]),
    Indicator(key="cowos_utilization", name_ja="CoWoS稼働率", layer=Layer.SEMICAP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["lasertec_rorze"]),
    Indicator(key="bb_ratio", name_ja="半導体装置 BBレシオ", layer=Layer.SEMICAP,
              source=DataSource.NONE, data_quality=DataQuality.UNAVAILABLE,
              targets=["lasertec_rorze"], note="SEMI BBレシオ生値は無料API無し。"),
]


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

# データ取得履歴の最大遡及(暦日)
HISTORY_DAYS = 365 * 5


def held_instruments() -> list[Instrument]:
    """ユーザー保有銘柄のみ返す。"""
    return [i for i in INSTRUMENTS if i.held]


def indicators_for_layer(layer: Layer) -> list[Indicator]:
    """指定レイヤーの指標を返す。"""
    return [ind for ind in INDICATORS if ind.layer == layer]
