import pytest

from tradingagent.risk import position_size


def test_size_from_stop_distance():
    r = position_size(100, 2, entry=100, stop=98)
    assert r["risk_usd"] == pytest.approx(2.0)
    assert r["qty"] == pytest.approx(1.0)
    assert not r["capped_by_leverage"]


def test_leverage_caps_notional_and_reduces_risk():
    r = position_size(100, 3, entry=100, stop=99.5, max_leverage=3)
    assert r["notional"] == pytest.approx(300.0)
    assert r["capped_by_leverage"]
    assert r["risk_usd"] < 3.0


def test_short_and_invalid_inputs():
    assert position_size(100, 1, entry=100, stop=102)["qty"] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        position_size(100, 1, entry=100, stop=100)
    with pytest.raises(ValueError):
        position_size(0, 1, entry=100, stop=99)
