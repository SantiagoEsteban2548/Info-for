"""Gestión de riesgo: tamaño de posición a partir del stop."""
from __future__ import annotations


def position_size(equity: float, risk_pct: float, entry: float, stop: float,
                  max_leverage: float = 3.0) -> dict:
    """Tamaño por riesgo: qty = riesgo en USD / distancia al stop.

    El apalancamiento solo limita el nocional máximo; nunca aumenta el riesgo.
    """
    if equity <= 0 or not 0 < risk_pct <= 100:
        raise ValueError("equity debe ser > 0 y risk_pct estar en (0, 100]")
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("entry y stop no pueden coincidir")
    risk_usd = equity * risk_pct / 100
    qty = risk_usd / distance
    max_qty = equity * max_leverage / entry
    capped = qty > max_qty
    qty = min(qty, max_qty)
    return {
        "qty": qty,
        "notional": qty * entry,
        "risk_usd": qty * distance,
        "leverage": qty * entry / equity,
        "capped_by_leverage": capped,
    }
