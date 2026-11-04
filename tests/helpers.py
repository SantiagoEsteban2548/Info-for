import pandas as pd


def frame(bars, start="2026-01-01 00:00", freq="4h"):
    """bars: lista de (open, high, low, close[, volume])."""
    idx = pd.date_range(start, periods=len(bars), freq=freq, tz="UTC")
    rows = [(*b, 1.0) if len(b) == 4 else b for b in bars]
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])
