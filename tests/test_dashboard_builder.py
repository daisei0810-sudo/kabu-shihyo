"""src/dashboard/builder.py のテスト(Service Workerキャッシュ更新バグの回帰確認)。

sw.jsのCACHE名が固定文字列だとファイル内容がビルドごとに同一になり、
ブラウザがService Worker更新を検知できずcache-first戦略で初回キャッシュした
index.htmlを無期限に配信し続ける(PWAが更新されない)。CACHE名にビルド時刻を
埋め込みビルドごとに内容を変える修正の回帰テスト。
"""

from __future__ import annotations

from src.dashboard.builder import _SW_JS


class TestServiceWorkerCacheVersioning:
    def test_cache_version_embedded(self) -> None:
        sw = _SW_JS.replace("__CACHE_VERSION__", "202601010000")
        assert "kabu-202601010000" in sw

    def test_different_build_times_produce_different_content(self) -> None:
        sw_a = _SW_JS.replace("__CACHE_VERSION__", "202601010000")
        sw_b = _SW_JS.replace("__CACHE_VERSION__", "202601020000")
        assert sw_a != sw_b

    def test_no_leftover_placeholder(self) -> None:
        sw = _SW_JS.replace("__CACHE_VERSION__", "202601010000")
        assert "__CACHE_VERSION__" not in sw
