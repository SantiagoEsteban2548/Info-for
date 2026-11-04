"""Descarga de velas OHLCV con ccxt (Binance y BingX), sin claves de API."""
from __future__ import annotations

import pandas as pd

SUPPORTED_EXCHANGES = ("binance", "bingx")
COLUMNS = ["open", "high", "low", "close", "volume"]


def make_exchange(exchange_id: str):
    """Crea el cliente ccxt. Datos públicos: no hacen falta claves."""
    import ccxt

    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Exchange no soportado: {exchange_id}. Usa uno de {SUPPORTED_EXCHANGES}")
    return getattr(ccxt, exchange_id)({"enableRateLimit": True})


def fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int | None = None,
                until_ms: int | None = None, page_limit: int = 1000) -> pd.DataFrame:
    """Descarga velas paginando. Devuelve un DataFrame indexado por hora UTC.

    Para perpetuos usa símbolos de ccxt como "BTC/USDT:USDT" o "XAU/USDT:USDT".
    """
    rows: list[list[float]] = []
    cursor = since_ms
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        if until_ms is not None and last_ts >= until_ms:
            break
        next_cursor = last_ts + 1
        if cursor is not None and next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < page_limit:
            break
    return to_frame(rows, until_ms)


def to_frame(rows: list[list[float]], until_ms: int | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", *COLUMNS])
    if df.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC"))
    df = df.drop_duplicates("ts").sort_values("ts")
    if until_ms is not None:
        df = df[df["ts"] <= until_ms]
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.index.name = "time"
    return df[COLUMNS].astype(float)
