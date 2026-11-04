from tradingagent.data import fetch_ohlcv, make_exchange, to_frame
import pytest


class FakeExchange:
    def __init__(self, candles):
        self.candles = candles
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls += 1
        out = [c for c in self.candles if since is None or c[0] >= since]
        return out[:limit]


def candles(n):
    return [[i * 60_000, 1, 2, 0.5, 1.5, 10] for i in range(n)]


def test_paginates_and_dedupes():
    ex = FakeExchange(candles(25))
    df = fetch_ohlcv(ex, "BTC/USDT", "1m", since_ms=0, page_limit=10)
    assert len(df) == 25
    assert df.index.is_monotonic_increasing and df.index.tz is not None
    assert ex.calls == 3


def test_respects_until():
    df = fetch_ohlcv(FakeExchange(candles(25)), "BTC/USDT", "1m", since_ms=0,
                     until_ms=10 * 60_000, page_limit=10)
    assert len(df) == 11


def test_empty_and_unsupported_exchange():
    assert to_frame([]).empty
    with pytest.raises(ValueError):
        make_exchange("kraken")
    assert make_exchange("binance").id == "binance"
