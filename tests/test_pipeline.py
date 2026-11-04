import json

import pytest

from helpers import frame
from tradingagent import broker as broker_mod
from tradingagent.assets import ASSETS
from tradingagent.broker import CCXTBroker, LiveTradingBlocked, PaperBroker, Placement
from tradingagent.debate import JUDGE_SCHEMA
from tradingagent.pipeline import Limits, format_markdown, run

UP = [(100 + i - 0.5, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(60)]
SETUP = (159, 160, 135, 158)


def df_setup():
    return frame(UP + [SETUP], freq="1D")


class Judge:
    def __init__(self, verdict="alcista", conviction=70):
        self.v = {"veredicto": verdict, "conviccion": conviction, "coincide_con_tecnico": True,
                  "razones": ["r"], "alertas": [], "eventos_proximos": []}
        self.calls = 0

    def complete_json(self, system, user, schema):
        self.calls += 1
        if schema is JUDGE_SCHEMA:
            return self.v
        return {"tesis": "", "argumentos": [], "citas": [], "riesgos_de_mi_tesis": [], "confianza": 50}


def no_news(url):
    raise OSError("sin red")


def assets(*keys):
    return [ASSETS[k] for k in keys]


def test_end_to_end_paper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paper = PaperBroker(tmp_path / "ledger.json")
    llm = Judge()
    flat = frame(UP, freq="1D")
    loads = {"BTC/USDT:USDT": df_setup(), "ETH/USDT:USDT": flat}
    reps = run(assets("BTC", "ETH"), llm, {"binance": paper}, Limits(), tmp_path,
               load_candles=lambda a: loads[a.symbol], fetch=no_news)
    btc, eth = reps
    assert btc.placement and btc.placement["risk_usd"] == pytest.approx(1.0)
    assert "sin setup" in eth.decision and llm.calls == 7      # ETH no gasta llamadas
    assert len(paper.open_positions()) == 1
    assert json.loads((tmp_path / "debate_BTC.json").read_text())["verdict"]["veredicto"] == "alcista"
    assert "Decisión" in format_markdown(reps, "paper", __import__("datetime").datetime.now())
    # Segunda ejecución con la misma señal: no duplica.
    run(assets("BTC"), Judge(), {"binance": paper}, Limits(), tmp_path,
        load_candles=lambda a: loads[a.symbol], fetch=no_news)
    assert len(paper.all()) == 1


def test_limits_block_orders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load = lambda a: df_setup()
    # Tope de posiciones global entre exchanges.
    paper = PaperBroker(tmp_path / "l.json")
    reps = run(assets("BTC", "ETH", "XAU"), Judge(), {"binance": paper, "bingx": paper},
               Limits(max_positions=2), tmp_path, load_candles=load, fetch=no_news)
    assert [bool(r.placement) for r in reps] == [True, True, False]
    assert "tope de 2" in reps[2].decision
    # Interruptor de parada.
    (tmp_path / "STOP").write_text("")
    reps = run(assets("XAG"), Judge(), {"bingx": PaperBroker(tmp_path / "m.json")}, Limits(),
               tmp_path, load_candles=load, fetch=no_news)
    assert not reps[0].placement and "interruptor" in reps[0].decision
    (tmp_path / "STOP").unlink()
    # Juez en contra: no se opera.
    reps = run(assets("XAG"), Judge("bajista"), {"bingx": PaperBroker(tmp_path / "n.json")},
               Limits(), tmp_path, load_candles=load, fetch=no_news)
    assert not reps[0].placement and "no coinciden" in reps[0].decision
    # Nocional mínimo.
    reps = run(assets("XAG"), Judge(), {"bingx": PaperBroker(tmp_path / "o.json")},
               Limits(equity=10), tmp_path, load_candles=load, fetch=no_news)
    assert not reps[0].placement and "mínimo" in reps[0].decision


def test_paper_update_closes_with_pnl(tmp_path):
    paper = PaperBroker(tmp_path / "l.json")
    df = df_setup()
    from tradingagent.weekly import pending_orders
    o = pending_orders(df, "BTC/USDT:USDT")[0]
    paper.place(Placement(o.symbol, o.setup, 1, o.entry, o.stop, o.target, 0.04, 1.0, 0.06,
                          o.created.isoformat(), o.valid_bars))
    later = frame(UP + [SETUP, (158, 162, 157, 161), (161, 230, 160, 225)], freq="1D")
    [p] = paper.update({o.symbol: later})
    assert p.status == "cerrada en objetivo" and p.pnl_usd == pytest.approx(
        0.04 * (o.target - o.entry) - 0.04 * (o.entry + o.target) * 5 / 1e4)
    assert paper.open_positions() == []


class FakeExchange:
    def __init__(self):
        self.calls = []

    def load_markets(self):
        pass

    def set_margin_mode(self, mode, sym):
        self.calls.append(("margin", mode))

    def set_leverage(self, lev, sym):
        self.calls.append(("leverage", lev))

    def amount_to_precision(self, sym, x):
        return f"{x:.3f}"

    def price_to_precision(self, sym, x):
        return f"{x:.2f}"

    def create_order(self, sym, type_, side, qty, price, params):
        self.calls.append(("order", side, qty, params))
        return {"id": str(len(self.calls))}


def test_ccxt_broker_places_bracket():
    ex = FakeExchange()
    p = CCXTBroker(ex, "testnet").place(Placement(
        "BTC/USDT:USDT", "S1", -1, 100.0, 110.0, 80.0, 0.1234, 1.2, 0.12, "2026-11-06", 7))
    orders = [c for c in ex.calls if c[0] == "order"]
    assert ("margin", "isolated") in ex.calls and ("leverage", 1) in ex.calls
    assert orders[0][1:3] == ("sell", 0.123) and orders[0][3] == {"triggerPrice": 100.0}
    assert orders[1][1] == "buy" and orders[1][3] == {"stopLossPrice": 110.0, "reduceOnly": True}
    assert orders[2][3] == {"takeProfitPrice": 80.0, "reduceOnly": True}
    assert len(p.broker_ids) == 3


def test_live_is_blocked_without_confirmation(monkeypatch):
    monkeypatch.delenv("TRADINGAGENT_ALLOW_LIVE", raising=False)
    with pytest.raises(LiveTradingBlocked):
        CCXTBroker(FakeExchange(), "live")
    with pytest.raises(LiveTradingBlocked):
        broker_mod.make_ccxt_client("binance", "live")
    monkeypatch.setenv("TRADINGAGENT_ALLOW_LIVE", "si")
    with pytest.raises(LiveTradingBlocked):
        CCXTBroker(FakeExchange(), "live")


def test_testnet_clients_point_to_demo():
    b = broker_mod.make_ccxt_client("binance", "testnet")
    assert "demo" in json.dumps(b.urls["api"]) or "testnet" in json.dumps(b.urls["api"])
    x = broker_mod.make_ccxt_client("bingx", "testnet")
    assert "vst" in json.dumps(x.urls["api"])
