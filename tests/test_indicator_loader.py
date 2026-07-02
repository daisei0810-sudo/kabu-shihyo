"""src/indicator_loader.py のテスト(Step2改善: データ駆動指標ローダー)。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config import DataQuality, DataSource, Indicator, Layer
from src.indicator_loader import (
    load_indicator_series,
    peer_basket_excluding,
    read_parquet_column,
)


def _write_price(path: Path, key: str, n: int = 60, base: float = 100.0) -> None:
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    prices = [base + i * 0.5 for i in range(n)]
    pd.DataFrame({"Close": prices}, index=dates).to_parquet(path / f"price_{key}.parquet")


class TestReadParquetColumn:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_parquet_column("nonexistent", "Close", tmp_path) is None

    def test_reads_existing_column(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "a")
        s = read_parquet_column("price_a", "Close", tmp_path)
        assert s is not None
        assert len(s) == 60

    def test_missing_column_returns_none(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "a")
        assert read_parquet_column("price_a", "NonexistentCol", tmp_path) is None


class TestPeerBasketExcluding:
    def test_excludes_target_itself(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "a", base=100.0)
        _write_price(tmp_path, "b", base=200.0)
        # target="a"を含むピアリスト["a","b"]でも、バスケットには"a"自身が混入しないこと。
        # 単独b(base=200)のみなら初日正規化=1.0。aが混入していれば初日から複数系列の平均になる。
        basket_with_a_excluded = peer_basket_excluding("a", ["a", "b"], tmp_path)
        basket_b_only = peer_basket_excluding("nonexistent", ["b"], tmp_path)
        assert basket_with_a_excluded is not None
        assert basket_b_only is not None
        pd.testing.assert_series_equal(
            basket_with_a_excluded, basket_b_only, check_names=False
        )

    def test_no_peers_available_returns_none(self, tmp_path: Path) -> None:
        assert peer_basket_excluding("a", ["a"], tmp_path) is None

    def test_multi_peer_basket_averages(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "b", base=100.0)
        _write_price(tmp_path, "c", base=100.0)
        basket = peer_basket_excluding("a", ["b", "c"], tmp_path)
        assert basket is not None
        assert len(basket) == 60


class TestLoadIndicatorSeries:
    def _make_indicator(self, **overrides: object) -> Indicator:
        defaults: dict[str, object] = dict(
            key="test_ind", name_ja="テスト指標", layer=Layer.SEMICAP,
            source=DataSource.YFINANCE, data_quality=DataQuality.VERIFIED,
            targets=["a"],
        )
        defaults.update(overrides)
        return Indicator(**defaults)  # type: ignore[arg-type]

    def test_parquet_stem_column_loading(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "a")
        ind = self._make_indicator(parquet_stem="price_a", column="Close")
        s = load_indicator_series(ind, "a", processed_dir=tmp_path)
        assert s is not None
        assert s.name == "test_ind"

    def test_no_source_returns_none(self, tmp_path: Path) -> None:
        ind = self._make_indicator()  # parquet_stem/column/loader すべてNone
        assert load_indicator_series(ind, "a", processed_dir=tmp_path) is None

    def test_step2_verifiable_false_respected_when_flagged(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "a")
        ind = self._make_indicator(
            parquet_stem="price_a", column="Close", step2_verifiable=False
        )
        assert load_indicator_series(ind, "a", tmp_path, respect_step2_flag=True) is None

    def test_step2_verifiable_false_ignored_when_not_flagged(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "a")
        ind = self._make_indicator(
            parquet_stem="price_a", column="Close", step2_verifiable=False
        )
        # respect_step2_flag=False(デフォルト、Extendedスコア向け)は無視して読み込む
        s = load_indicator_series(ind, "a", tmp_path, respect_step2_flag=False)
        assert s is not None

    def test_peer_basket_loader(self, tmp_path: Path) -> None:
        _write_price(tmp_path, "b")
        _write_price(tmp_path, "c")
        ind = self._make_indicator(loader="peer_basket:optical", targets=["a"])
        # OPTICAL_PEERSに"b"/"c"は含まれないため空になる。loader経路が正しく呼ばれることのみ確認
        result = load_indicator_series(ind, "fujikura", tmp_path)
        # fujikura自身のprice_fujikura.parquetがtmp_pathに無いのでNoneになるはずだが、
        # クラッシュしないことを確認するのが目的
        assert result is None

    def test_unregistered_loader_returns_none(self, tmp_path: Path) -> None:
        ind = self._make_indicator(loader="peer_basket:nonexistent")
        assert load_indicator_series(ind, "a", tmp_path) is None

    def test_monthly_freq_ffills_to_daily(self, tmp_path: Path) -> None:
        # 月初のみのデータ(月次系列を模擬)
        dates = pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"])
        pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0]}, index=dates).to_parquet(
            tmp_path / "monthly.parquet"
        )
        ind = self._make_indicator(
            parquet_stem="monthly", column="value", freq="monthly"
        )
        s = load_indicator_series(ind, "a", tmp_path)
        assert s is not None
        # ffillにより月初と次の月初の間の日次データが埋まっているはず
        assert len(s) > 4

    def test_empty_series_returns_none(self, tmp_path: Path) -> None:
        pd.DataFrame({"value": []}).to_parquet(tmp_path / "empty.parquet")
        ind = self._make_indicator(parquet_stem="empty", column="value")
        assert load_indicator_series(ind, "a", tmp_path) is None
