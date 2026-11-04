"""Línea de comandos: python -m tradingagent.cli --exchange binance --symbol BTC/USDT --strategy s1"""
from __future__ import annotations

import argparse
import time

from .backtest import run_backtest
from .data import fetch_ohlcv, make_exchange
from .strategies import i1_opening_range_breakout, s1_pullback_ema20
from .weekly import format_report, pending_orders

DEFAULT_TIMEFRAME = {"s1": "4h", "i1": "5m"}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Backtest de un setup del playbook")
    p.add_argument("--exchange", choices=["binance", "bingx"], default="binance")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--strategy", choices=["s1", "i1"], default="s1")
    p.add_argument("--timeframe")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--fee-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--weekly", action="store_true",
                   help="informe semanal de órdenes S1 sobre velas diarias en vez de backtest")
    p.add_argument("--symbols", default="BTC/USDT,ETH/USDT",
                   help="con --weekly: símbolos separados por coma")
    p.add_argument("--equity", type=float, default=100.0)
    p.add_argument("--risk-pct", type=float, default=1.0)
    args = p.parse_args(argv)

    if args.weekly:
        exchange = make_exchange(args.exchange)
        since = int((time.time() - 300 * 86400) * 1000)
        orders = []
        for symbol in (x.strip() for x in args.symbols.split(",") if x.strip()):
            df = fetch_ohlcv(exchange, symbol, "1d", since_ms=since)
            df = df.iloc[:-1]  # la última vela diaria aún no cerró
            orders += pending_orders(df, symbol, fee_bps=args.fee_bps,
                                     slippage_bps=args.slippage_bps)
        print(format_report(orders, args.equity, args.risk_pct))
        return

    timeframe = args.timeframe or DEFAULT_TIMEFRAME[args.strategy]
    since = int((time.time() - args.days * 86400) * 1000)
    df = fetch_ohlcv(make_exchange(args.exchange), args.symbol, timeframe, since_ms=since)
    signals = (s1_pullback_ema20(df) if args.strategy == "s1"
               else i1_opening_range_breakout(df))
    result = run_backtest(df, signals, args.fee_bps, args.slippage_bps)
    print(f"{args.strategy.upper()} {args.symbol} {timeframe} en {args.exchange}, {len(df)} velas")
    for key, value in result.stats.items():
        print(f"  {key}: {value:.3f}" if isinstance(value, float) else f"  {key}: {value}")


if __name__ == "__main__":
    main()
