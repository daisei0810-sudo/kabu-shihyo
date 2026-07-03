"""レジストリ層(config/*.csv 読み込み)のテスト。"""

from __future__ import annotations

from src.config import INDICATORS, INSTRUMENTS, DataQuality, Layer
from src.registry.indicators import freshness_sla_days, importance, observability
from src.registry.instruments import price_proxy_map
from src.registry.themes import benchmark_for, load_themes


class TestInstrumentsRegistry:
    def test_config_instruments_loaded_from_csv(self) -> None:
        assert len(INSTRUMENTS) > 0
        keys = {i.key for i in INSTRUMENTS}
        assert "fujikura" in keys
        assert "harmonic" in keys

    def test_harmonic_still_held(self) -> None:
        harmonic = next(i for i in INSTRUMENTS if i.key == "harmonic")
        assert harmonic.held is True

    def test_price_proxy_map_includes_quantinuum(self) -> None:
        mapping = price_proxy_map()
        assert mapping.get("quantinuum") == "honeywell"


class TestIndicatorsRegistry:
    def test_config_indicators_loaded_from_csv(self) -> None:
        assert len(INDICATORS) > 0
        keys = {i.key for i in INDICATORS}
        assert "xrp_price" in keys

    def test_importance_derived_from_data_quality(self) -> None:
        verified_ind = next(i for i in INDICATORS if i.data_quality == DataQuality.VERIFIED)
        unavailable_ind = next(
            i for i in INDICATORS if i.data_quality == DataQuality.UNAVAILABLE
        )
        assert importance(verified_ind) > importance(unavailable_ind)

    def test_observability_mapping(self) -> None:
        verified_ind = next(i for i in INDICATORS if i.data_quality == DataQuality.VERIFIED)
        assert observability(verified_ind) == "direct"

    def test_freshness_sla_monthly_longer_than_daily(self) -> None:
        daily_ind = next(i for i in INDICATORS if i.freq == "daily")
        monthly_ind = next(i for i in INDICATORS if i.freq == "monthly")
        assert freshness_sla_days(monthly_ind) > freshness_sla_days(daily_ind)


class TestThemesRegistry:
    def test_themes_include_power_and_bio(self) -> None:
        themes = load_themes()
        keys = {t.key for t in themes}
        assert "power" in keys
        assert "bio" in keys

    def test_bio_theme_is_watch_status(self) -> None:
        themes = {t.key: t for t in load_themes()}
        assert themes["bio"].status == "watch"

    def test_layer_enum_has_power_and_bio(self) -> None:
        assert Layer.POWER.value == "power"
        assert Layer.BIO.value == "bio"

    def test_benchmark_for_known_theme(self) -> None:
        assert benchmark_for("semicap") == "index_sox"

    def test_benchmark_for_unknown_theme_is_none(self) -> None:
        assert benchmark_for("crypto_xrp") is None
