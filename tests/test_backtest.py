import pandas as pd
import pytest

from helpers import frame
from tradingagent.backtest import run_backtest, summarize
from tradingagent.strategies import Signal


def sig(df, direction=1, entry=100.0, stop=95.0, target=110.0, entry_pos=0, check=1, last=None):
    return Signal("T", direction, entry, stop, target, entry_pos, check,
                  len(df) - 1 if last is None else last, df.index[entry_pos])


def run(df, s, **kw):
    kw.setdefault("fee_bps", 0)
    kw.setdefault("slippage_bps", 0)
    return run_backtest(df, [s], **kw).trades.iloc[0]


def test_target_hit_gives_plus_2r():
    df = frame([(100, 100, 100, 100), (100, 106, 99, 105), (105, 111, 104, 110)])
    t = run(df, sig(df))
    assert t["reason"] == "objetivo" and t["r"] == pytest.approx(2.0)


def test_stop_hit_gives_minus_1r():
    df = frame([(100, 100, 100, 100), (100, 101, 94, 96)])
    t = run(df, sig(df))
    assert t["reason"] == "stop" and t["r"] == pytest.approx(-1.0)


def test_same_bar_touching_both_counts_as_stop():
    df = frame([(100, 100, 100, 100), (100, 112, 94, 100)])
    assert run(df, sig(df))["reason"] == "stop"


def test_gap_through_stop_exits_at_open():
    df = frame([(100, 100, 100, 100), (90, 91, 89, 90)])
    t = run(df, sig(df))
    assert t["exit"] == 90 and t["r"] == pytest.approx(-2.0)


def test_time_exit_at_last_bar_close():
    df = frame([(100, 100, 100, 100), (100, 102, 99, 101), (101, 103, 100, 102)])
    t = run(df, sig(df))
    assert t["reason"] == "tiempo" and t["exit"] == 102


def test_short_target():
    df = frame([(100, 100, 100, 100), (100, 101, 89, 90)])
    t = run(df, sig(df, direction=-1, stop=105, target=90))
    assert t["reason"] == "objetivo" and t["r"] == pytest.approx(2.0)


def test_costs_reduce_r():
    df = frame([(100, 100, 100, 100), (100, 111, 99, 110)])
    t = run(df, sig(df), fee_bps=10, slippage_bps=10, min_reward_cost_ratio=0)
    assert t["r"] == pytest.approx(2.0 - (100 + 110) * 0.002 / 5)


def test_low_reward_to_cost_is_skipped():
    df = frame([(100, 100, 100, 100), (100, 101, 99, 100.5)])
    s = sig(df, stop=99.9, target=100.1)
    assert run_backtest(df, [s], fee_bps=5, slippage_bps=2).trades.empty


def test_no_overlapping_positions():
    df = frame([(100, 100, 100, 100)] + [(100, 101, 99, 100)] * 4)
    a = sig(df, entry_pos=0, check=1, last=3)
    b = sig(df, entry_pos=2, check=3, last=4)
    assert len(run_backtest(df, [a, b], 0, 0, 0).trades) == 1


def test_summary_stats_and_small_sample_warning():
    trades = pd.DataFrame({"r": [2.0, -1.0, -1.0, 2.0]})
    s = summarize(trades)
    assert s["trades"] == 4 and s["win_rate"] == 0.5
    assert s["expectancy_r"] == pytest.approx(0.5)
    assert s["profit_factor"] == pytest.approx(2.0)
    assert s["max_drawdown_r"] == pytest.approx(2.0)
    assert "warning" in s
    assert summarize(pd.DataFrame({"r": []}))["trades"] == 0
