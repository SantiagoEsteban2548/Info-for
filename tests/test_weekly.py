import pytest

from helpers import frame
from tradingagent import cli
from tradingagent.weekly import Order, format_report, order_status, pending_orders


def uptrend(n=60):
    return [(100 + i - 0.5, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(n)]


SETUP_BAR = (159, 160, 135, 158)   # toca la EMA 20 y cierra por encima


def daily(bars):
    return frame(bars, freq="1D")


def test_pending_order_from_last_bar_has_no_lookahead():
    df = daily(uptrend() + [SETUP_BAR])
    [o] = pending_orders(df, "BTC/USDT")
    assert o.direction == 1 and o.entry == 160 and o.stop < 135
    assert o.target == pytest.approx(o.entry + 2 * (o.entry - o.stop))
    assert o.created == df.index[-1]


def test_setup_two_bars_back_still_pending_if_not_triggered():
    df = daily(uptrend() + [SETUP_BAR, (158, 159, 150, 152), (152, 159.5, 157, 158)])
    assert [o.entry for o in pending_orders(df, "BTC/USDT")] == [160]


def test_triggered_or_stopped_setup_is_dropped():
    triggered = daily(uptrend() + [SETUP_BAR, (158, 162, 157, 161)])
    assert pending_orders(triggered, "BTC/USDT") == []
    stopped = daily(uptrend() + [SETUP_BAR, (150, 152, 120, 125)])
    assert pending_orders(stopped, "BTC/USDT") == []


def test_no_orders_without_enough_history_or_setup():
    assert pending_orders(daily(uptrend(40)), "X") == []
    assert pending_orders(daily(uptrend(60)), "X") == []


def test_low_reward_vs_cost_is_filtered():
    df = daily(uptrend() + [SETUP_BAR])
    assert pending_orders(df, "X", fee_bps=2000) == []


def order_for(df):
    return pending_orders(df, "BTC/USDT")[0]


def test_status_flow():
    base = daily(uptrend() + [SETUP_BAR])
    o = order_for(base)
    assert order_status(o, base) == "pendiente"
    wait = daily(uptrend() + [SETUP_BAR, (158, 159.5, 150, 152)])
    assert order_status(o, wait) == "pendiente"
    active = daily(uptrend() + [SETUP_BAR, (158, 162, 157, 161)])
    assert order_status(o, active) == "activada"
    hit = daily(uptrend() + [SETUP_BAR, (158, 162, 157, 161), (161, 300, 160, 290)])
    assert order_status(o, hit) == "cerrada en objetivo"
    lost = daily(uptrend() + [SETUP_BAR, (158, 162, 157, 161), (150, 151, 100, 110)])
    assert order_status(o, lost) == "cerrada en stop"
    broken = daily(uptrend() + [SETUP_BAR, (150, 152, 120, 125)])
    assert order_status(o, broken) == "invalidada"


def test_status_expires_and_same_bar_entry_and_stop_counts_stop():
    base = daily(uptrend() + [SETUP_BAR])
    o = order_for(base)
    idle = daily(uptrend() + [SETUP_BAR] + [(158, 159.5, 150, 156)] * 7)
    assert order_status(o, idle) == "expirada"
    both = daily(uptrend() + [SETUP_BAR, (158, 165, 100, 120)])
    assert order_status(o, both) == "cerrada en stop"


def test_short_order_status():
    down = [(200 - i + 0.5, 200 - i + 1, 200 - i - 1, 200 - i) for i in range(60)]
    df = daily(down + [(141, 165, 140, 142)])
    [o] = pending_orders(df, "ETH/USDT")
    assert o.direction == -1 and o.entry == 140
    assert order_status(o, daily(down + [(141, 165, 140, 142), (141, 143, 138, 139)])) == "activada"


def test_report_sizes_by_risk_and_flags():
    o = Order("BTC/USDT", "S1", 1, 100.0, 98.0, 104.0, frame([(1, 1, 1, 1)]).index[0])
    text = format_report([o], equity=100, risk_pct=1)
    assert "BTC/USDT" in text and "largo" in text and "no asesoramiento" in text
    assert "| 0.5 |" in text    # riesgo 1 USD / distancia 2 USD
    tight = Order("X", "S1", 1, 100.0, 99.9, 100.2, o.created)
    assert "tope 3x" in format_report([tight], 100, 1)
    assert "nocional mínimo" in format_report([Order("Y", "S1", 1, 100.0, 90.0, 120.0, o.created)], 10, 1)
    assert "Sin ideas" in format_report([], 100)


def test_cli_weekly_report(monkeypatch, capsys):
    df = daily(uptrend() + [SETUP_BAR, (160, 161, 159, 160)])   # la última vela aún no cerró
    monkeypatch.setattr(cli, "make_exchange", lambda _id: object())
    monkeypatch.setattr(cli, "fetch_ohlcv", lambda *a, **k: df)
    cli.main(["--weekly", "--symbols", "BTC/USDT", "--equity", "100"])
    out = capsys.readouterr().out
    assert "Ideas de la semana" in out and "BTC/USDT" in out
