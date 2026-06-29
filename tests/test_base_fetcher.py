"""BaseFetcher のユーティリティ関数テスト。"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data_sources.base import BaseFetcher, FetchResult


class _DummyFetcher(BaseFetcher):
    source_name = "dummy"

    def fetch(self) -> list[FetchResult]:
        return []


@pytest.fixture
def fetcher(tmp_path: Path) -> _DummyFetcher:
    return _DummyFetcher(
        raw_dir=str(tmp_path / "raw"),
        processed_dir=str(tmp_path / "processed"),
    )


def test_compute_missing_rate_zero(fetcher: _DummyFetcher) -> None:
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert fetcher.compute_missing_rate(df) == 0.0


def test_compute_missing_rate_full(fetcher: _DummyFetcher) -> None:
    df = pd.DataFrame({"a": [np.nan, np.nan]})
    assert fetcher.compute_missing_rate(df) == 1.0


def test_compute_missing_rate_partial(fetcher: _DummyFetcher) -> None:
    df = pd.DataFrame({"a": [1.0, np.nan], "b": [np.nan, np.nan]})
    # 4セル中3つがNaN
    assert abs(fetcher.compute_missing_rate(df) - 0.75) < 1e-9


def test_compute_missing_rate_empty(fetcher: _DummyFetcher) -> None:
    df = pd.DataFrame()
    assert fetcher.compute_missing_rate(df) == 1.0


def test_fetch_result_is_ok_with_data(fetcher: _DummyFetcher) -> None:
    df = pd.DataFrame({"a": [1.0]})
    r = FetchResult(key="k", source="s", fetched_at=datetime.now(), df=df)
    assert r.is_ok()


def test_fetch_result_is_not_ok_with_none(fetcher: _DummyFetcher) -> None:
    r = FetchResult(key="k", source="s", fetched_at=datetime.now(), df=None)
    assert not r.is_ok()


def test_fetch_result_is_not_ok_with_empty_df(fetcher: _DummyFetcher) -> None:
    r = FetchResult(key="k", source="s", fetched_at=datetime.now(), df=pd.DataFrame())
    assert not r.is_ok()


def test_save_and_load_processed(fetcher: _DummyFetcher) -> None:
    df = pd.DataFrame({"price": [100.0, 200.0]}, index=pd.date_range("2024-01-01", periods=2))
    df.index.name = "date"
    fetcher.save_processed("test_key", df)
    loaded = fetcher.load_processed("test_key")
    assert loaded is not None
    assert len(loaded) == 2
    pd.testing.assert_frame_equal(df, loaded, check_freq=False)


def test_load_processed_missing_returns_none(fetcher: _DummyFetcher) -> None:
    result = fetcher.load_processed("nonexistent_key")
    assert result is None


def test_save_raw_creates_file(fetcher: _DummyFetcher) -> None:
    path = fetcher.save_raw("raw_test", {"key": "value"}, datetime.now())
    assert path.exists()
    import json
    data = json.loads(path.read_text())
    assert data["key"] == "value"
