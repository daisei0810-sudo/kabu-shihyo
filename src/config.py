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
    POWER = "power"                           # 9. 電力(Utility CAPEX/Grid/Nuclear/SMR)
    BIO = "bio"                               # 10. バイオ(優先度低・監視のみ)


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
#
# 正本は config/instruments.csv (Investment OS Layer1銘柄マスタの外部化)。
# 銘柄追加はCSVへ1行足すだけでよく、この変数を直接編集する必要はない。
from src.registry.instruments import load_instruments, price_proxy_map  # noqa: E402

INSTRUMENTS: list[Instrument] = load_instruments()

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


# 正本は config/indicators.csv (Investment OS Layer3指標辞書の外部化)。
# 指標追加はCSVへ1行足すだけでよく、この変数を直接編集する必要はない。
from src.registry.indicators import load_indicators  # noqa: E402

INDICATORS: list[Indicator] = load_indicators()

# ロボティクス/量子ピアバスケットの構成銘柄(自己除外ロジックはoptical_module_demandと同じ
# _peer_basket_excluding() を再利用)。定義はここで一元管理し run_validation.py から参照する。
ROBOTICS_PEER_KEYS: list[str] = ["harmonic", "fanuc", "yaskawa", "nabtesco"]
QUANTUM_PEER_KEYS: list[str] = ["ionq", "dwave", "rigetti", "ibm"]  # honeywell除外(循環参照回避)

# Step2検証対象に価格データとして最低限必要な行数。Zスコア窓(252営業日)を確保するため
# 250行を下限とする(methodology.md §1のZスコア窓=最低252営業日に準拠)。
MIN_PRICE_ROWS: int = 250

# 検証対象価格が自身に無い銘柄 → 代理銘柄の価格で検証する(quantinuum非上場のため)。
# 正本は config/instruments.csv の proxy_key 列。
PRICE_PROXY: dict[str, str] = price_proxy_map()


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
