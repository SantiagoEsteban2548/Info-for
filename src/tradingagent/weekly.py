"""Modo fin de semana: órdenes condicionales para la semana y vigilancia entre semana.

El sábado se escanean velas diarias cerradas y se generan órdenes con entrada, stop y
objetivo (S1, swing). De lunes a viernes solo se revisa si cada orden sigue vigente.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .backtest import covers_costs
from .indicators import atr, ema
from .risk import position_size


@dataclass(frozen=True)
class Order:
    symbol: str
    setup: str
    direction: int            # 1 largo, -1 corto
    entry: float              # orden stop: se activa al romper este nivel
    stop: float
    target: float
    created: pd.Timestamp     # vela de señal
    valid_bars: int = 7       # velas diarias de vigencia


def pending_orders(df: pd.DataFrame, symbol: str, rr: float = 2.0, atr_buffer: float = 0.1,
                   lookback: int = 3, valid_bars: int = 7, allow_short: bool = True,
                   fee_bps: float = 5.0, slippage_bps: float = 2.0,
                   min_reward_cost_ratio: float = 5.0) -> list[Order]:
    """Órdenes S1 vigentes al cierre de la última vela diaria.

    Mira las últimas `lookback` velas como posibles velas de señal y descarta las que ya
    activaron la entrada o ya tocaron el stop. Devuelve a lo sumo la señal más reciente.
    """
    n = len(df)
    if n < 51:
        return []
    close, high, low = (df[c].to_numpy() for c in ("close", "high", "low"))
    e20, e50 = ema(df["close"], 20).to_numpy(), ema(df["close"], 50).to_numpy()
    a = atr(df).to_numpy()
    for i in range(n - 1, max(49, n - 1 - lookback), -1):
        long_ok = close[i] > e50[i] and e20[i] > e50[i] and low[i] <= e20[i] < close[i]
        short_ok = (allow_short and close[i] < e50[i] and e20[i] < e50[i]
                    and high[i] >= e20[i] > close[i])
        if long_ok:
            direction, entry, stop = 1, float(high[i]), float(low[i] - atr_buffer * a[i])
        elif short_ok:
            direction, entry, stop = -1, float(low[i]), float(high[i] + atr_buffer * a[i])
        else:
            continue
        risk = (entry - stop) * direction
        if risk <= 0:
            continue
        later_high, later_low = high[i + 1:], low[i + 1:]
        triggered = (later_high >= entry).any() if direction == 1 else (later_low <= entry).any()
        stopped = (later_low <= stop).any() if direction == 1 else (later_high >= stop).any()
        if triggered or stopped:
            continue
        target = entry + direction * rr * risk
        if not covers_costs(entry, target, fee_bps, slippage_bps, min_reward_cost_ratio):
            continue
        return [Order(symbol, "S1", direction, entry, stop, float(target), df.index[i], valid_bars)]
    return []


def order_status(order: Order, df: pd.DataFrame) -> str:
    """Estado de una orden con las velas diarias cerradas hasta hoy.

    pendiente | activada | invalidada | expirada | cerrada en stop | cerrada en objetivo
    Si una vela toca a la vez la entrada y el stop, se asume stop.
    """
    after = df[df.index > order.created]
    e50 = ema(df["close"], 50)
    d = order.direction
    triggered = False
    for ts, bar in after.iterrows():
        if not triggered:
            hit_entry = bar["high"] >= order.entry if d == 1 else bar["low"] <= order.entry
            hit_stop = bar["low"] <= order.stop if d == 1 else bar["high"] >= order.stop
            if hit_entry:
                triggered = True
            elif hit_stop:
                return "invalidada"
            elif (bar["close"] < e50[ts]) if d == 1 else (bar["close"] > e50[ts]):
                return "invalidada"
        if triggered:
            hit_stop = bar["low"] <= order.stop if d == 1 else bar["high"] >= order.stop
            hit_target = bar["high"] >= order.target if d == 1 else bar["low"] <= order.target
            if hit_stop:
                return "cerrada en stop"
            if hit_target:
                return "cerrada en objetivo"
    if triggered:
        return "activada"
    return "expirada" if len(after) >= order.valid_bars else "pendiente"


def format_report(orders: list[Order], equity: float, risk_pct: float = 1.0,
                  max_leverage: float = 3.0, min_notional: float = 5.0) -> str:
    """Informe semanal en Markdown con tamaño sugerido por riesgo."""
    lines = ["# Ideas de la semana (swing, velas diarias)", ""]
    if not orders:
        return "\n".join(lines + ["Sin ideas válidas esta semana. No operar también es una decisión.", ""])
    lines += ["| Activo | Lado | Entrada | Stop | Objetivo | Tamaño | Nocional USD | Apalanc. | Notas |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for o in orders:
        size = position_size(equity, risk_pct, o.entry, o.stop, max_leverage)
        notes = []
        if size["capped_by_leverage"]:
            notes.append(f"tope {max_leverage:g}x: riesgo real {size['risk_usd']:.2f} USD")
        if size["notional"] < min_notional:
            notes.append("bajo el nocional mínimo del exchange")
        lines.append(
            f"| {o.symbol} | {'largo' if o.direction == 1 else 'corto'} | {o.entry:.4g} | "
            f"{o.stop:.4g} | {o.target:.4g} | {size['qty']:.4g} | {size['notional']:.2f} | "
            f"{size['leverage']:.2f}x | {'; '.join(notes) or '-'} |")
    lines += ["", f"Capital {equity:g} USD, riesgo {risk_pct:g}% por idea. Cada orden vale para "
              f"{orders[0].valid_bars} velas diarias y se cancela si el precio toca el stop antes de activarse o la tendencia se rompe.",
              "Ideas de análisis, no asesoramiento financiero: tú decides y ejecutas.", ""]
    return "\n".join(lines)
