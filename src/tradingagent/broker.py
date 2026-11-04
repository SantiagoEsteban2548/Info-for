"""Ejecución: paper (local), testnet/demo del exchange y, más adelante, real.

Orden de prioridades:
1. `PaperBroker` simula en un archivo JSON con las velas reales. Es el modo por defecto.
2. `CCXTBroker(mode="testnet")` manda órdenes a la cuenta demo de Binance (demo trading de
   futuros USDⓈ-M) o BingX (VST, USDT virtuales). Necesita claves de API de esa cuenta demo.
3. `mode="live"` está bloqueado salvo que TRADINGAGENT_ALLOW_LIVE valga exactamente
   LIVE_CONFIRMATION. Solo se activa tras resultados en paper y el visto bueno explícito.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from .weekly import Order, order_status

LIVE_CONFIRMATION = "entiendo-que-es-dinero-real"
OPEN_STATES = ("pendiente", "activada")


@dataclass
class Placement:
    symbol: str
    setup: str
    direction: int
    entry: float
    stop: float
    target: float
    qty: float
    risk_usd: float
    leverage: float
    created: str              # ISO de la vela de señal
    valid_bars: int
    status: str = "pendiente"
    pnl_usd: float = 0.0
    broker_ids: tuple[str, ...] = ()

    def order(self) -> Order:
        return Order(self.symbol, self.setup, self.direction, self.entry, self.stop, self.target,
                     pd.Timestamp(self.created), self.valid_bars)


class PaperBroker:
    """Libro local de órdenes simuladas. Las cierra con las mismas reglas que el informe semanal."""

    def __init__(self, path: str | Path, fee_bps: float = 5.0):
        self.path, self.fee_bps = Path(path), fee_bps
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[Placement]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return [Placement(**{**r, "broker_ids": tuple(r.get("broker_ids", ()))}) for r in rows]

    def _save(self, rows: list[Placement]) -> None:
        self.path.write_text(json.dumps([asdict(r) for r in rows], indent=2, ensure_ascii=False),
                             encoding="utf-8")

    def all(self) -> list[Placement]:
        return self._load()

    def open_positions(self) -> list[Placement]:
        return [p for p in self._load() if p.status in OPEN_STATES]

    def place(self, p: Placement) -> Placement:
        rows = self._load()
        if any(r.symbol == p.symbol and r.created == p.created for r in rows):
            return p                    # idempotente: la misma señal no se coloca dos veces
        p.broker_ids = (f"paper-{len(rows) + 1}",)
        rows.append(p)
        self._save(rows)
        return p

    def update(self, candles: dict[str, pd.DataFrame]) -> list[Placement]:
        """Recalcula el estado de cada orden abierta con las velas diarias cerradas."""
        rows = self._load()
        for p in rows:
            if p.status not in OPEN_STATES or p.symbol not in candles:
                continue
            p.status = order_status(p.order(), candles[p.symbol])
            if p.status in ("cerrada en stop", "cerrada en objetivo"):
                exit_price = p.stop if p.status == "cerrada en stop" else p.target
                gross = (exit_price - p.entry) * p.direction * p.qty
                fees = (p.entry + exit_price) * p.qty * self.fee_bps / 1e4
                p.pnl_usd = gross - fees
        self._save(rows)
        return rows

    def realized_pnl(self) -> float:
        return sum(p.pnl_usd for p in self._load())


class LiveTradingBlocked(RuntimeError):
    pass


def make_ccxt_client(exchange_id: str, mode: str):
    """Cliente ccxt autenticado. Claves en variables de entorno, nunca en el repo:
    BINANCE_API_KEY / BINANCE_API_SECRET, BINGX_API_KEY / BINGX_API_SECRET."""
    import ccxt

    if mode not in ("testnet", "live"):
        raise ValueError("mode debe ser testnet o live")
    if mode == "live" and os.environ.get("TRADINGAGENT_ALLOW_LIVE") != LIVE_CONFIRMATION:
        raise LiveTradingBlocked(
            "Trading real bloqueado. Primero resultados en paper/testnet y tu visto bueno; "
            f"luego TRADINGAGENT_ALLOW_LIVE={LIVE_CONFIRMATION}.")
    prefix = exchange_id.upper()
    cls = {"binance": ccxt.binanceusdm, "bingx": ccxt.bingx}[exchange_id]
    client = cls({"apiKey": os.environ.get(f"{prefix}_API_KEY", ""),
                  "secret": os.environ.get(f"{prefix}_API_SECRET", ""),
                  "enableRateLimit": True, "options": {"defaultType": "swap"}})
    if mode == "testnet":
        if exchange_id == "binance":
            client.enable_demo_trading(True)
        else:
            client.set_sandbox_mode(True)   # BingX VST
    return client


class CCXTBroker:
    """Órdenes condicionales en perpetuos: entrada stop-market + stop loss + take profit.

    Sin probar contra los exchanges reales (el entorno de desarrollo no llega a sus APIs):
    validar primero en testnet con cantidades mínimas.
    """

    def __init__(self, client, mode: str, max_leverage: float = 3.0):
        if mode == "live" and os.environ.get("TRADINGAGENT_ALLOW_LIVE") != LIVE_CONFIRMATION:
            raise LiveTradingBlocked("Trading real bloqueado")
        self.client, self.mode, self.max_leverage = client, mode, max_leverage

    def open_positions(self) -> list[dict]:
        positions = [p for p in self.client.fetch_positions() if float(p.get("contracts") or 0) > 0]
        orders = self.client.fetch_open_orders()
        entry_orders = [o for o in orders if not (o.get("reduceOnly") or
                                                  o.get("info", {}).get("reduceOnly") in (True, "true"))]
        return positions + entry_orders

    def place(self, p: Placement) -> Placement:
        c, sym = self.client, p.symbol
        c.load_markets()
        lev = max(1, min(int(self.max_leverage), int(p.leverage) + 1))
        try:
            c.set_margin_mode("isolated", sym)
        except Exception as exc:     # ya estaba en aislado: el exchange devuelve error
            if "isolated" not in str(exc).lower() and "no need" not in str(exc).lower():
                raise
        c.set_leverage(lev, sym)
        qty = float(c.amount_to_precision(sym, p.qty))
        side, exit_side = ("buy", "sell") if p.direction == 1 else ("sell", "buy")
        entry = c.create_order(sym, "market", side, qty, None,
                               {"triggerPrice": float(c.price_to_precision(sym, p.entry))})
        sl = c.create_order(sym, "market", exit_side, qty, None,
                            {"stopLossPrice": float(c.price_to_precision(sym, p.stop)),
                             "reduceOnly": True})
        tp = c.create_order(sym, "market", exit_side, qty, None,
                            {"takeProfitPrice": float(c.price_to_precision(sym, p.target)),
                             "reduceOnly": True})
        p.qty = qty
        p.broker_ids = tuple(str(o.get("id")) for o in (entry, sl, tp))
        return p
