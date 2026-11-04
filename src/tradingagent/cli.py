"""Línea de comandos: python -m tradingagent.cli --exchange binance --symbol BTC/USDT --strategy s1"""
from __future__ import annotations

import argparse
import time

from .backtest import run_backtest
from .data import fetch_ohlcv, make_exchange
from .strategies import i1_opening_range_breakout, s1_pullback_ema20

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
    args = p.parse_args(argv)

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
