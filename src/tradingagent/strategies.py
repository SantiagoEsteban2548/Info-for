"""Setups del playbook como reglas programables.

S1: pullback a la EMA 20 en tendencia (swing, velas de 4h o diarias).
I1: ruptura del rango de apertura de 15 min en activos "in play" (intradía).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr, ema


@dataclass(frozen=True)
class Signal:
    setup: str
    direction: int            # 1 largo, -1 corto
    entry: float
    stop: float
    target: float
    entry_pos: int            # posición de la vela donde se abre la operación
    first_check_pos: int      # primera vela en la que se vigilan stop y objetivo
    last_pos: int             # última vela permitida (salida por tiempo al cierre)
    time: pd.Timestamp


def s1_pullback_ema20(df: pd.DataFrame, rr: float = 2.0, atr_buffer: float = 0.1,
                      max_hold: int = 20, allow_short: bool = True) -> list[Signal]:
    """Pullback a EMA 20 con tendencia (EMA 20 vs EMA 50) y entrada por ruptura.

    La señal se forma en la vela i; la entrada es una orden stop sobre el máximo
    (o bajo el mínimo en cortos) de esa vela, válida solo para la vela siguiente.
    """
    close, high, low, open_ = (df[c].to_numpy() for c in ("close", "high", "low", "open"))
    e20, e50 = ema(df["close"], 20).to_numpy(), ema(df["close"], 50).to_numpy()
    a = atr(df).to_numpy()
    n = len(df)
    signals: list[Signal] = []
    for i in range(50, n - 1):
        long_ok = close[i] > e50[i] and e20[i] > e50[i] and low[i] <= e20[i] < close[i]
        short_ok = (allow_short and close[i] < e50[i] and e20[i] < e50[i]
                    and high[i] >= e20[i] > close[i])
        if long_ok and high[i + 1] >= high[i]:
            fill = max(open_[i + 1], high[i])
            stop = low[i] - atr_buffer * a[i]
            direction = 1
        elif short_ok and low[i + 1] <= low[i]:
            fill = min(open_[i + 1], low[i])
            stop = high[i] + atr_buffer * a[i]
            direction = -1
        else:
            continue
        risk = (fill - stop) * direction
        if risk <= 0:
            continue
        signals.append(Signal(
            setup="S1", direction=direction, entry=float(fill), stop=float(stop),
            target=float(fill + direction * rr * risk), entry_pos=i + 1,
            first_check_pos=i + 1, last_pos=min(i + 1 + max_hold, n - 1),
            time=df.index[i + 1]))
    return signals


def i1_opening_range_breakout(df: pd.DataFrame, session_start: str = "13:30",
                              or_minutes: int = 15, session_minutes: int = 240,
                              min_rvol: float = 2.0, lookback_days: int = 10,
                              rr: float = 2.0) -> list[Signal]:
    """Ruptura del rango de apertura solo si el volumen del rango es >= min_rvol veces su media.

    Las horas están en UTC (13:30 UTC = apertura de EE. UU. en horario de invierno
    de Nueva York; ajustar en horario de verano). Una operación por día como máximo.
    """
    if len(df) < 2:
        return []
    tf = int((df.index[1] - df.index[0]).total_seconds() // 60)
    if tf <= 0 or or_minutes % tf:
        raise ValueError(f"El timeframe ({tf} min) debe dividir el rango de apertura ({or_minutes} min)")
    need_bars = or_minutes // tf
    hh, mm = (int(x) for x in session_start.split(":"))
    close = df["close"].to_numpy()
    signals: list[Signal] = []
    past_or_volume: list[float] = []

    for day in sorted({ts.normalize() for ts in df.index}):
        start = day + pd.Timedelta(hours=hh, minutes=mm)
        or_end = start + pd.Timedelta(minutes=or_minutes)
        end = start + pd.Timedelta(minutes=session_minutes)
        or_pos = np.flatnonzero((df.index >= start) & (df.index < or_end))
        if len(or_pos) != need_bars:
            continue
        or_bars = df.iloc[or_pos]
        or_high, or_low = float(or_bars["high"].max()), float(or_bars["low"].min())
        or_vol = float(or_bars["volume"].sum())
        history = past_or_volume[-lookback_days:]
        past_or_volume.append(or_vol)
        if len(history) < min(5, lookback_days):
            continue
        base = float(np.mean(history))
        if base <= 0 or or_vol / base < min_rvol:
            continue
        post = np.flatnonzero((df.index >= or_end) & (df.index < end))
        if len(post) < 2:
            continue
        last_pos = int(post[-1])
        for pos in post[:-1]:
            c = close[pos]
            if c > or_high:
                direction, stop = 1, or_low
            elif c < or_low:
                direction, stop = -1, or_high
            else:
                continue
            risk = (c - stop) * direction
            if risk <= 0:
                continue
            signals.append(Signal(
                setup="I1", direction=direction, entry=float(c), stop=float(stop),
                target=float(c + direction * rr * risk), entry_pos=int(pos),
                first_check_pos=int(pos) + 1, last_pos=last_pos, time=df.index[pos]))
            break
    return signals
