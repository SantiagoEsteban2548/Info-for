"""Informe diario en PDF: resumen, gráfico por activo con stop/objetivo y rango proyectado.

El rango proyectado es estadístico, no una predicción: banda de 1 desviación típica de la
volatilidad reciente (EWMA de retornos logarítmicos) alrededor del último cierre. Con retornos
normales cubre ~68% de los casos; en cripto las colas gordas hacen que cubra menos, y el propio
informe mide la cobertura histórica real de cada activo.
"""
from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .indicators import atr, ema  # noqa: E402
from .risk import position_size  # noqa: E402
from .weekly import Order, pending_orders  # noqa: E402

A4 = (8.27, 11.69)
UP, DOWN = "#1b7f5c", "#c0392b"
INK, MUTED, BLUE = "#222222", "#666666", "#1f5fbf"
DISCLAIMER = ("Ideas de análisis generadas automáticamente, no asesoramiento financiero. "
              "Tú decides y ejecutas. Los rangos son estadísticos, no predicciones.")


@dataclass
class ReportItem:
    name: str
    symbol: str
    df: pd.DataFrame                   # velas diarias cerradas
    order: Order | None = None
    technical: str = ""
    verdict: dict = field(default_factory=dict)
    decision: str = ""
    risk_pct: float = 1.0
    max_leverage: float = 3.0


def volatility(df: pd.DataFrame, span: int = 20) -> pd.Series:
    """Desviación típica diaria (EWMA) de los retornos logarítmicos."""
    r = np.log(df["close"]).diff()
    return r.ewm(span=span, adjust=False, min_periods=span).std()


def expected_range(df: pd.DataFrame, days: int = 1, k: float = 1.0) -> tuple[float, float]:
    """Banda de k desviaciones típicas a `days` días vista desde el último cierre."""
    sigma = float(volatility(df).iloc[-1])
    last = float(df["close"].iloc[-1])
    width = k * sigma * np.sqrt(days)
    return last * float(np.exp(-width)), last * float(np.exp(width))


def range_coverage(df: pd.DataFrame, k: float = 1.0, min_obs: int = 60) -> float | None:
    """Fracción de días en que el cierre siguiente cayó dentro de la banda de ese día."""
    sigma = volatility(df).to_numpy()
    close = df["close"].to_numpy()
    lo, hi = close[:-1] * np.exp(-k * sigma[:-1]), close[:-1] * np.exp(k * sigma[:-1])
    ok = ~np.isnan(sigma[:-1])
    if ok.sum() < min_obs:
        return None
    nxt = close[1:][ok]
    return float(((nxt >= lo[ok]) & (nxt <= hi[ok])).mean())


def conditional_plan(df: pd.DataFrame, symbol: str, rr: float = 2.0, atr_buffer: float = 0.1,
                     lookback: int = 3) -> tuple[Order, str]:
    """Plan hipotético para días sin setup S1: mismo esquema de entrada, stop y objetivo.

    El sesgo sale de la tendencia (cierre y EMA20 frente a EMA50). Entrada: ruptura del máximo
    (largo) o mínimo (corto) de la última vela; stop: extremo de las últimas `lookback` velas
    con un margen de ATR; objetivo a `rr`R. NO es una señal confirmada: es el nivel que habría
    que ver para que la idea exista.
    """
    close = df["close"]
    e20, e50 = float(ema(close, 20).iloc[-1]), float(ema(close, 50).iloc[-1])
    last, a = float(close.iloc[-1]), float(atr(df).iloc[-1])
    direction = 1 if last >= e50 else -1
    strong = (last > e50 and e20 > e50) or (last < e50 and e20 < e50)
    tail = df.iloc[-lookback:]
    if direction == 1:
        entry = float(df["high"].iloc[-1])
        stop = float(tail["low"].min()) - atr_buffer * a
    else:
        entry = float(df["low"].iloc[-1])
        stop = float(tail["high"].max()) + atr_buffer * a
    risk = (entry - stop) * direction
    if risk <= 0:
        risk = a
        stop = entry - direction * risk
    target = entry + direction * rr * risk
    bias = "tendencia clara" if strong else "tendencia mixta, sesgo débil"
    return Order(symbol, "COND", direction, entry, stop, float(target), df.index[-1]), bias


def effective_plan(item: "ReportItem") -> tuple[Order | None, bool, str]:
    """(orden, es_condicional, nota de sesgo). Siempre hay plan si hay velas suficientes."""
    if item.order:
        return item.order, False, ""
    if len(item.df) < 51:
        return None, False, ""
    order, bias = conditional_plan(item.df, item.symbol)
    return order, True, bias


def fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) >= 100 else f"{x:.4g}"


def _side(order: Order) -> str:
    return "largo" if order.direction == 1 else "corto"


def draw_candles(ax, df: pd.DataFrame) -> None:
    for i, (o, h, l, c) in enumerate(zip(df["open"], df["high"], df["low"], df["close"])):
        color = UP if c >= o else DOWN
        ax.vlines(i, l, h, color=color, linewidth=0.8, zorder=2)
        ax.add_patch(Rectangle((i - 0.32, min(o, c)), 0.64, max(abs(c - o), 1e-12),
                               facecolor=color, edgecolor=color, linewidth=0.5, zorder=3))


def draw_asset_page(fig, item: ReportItem, equity: float, lookback: int = 90, horizon: int = 5) -> None:
    df = item.df.iloc[-lookback:]
    full = item.df
    n = len(df)
    ax = fig.add_axes([0.09, 0.40, 0.86, 0.50])
    draw_candles(ax, df)
    e20, e50 = ema(full["close"], 20).iloc[-n:], ema(full["close"], 50).iloc[-n:]
    ax.plot(range(n), e20, color="#e08a00", linewidth=1.1, label="EMA 20", zorder=4)
    ax.plot(range(n), e50, color="#7a3fb0", linewidth=1.1, label="EMA 50", zorder=4)

    last = float(df["close"].iloc[-1])
    sigma = float(volatility(full).iloc[-1])
    ks = np.arange(0, horizon + 1)
    xs = n - 1 + ks
    for mult, alpha, label in ((2, 0.10, "rango 2σ"), (1, 0.22, "rango 1σ (~68%)")):
        ax.fill_between(xs, last * np.exp(-mult * sigma * np.sqrt(ks)),
                        last * np.exp(mult * sigma * np.sqrt(ks)),
                        color=BLUE, alpha=alpha, linewidth=0, label=label, zorder=1)
    ax.axvline(n - 0.5, color=MUTED, linewidth=0.6, linestyle=":")

    levels = [last]
    x_right = n + horizon + 0.3
    o, cond, bias = effective_plan(item)
    if o:
        levels += [o.entry, o.stop, o.target]
        ax.axhspan(min(o.entry, o.target), max(o.entry, o.target), color=UP, alpha=0.07, zorder=0)
        ax.axhspan(min(o.entry, o.stop), max(o.entry, o.stop), color=DOWN, alpha=0.07, zorder=0)
        tag = " (cond.)" if cond else ""
        for value, name, color, style in ((o.entry, "Entrada", BLUE, "-" if not cond else ":"),
                                          (o.stop, "Stop", DOWN, "--"),
                                          (o.target, "Objetivo", UP, "--")):
            ax.hlines(value, -1, x_right - 0.2, color=color, linewidth=1.2, linestyle=style,
                      alpha=0.7 if cond else 1.0, zorder=5)
            ax.text(x_right, value, f"{name}{tag} {fmt(value)}", color=color, fontsize=8, va="center",
                    fontweight="bold", zorder=6,
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5})
    lo2, hi2 = last * np.exp(-2 * sigma * np.sqrt(horizon)), last * np.exp(2 * sigma * np.sqrt(horizon))
    levels += [lo2, hi2, float(df["low"].min()), float(df["high"].max())]
    pad = (max(levels) - min(levels)) * 0.05
    ax.set_ylim(min(levels) - pad, max(levels) + pad)
    ax.set_xlim(-1, n + horizon + 34)
    step = max(1, n // 6)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks, [df.index[t].strftime("%d %b") for t in ticks], fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(alpha=0.2)
    ax.legend(loc="upper left", fontsize=7.5, ncol=2, frameon=False)
    fig.text(0.09, 0.93, f"{item.name} ({item.symbol}), velas diarias", fontsize=14,
             fontweight="bold", color=INK)
    fig.text(0.09, 0.915, f"Último cierre {fmt(last)} · {df.index[-1]:%Y-%m-%d} · "
             f"zona de proyección a la derecha ({horizon} días)", fontsize=8.5, color=MUTED)

    lines: list[str] = []
    lo1, hi1 = expected_range(full, 1)
    lo5, hi5 = expected_range(full, horizon)
    cov = range_coverage(full)
    lines.append(f"Rango 1σ mañana: {fmt(lo1)} a {fmt(hi1)}. Próximos {horizon} días: {fmt(lo5)} a {fmt(hi5)}. "
                 f"ATR(14): {fmt(float(atr(full).iloc[-1]))}.")
    if cov is not None:
        lines.append(f"Cobertura histórica de la banda 1σ en este activo: {cov:.0%} de los días "
                     f"(teórico ~68%; menos indica colas gordas).")
    if o:
        risk = abs(o.entry - o.stop)
        rr = abs(o.target - o.entry) / risk
        size = position_size(equity, item.risk_pct, o.entry, o.stop, item.max_leverage)
        setting = max(1, math.ceil(size["leverage"] - 1e-9))
        if cond:
            lines.append(f"PLAN CONDICIONAL, NO ACTIVADO ({bias}). No hay setup confirmado hoy; esto es el plan "
                         f"si el precio rompe el nivel de entrada.")
        lines.append(f"{'Plan' if cond else 'Idea'} {_side(o)}: entrada {fmt(o.entry)} (orden stop, se activa al "
                     f"romper), stop {fmt(o.stop)} ({risk / o.entry:.2%} del precio), objetivo {fmt(o.target)} "
                     f"(beneficio/riesgo {rr:.1f}). Válido {o.valid_bars} velas diarias.")
        lines.append(f"Tamaño con {equity:g} USD y {item.risk_pct:g}% de riesgo: {size['qty']:.4g} unidades, "
                     f"nocional {size['notional']:.2f} USD, apalancamiento efectivo {size['leverage']:.2f}x; "
                     f"configura {setting}x aislado (tope {item.max_leverage:g}x), margen {size['notional'] / setting:.2f} USD, "
                     f"riesgo real {size['risk_usd']:.2f} USD, ganancia al objetivo {size['risk_usd'] * rr:.2f} USD.")
        if size["capped_by_leverage"]:
            lines.append("Aviso: el tope de apalancamiento recortó el tamaño; el riesgo real es menor al pedido.")
    else:
        lines.append("Sin velas suficientes para calcular un plan.")
    if item.technical:
        lines.append("Técnico: " + item.technical)
    v = item.verdict
    if v:
        lines.append(f"Juez: {v.get('veredicto')}, convicción {v.get('conviccion')}, coincide con el técnico: "
                     f"{'sí' if v.get('coincide_con_tecnico') else 'no'}.")
        lines += [f"  • {r}" for r in v.get("razones", [])[:4]]
        lines += [f"  ! {a}" for a in v.get("alertas", [])[:3]]
    if item.decision:
        lines.append("Decisión: " + item.decision)
    y = 0.33
    for line in lines:
        for wrapped in textwrap.wrap(line, 105, subsequent_indent="    ") or [""]:
            if y < 0.05:
                break
            fig.text(0.09, y, wrapped, fontsize=8.6, color=INK, va="top")
            y -= 0.0185
        y -= 0.006
    fig.text(0.09, 0.025, DISCLAIMER, fontsize=7, color=MUTED)


def draw_summary(fig, items: list[ReportItem], now: datetime, equity: float, broker_name: str = "") -> None:
    fig.text(0.09, 0.94, f"Informe diario {now:%Y-%m-%d}", fontsize=18, fontweight="bold", color=INK)
    sub = f"Swing sobre velas diarias · capital {equity:g} USD" + (f" · ejecución: {broker_name}" if broker_name else "")
    fig.text(0.09, 0.915, sub, fontsize=9.5, color=MUTED)
    header = ["Activo", "Cierre", "Idea", "Entrada", "Stop", "Objetivo", "Apal.", "Rango 1σ 5 días"]
    rows = []
    for it in items:
        lo5, hi5 = expected_range(it.df, 5)
        o, cond, _ = effective_plan(it)
        lev = "-"
        if o:
            lev = f"{position_size(equity, it.risk_pct, o.entry, o.stop, it.max_leverage)['leverage']:.2f}x"
        idea = "sin plan" if not o else (f"cond. {_side(o)}" if cond else _side(o))
        rows.append([it.name, fmt(float(it.df["close"].iloc[-1])), idea,
                     fmt(o.entry) if o else "-", fmt(o.stop) if o else "-", fmt(o.target) if o else "-",
                     lev, f"{fmt(lo5)} a {fmt(hi5)}"])
    ax = fig.add_axes([0.05, 0.55, 0.90, 0.33])
    ax.axis("off")
    table = ax.table(cellText=rows or [["-"] * len(header)], colLabels=header, loc="upper center",
                     cellLoc="center", colWidths=[0.15, 0.10, 0.12, 0.10, 0.10, 0.10, 0.08, 0.21])
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.8)
    for (r, _c), cell in table.get_celld().items():
        cell.set_edgecolor("#cccccc")
        if r == 0:
            cell.set_facecolor("#eef1f6")
            cell.set_text_props(fontweight="bold")
    n_ideas = sum(1 for it in items if it.order)
    notes = [
        f"Ideas con setup técnico confirmado hoy: {n_ideas} de {len(items)} activos.",
        "\"cond.\" = plan condicional: no hay setup confirmado, es el plan si el precio rompe la entrada. No operar hasta que se active.",
        "Cada orden usa un stop por estructura del gráfico y objetivo de 2R; el tamaño sale de la distancia al stop.",
        "El rango 1σ es una banda estadística de la volatilidad reciente alrededor del último cierre, no una predicción.",
        "Las páginas siguientes muestran, por activo, las velas, EMA 20/50, entrada, stop, objetivo y la zona de rango proyectado.",
    ]
    y = 0.885 - 0.03 * (len(rows) + 1) - 0.04
    for line in notes:
        for wrapped in textwrap.wrap(line, 105):
            fig.text(0.09, y, wrapped, fontsize=9, color=INK)
            y -= 0.02
        y -= 0.006
    fig.text(0.09, 0.025, DISCLAIMER, fontsize=7, color=MUTED)


def build_daily_pdf(items: list[ReportItem], path: str | Path, now: datetime | None = None,
                    equity: float = 100.0, broker_name: str = "") -> Path:
    now = now or datetime.now()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=A4)
        draw_summary(fig, items, now, equity, broker_name)
        pdf.savefig(fig)
        plt.close(fig)
        for item in items:
            fig = plt.figure(figsize=A4)
            draw_asset_page(fig, item, equity)
            pdf.savefig(fig)
            plt.close(fig)
        info = pdf.infodict()
        info["Title"] = f"Informe diario {now:%Y-%m-%d}"
        info["Author"] = "tradingagent"
    return path


def items_from_reports(reports, candles: dict[str, pd.DataFrame], names: dict[str, str] | None = None,
                       equity: float = 100.0) -> list[ReportItem]:
    """Adaptador para `pipeline.AssetReport`: reúne velas, orden vigente, veredicto y decisión."""
    items = []
    for r in reports:
        df = candles.get(r.symbol)
        if df is None or len(df) < 51:
            continue
        orders = pending_orders(df, r.symbol)
        items.append(ReportItem(
            name=(names or {}).get(r.asset, r.asset), symbol=r.symbol, df=df,
            order=orders[0] if orders else None, technical=r.technical, verdict=r.verdict or {},
            decision=r.decision))
    return items


def main(argv: list[str] | None = None) -> None:
    """python -m tradingagent.report --assets BTC,ETH,XAU,XAG --out state/informe.pdf"""
    import argparse

    from . import pipeline
    from .assets import ASSETS

    p = argparse.ArgumentParser(description="Informe diario en PDF (velas, stop, objetivo y rango)")
    p.add_argument("--assets", default="BTC,ETH,XAU,XAG")
    p.add_argument("--out", default=None, help="ruta del PDF (por defecto state/informe_AAAA-MM-DD.pdf)")
    p.add_argument("--equity", type=float, default=100.0)
    args = p.parse_args(argv)
    now = datetime.now()
    items = []
    for key in (k.strip().upper() for k in args.assets.split(",") if k.strip()):
        asset = ASSETS[key]
        df = pipeline.load_daily_candles(asset)
        orders = pending_orders(df, asset.symbol)
        items.append(ReportItem(asset.name, asset.symbol, df, orders[0] if orders else None,
                                decision="" if orders else "sin setup técnico hoy",
                                risk_pct=min(1.0, asset.max_risk_pct)))
    out = build_daily_pdf(items, args.out or f"state/informe_{now:%Y-%m-%d}.pdf", now, args.equity)
    print(out)


if __name__ == "__main__":
    main()
