import pytest

from helpers import frame
from tradingagent.backtest import run_backtest
from tradingagent.strategies import i1_opening_range_breakout, s1_pullback_ema20


def uptrend(n=60):
    return [(100 + i - 0.5, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(n)]


def test_s1_long_pullback_to_ema20():
    bars = uptrend() + [
        (159, 160, 135, 158),      # toca la EMA 20 y cierra por encima
        (158.5, 161, 157, 160.5),  # rompe el máximo anterior: entrada
    ] + [(160 + 7 * k, 165 + 7 * k, 159 + 7 * k, 164 + 7 * k) for k in range(10)]
    df = frame(bars)
    signals = s1_pullback_ema20(df)
    sig = next(s for s in signals if s.entry_pos == 61)
    assert sig.direction == 1 and sig.entry == 160 and sig.stop < 135
    assert sig.target == pytest.approx(sig.entry + 2 * (sig.entry - sig.stop))
    result = run_backtest(df, [sig], fee_bps=0, slippage_bps=0)
    assert result.trades.iloc[0]["reason"] == "objetivo"


def test_s1_no_signal_when_breakout_never_triggers():
    bars = uptrend() + [(159, 160, 135, 158), (158, 159.5, 150, 152)] + [(152, 153, 150, 151)] * 3
    assert not [s for s in s1_pullback_ema20(frame(bars)) if s.entry_pos == 61]


def test_s1_short_mirrors_downtrend():
    down = [(200 - i + 0.5, 200 - i + 1, 200 - i - 1, 200 - i) for i in range(60)]
    bars = down + [(141, 165, 140, 142), (141.5, 143, 139, 140)] + [(140, 141, 139, 140)] * 3
    shorts = [s for s in s1_pullback_ema20(frame(bars)) if s.direction == -1]
    assert any(s.entry_pos == 61 and s.stop > 165 for s in shorts)
    assert not [s for s in s1_pullback_ema20(frame(bars), allow_short=False) if s.direction == -1]


def orb_day(day, or_volume, breakout_close):
    """Un día de velas de 5 min desde las 13:30 UTC: 3 velas de rango y 12 posteriores."""
    start = f"2026-01-{day:02d} 13:30"
    bars = [(100, 101, 99, 100, or_volume / 3)] * 3
    bars += [(100, 100.5, 99.5, 100, 1.0)] * 2
    bars += [(100, breakout_close + 0.5, 100, breakout_close, 1.0)]
    bars += [(breakout_close, breakout_close + 5, breakout_close - 0.2, breakout_close + 4, 1.0)] * 9
    return frame(bars, start=start, freq="5min")


def build_orb(or_volumes, breakout_close=102):
    import pandas as pd
    return pd.concat([orb_day(i + 1, v, breakout_close) for i, v in enumerate(or_volumes)])


def test_i1_needs_relative_volume():
    quiet = build_orb([300] * 6 + [330])
    assert i1_opening_range_breakout(quiet) == []
    busy = build_orb([300] * 6 + [900])
    signals = i1_opening_range_breakout(busy)
    assert len(signals) == 1
    s = signals[0]
    assert s.direction == 1 and s.entry == 102 and s.stop == 99
    assert s.target == pytest.approx(108)
    assert s.time.day == 7


def test_i1_short_breakdown_and_validation():
    s = i1_opening_range_breakout(build_orb([300] * 6 + [900], breakout_close=98))
    assert len(s) == 1 and s[0].direction == -1 and s[0].stop == 101
    bad = build_orb([300] * 6 + [900])
    with pytest.raises(ValueError):
        i1_opening_range_breakout(bad, or_minutes=12)
