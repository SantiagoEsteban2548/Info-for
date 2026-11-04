"""Pipeline diario automático: velas → setup técnico → noticias → debate → juez → órdenes.

    python -m tradingagent.pipeline run --broker paper --llm anthropic   # una vez al día
    python -m tradingagent.pipeline update                               # solo actualizar paper

Se ejecuta cada día tras el cierre de la vela diaria (00:00 UTC). Cada corrida primero
actualiza las órdenes abiertas con las velas nuevas y después busca ideas. El debate solo se
lanza para activos con setup técnico nuevo: sin setup, con posición abierta o con esa misma
señal ya colocada, no se gasta en modelo.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from .assets import ASSETS, Asset
from .broker import CCXTBroker, PaperBroker, Placement, make_ccxt_client
from .debate import AnthropicLLM, KeywordLLM, LLM, decide, run_debate, technical_context
from .news import collect_news, http_get
from .risk import position_size
from .weekly import pending_orders

CandleLoader = Callable[[Asset], pd.DataFrame]


@dataclass
class Limits:
    equity: float = 100.0
    max_positions: int = 2
    max_leverage: float = 3.0
    min_notional: float = 5.0
    max_drawdown_pct: float = 10.0     # con esta pérdida acumulada se dejan de abrir operaciones
    kill_switch_file: str = "STOP"     # si existe este archivo, no se abre nada


@dataclass
class AssetReport:
    asset: str
    symbol: str
    technical: str
    news_count: int = 0
    news_errors: list[str] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    decision: str = ""
    placement: dict | None = None


def load_daily_candles(asset: Asset, days: int = 400) -> pd.DataFrame:
    from .data import fetch_ohlcv, make_exchange

    import ccxt

    since = int((time.time() - days * 86400) * 1000)
    try:
        df = fetch_ohlcv(make_exchange(asset.exchange), asset.symbol, "1d", since_ms=since)
    except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
        # Binance bloquea algunas regiones (HTTP 451, p. ej. servidores en EE. UU.). Los perpetuos
        # cripto de BingX usan el mismo símbolo, así que las velas salen de ahí. Solo afecta a los datos.
        if asset.exchange == "bingx":
            raise
        df = fetch_ohlcv(make_exchange("bingx"), asset.symbol, "1d", since_ms=since)
    today = pd.Timestamp.now(tz="UTC").normalize()
    return df[df.index < today]          # solo velas cerradas


def run(assets: list[Asset], llm: LLM, brokers: dict, limits: Limits, out_dir: Path,
        load_candles: CandleLoader = load_daily_candles, fetch=http_get,
        now: datetime | None = None, broker_name: str = "paper",
        pdf: bool = True) -> list[AssetReport]:
    """`brokers` mapea exchange -> broker. Los topes de posiciones y pérdidas son globales."""
    now = now or datetime.now(timezone.utc)
    reports: list[AssetReport] = []
    unique = list({id(b): b for b in brokers.values()}.values())
    candles: dict[str, pd.DataFrame] = {}

    def get_candles(asset: Asset) -> pd.DataFrame:
        if asset.symbol not in candles:
            candles[asset.symbol] = load_candles(asset)
        return candles[asset.symbol]

    # 1) Poner al día las órdenes abiertas (paper) antes de contar huecos y pérdidas.
    by_symbol = {a.symbol: a for a in ASSETS.values()} | {a.symbol: a for a in assets}
    for b in unique:
        if hasattr(b, "update"):
            syms = {p.symbol for p in b.open_positions() if p.symbol in by_symbol}
            b.update({s_: get_candles(by_symbol[s_]) for s_ in syms})
    placed = {(p.symbol, p.created) for b in unique if hasattr(b, "all") for p in b.all()}
    open_now = [p for b in unique for p in b.open_positions()]
    open_symbols = {p.symbol if isinstance(p, Placement) else p.get("symbol") for p in open_now}
    slots = limits.max_positions - len(open_now)
    pnl = sum(b.realized_pnl() for b in unique if hasattr(b, "realized_pnl"))
    equity = limits.equity + pnl
    blocked = None
    if Path(limits.kill_switch_file).exists():
        blocked = f"interruptor de parada activo ({limits.kill_switch_file})"
    elif pnl <= -limits.equity * limits.max_drawdown_pct / 100:
        blocked = f"pérdida acumulada {pnl:.2f} USD supera el {limits.max_drawdown_pct:g}%"

    for asset in assets:
        df = get_candles(asset)
        orders = pending_orders(df, asset.symbol)
        order = orders[0] if orders else None
        tech = technical_context(df, asset.symbol, order)
        rep = AssetReport(asset.key, asset.symbol, tech.describe())
        reports.append(rep)
        if order is None:
            rep.decision = "sin setup técnico: no se debate"
            continue
        if asset.symbol in open_symbols:
            rep.decision = "ya hay una posición u orden abierta en este activo: no se debate"
            continue
        if (asset.symbol, order.created.isoformat()) in placed:
            rep.decision = "esta señal ya se colocó antes: no se repite"
            continue
        news, errors = collect_news(asset.sources(), fetch=fetch, now=now)
        rep.news_count, rep.news_errors = len(news), errors
        debate = run_debate(llm, tech, news)
        rep.verdict = debate.verdict
        (out_dir / f"debate_{asset.key}.json").write_text(
            json.dumps({"noticias": [asdict(n) for n in news], **debate.to_dict()},
                       ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        d = decide(order, debate.verdict, asset.max_risk_pct)
        rep.decision = d.reason
        if not d.trade:
            continue
        if blocked:
            rep.decision += f"; no se coloca: {blocked}"
            continue
        if slots <= 0:
            rep.decision += f"; tope de {limits.max_positions} posiciones alcanzado"
            continue
        size = position_size(equity, d.risk_pct, order.entry, order.stop, limits.max_leverage)
        if size["notional"] < limits.min_notional:
            rep.decision += f"; nocional {size['notional']:.2f} USD bajo el mínimo del exchange"
            continue
        p = brokers[asset.exchange].place(Placement(
            asset.symbol, order.setup, order.direction, order.entry, order.stop, order.target,
            size["qty"], size["risk_usd"], size["leverage"], order.created.isoformat(),
            order.valid_bars))
        rep.placement = asdict(p)
        rep.decision += f"; orden colocada con riesgo {d.risk_pct:g}% ({size['risk_usd']:.2f} USD)"
        slots -= 1
        open_symbols.add(asset.symbol)
    if pdf:
        # El PDF es un extra: si falla, la corrida (y las órdenes) siguen valiendo.
        try:
            from . import report

            items = report.items_from_reports(reports, candles, {a.key: a.name for a in assets},
                                              equity)
            report.build_daily_pdf(items, out_dir / "informe.pdf", now, equity, broker_name)
        except Exception as exc:
            (out_dir / "informe_pdf_error.txt").write_text(f"{type(exc).__name__}: {exc}\n",
                                                           encoding="utf-8")
    return reports


def format_markdown(reports: list[AssetReport], broker_name: str, now: datetime) -> str:
    lines = [f"# Pipeline diario {now:%Y-%m-%d} (ejecución: {broker_name})", ""]
    for r in reports:
        lines += [f"## {r.asset} ({r.symbol})", "", r.technical, ""]
        if r.verdict:
            v = r.verdict
            lines.append(f"Juez: **{v.get('veredicto')}**, convicción {v.get('conviccion')}, "
                         f"coincide con el técnico: {'sí' if v.get('coincide_con_tecnico') else 'no'}.")
            lines += [f"- {x}" for x in v.get("razones", [])]
            if v.get("alertas"):
                lines += ["", "Alertas:"] + [f"- {x}" for x in v["alertas"]]
            lines.append(f"\nNoticias leídas: {r.news_count}.")
        if r.news_errors:
            lines.append("Fuentes caídas: " + "; ".join(r.news_errors))
        lines += ["", f"Decisión: {r.decision}.", ""]
    lines.append("Ideas generadas automáticamente, no asesoramiento financiero.")
    return "\n".join(lines) + "\n"


def make_broker(kind: str, state_dir: Path, exchange: str, max_leverage: float):
    return CCXTBroker(make_ccxt_client(exchange, kind), kind, max_leverage)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Pipeline diario: noticias, debate, juez y ejecución")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="corrida diaria: actualiza órdenes, analiza y coloca")
    r.add_argument("--assets", default="BTC,ETH,XAU,XAG")
    r.add_argument("--broker", choices=["paper", "testnet", "live"], default="paper")
    r.add_argument("--llm", choices=["anthropic", "keywords"], default="anthropic")
    r.add_argument("--equity", type=float, default=100.0)
    r.add_argument("--state-dir", default="state")
    u = sub.add_parser("update", help="solo actualiza las órdenes paper con velas nuevas")
    u.add_argument("--state-dir", default="state")
    args = p.parse_args(argv)
    state = Path(args.state_dir)

    if args.cmd == "update":
        broker = PaperBroker(state / "paper_ledger.json")
        symbols = {pl.symbol for pl in broker.open_positions()}
        candles = {a.symbol: load_daily_candles(a, 120) for a in ASSETS.values() if a.symbol in symbols}
        for pl in broker.update(candles):
            print(f"{pl.symbol} {pl.setup} {pl.status} pnl {pl.pnl_usd:+.2f} USD")
        print(f"PnL realizado: {broker.realized_pnl():+.2f} USD")
        return

    now = datetime.now(timezone.utc)
    out = state / "runs" / f"{now:%Y-%m-%d_%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    assets = [ASSETS[k.strip().upper()] for k in args.assets.split(",")]
    llm = AnthropicLLM() if args.llm == "anthropic" else KeywordLLM()
    limits = Limits(equity=args.equity)
    # Paper: un único libro. Testnet/real: un cliente por exchange (Binance cripto, BingX metales).
    if args.broker == "paper":
        paper = PaperBroker(state / "paper_ledger.json")
        brokers = {a.exchange: paper for a in assets}
    else:
        brokers = {ex: make_broker(args.broker, state, ex, limits.max_leverage)
                   for ex in {a.exchange for a in assets}}
    reports = run(assets, llm, brokers, limits, out, broker_name=args.broker)
    md = format_markdown(reports, args.broker, now)
    (out / "informe.md").write_text(md, encoding="utf-8")
    (out / "informe.json").write_text(json.dumps([asdict(x) for x in reports], ensure_ascii=False,
                                                 indent=2, default=str), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
