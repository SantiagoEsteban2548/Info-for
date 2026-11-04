"""Backtest vela a vela con comisiones, deslizamiento y resultados en múltiplos de R."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategies import Signal

MIN_TRADES_FOR_CONCLUSIONS = 30


def covers_costs(entry: float, target: float, fee_bps: float, slippage_bps: float,
                 min_ratio: float) -> bool:
    """True si el beneficio hasta el objetivo cubre al menos min_ratio veces el coste de ida y vuelta."""
    cost = (entry + target) * (fee_bps + slippage_bps) / 1e4
    return abs(target - entry) >= min_ratio * cost


@dataclass
class Result:
    trades: pd.DataFrame
    stats: dict


def _exit(df: pd.DataFrame, s: Signal) -> tuple[int, float, str]:
    """Primera salida entre stop, objetivo o tiempo. Si una vela toca ambos, gana el stop."""
    high, low, open_, close = (df[c].to_numpy() for c in ("high", "low", "open", "close"))
    for j in range(s.first_check_pos, s.last_pos + 1):
        if s.direction == 1:
            if low[j] <= s.stop:
                return j, float(min(open_[j], s.stop)), "stop"
            if high[j] >= s.target:
                return j, s.target, "objetivo"
        else:
            if high[j] >= s.stop:
                return j, float(max(open_[j], s.stop)), "stop"
            if low[j] <= s.target:
                return j, s.target, "objetivo"
    return s.last_pos, float(close[s.last_pos]), "tiempo"


def run_backtest(df: pd.DataFrame, signals: list[Signal], fee_bps: float = 5.0,
                 slippage_bps: float = 2.0, min_reward_cost_ratio: float = 5.0) -> Result:
    """Simula las señales de a una posición por vez.

    fee_bps y slippage_bps se cobran en cada lado (entrada y salida). Se descartan
    las ideas cuyo objetivo no cubra al menos min_reward_cost_ratio veces el coste.
    """
    cost_rate = (fee_bps + slippage_bps) / 1e4
    rows = []
    busy_until = -1
    for s in sorted(signals, key=lambda x: x.entry_pos):
        if s.entry_pos <= busy_until:
            continue
        risk = (s.entry - s.stop) * s.direction
        if risk <= 0 or not covers_costs(s.entry, s.target, fee_bps, slippage_bps,
                                         min_reward_cost_ratio):
            continue
        j, exit_price, reason = _exit(df, s)
        gross = (exit_price - s.entry) * s.direction
        cost = (s.entry + exit_price) * cost_rate
        rows.append({
            "setup": s.setup, "direction": "largo" if s.direction == 1 else "corto",
            "entry_time": df.index[s.entry_pos], "exit_time": df.index[j],
            "entry": s.entry, "stop": s.stop, "target": s.target, "exit": exit_price,
            "reason": reason, "r": (gross - cost) / risk,
        })
        busy_until = j
    trades = pd.DataFrame(rows, columns=[
        "setup", "direction", "entry_time", "exit_time", "entry", "stop", "target",
        "exit", "reason", "r"])
    return Result(trades, summarize(trades))


def summarize(trades: pd.DataFrame) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "warning": "sin operaciones: no hay nada que concluir"}
    r = trades["r"].to_numpy()
    wins, losses = r[r > 0], r[r < 0]
    equity = np.cumsum(r)
    drawdown = np.max(np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:] - equity)
    stats = {
        "trades": n,
        "win_rate": float((r > 0).mean()),
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.size else float("inf"),
        "max_drawdown_r": float(drawdown),
    }
    if n < MIN_TRADES_FOR_CONCLUSIONS:
        stats["warning"] = f"menos de {MIN_TRADES_FOR_CONCLUSIONS} operaciones: la muestra no prueba nada"
    return stats
