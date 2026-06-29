"""config.py の基本動作テスト。"""

from src.config import (
    DEFAULT_CONFIDENCE_WEIGHT,
    INDICATORS,
    INSTRUMENTS,
    DataQuality,
    Layer,
    held_instruments,
    indicators_for_layer,
)


def test_all_instruments_have_required_fields() -> None:
    for inst in INSTRUMENTS:
        assert inst.key, f"key is empty: {inst}"
        assert inst.name_ja, f"name_ja is empty: {inst.key}"
        assert inst.layer is not None
        assert inst.data_quality is not None


def test_unavailable_instruments_have_no_ticker_or_note() -> None:
    for inst in INSTRUMENTS:
        if inst.data_quality == DataQuality.UNAVAILABLE:
            assert inst.ticker is None, f"{inst.key} is UNAVAILABLE but has ticker"
            assert inst.note, f"{inst.key} is UNAVAILABLE but has no note"


def test_held_instruments_returns_only_held() -> None:
    held = held_instruments()
    assert len(held) > 0
    assert all(i.held for i in held)


def test_confidence_weight_ordering() -> None:
    assert DEFAULT_CONFIDENCE_WEIGHT[DataQuality.VERIFIED] == 1.0
    assert DEFAULT_CONFIDENCE_WEIGHT[DataQuality.PROXY] < 1.0
    assert (
        DEFAULT_CONFIDENCE_WEIGHT[DataQuality.ESTIMATED]
        < DEFAULT_CONFIDENCE_WEIGHT[DataQuality.PROXY]
    )
    assert DEFAULT_CONFIDENCE_WEIGHT[DataQuality.UNAVAILABLE] == 0.0


def test_all_indicators_have_targets() -> None:
    for ind in INDICATORS:
        # unavailable は targets があってもなくていい
        if ind.data_quality != DataQuality.UNAVAILABLE:
            assert ind.targets, f"indicator {ind.key} has no targets"


def test_indicators_for_layer_filters_correctly() -> None:
    xrp_inds = indicators_for_layer(Layer.CRYPTO_XRP)
    assert len(xrp_inds) > 0
    assert all(i.layer == Layer.CRYPTO_XRP for i in xrp_inds)


def test_indicator_confidence_weight_property() -> None:
    for ind in INDICATORS:
        w = ind.confidence_weight
        assert 0.0 <= w <= 1.0
        if ind.data_quality == DataQuality.VERIFIED:
            assert w == 1.0
        elif ind.data_quality == DataQuality.UNAVAILABLE:
            assert w == 0.0
