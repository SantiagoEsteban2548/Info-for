# Info-for: agente de recomendaciones de trading

Agente que propone ideas de trading (swing e intradía) combinando análisis técnico con fundamentales y noticias.
El agente **recomienda; tú decides y ejecutas**. No es asesoramiento financiero.

Plan completo: https://claude.ai/artifact/63dFqUVkMd41zQLBr1YciQ

## Estado

Base de código de la fase 2 (aún sin recomendaciones automáticas):

| Módulo | Qué hace |
| --- | --- |
| `data.py` | Velas OHLCV de Binance y BingX con ccxt, sin claves |
| `indicators.py` | EMA y ATR |
| `strategies.py` | S1 (pullback a EMA 20, swing) e I1 (ruptura del rango de apertura de 15 min, intradía) |
| `backtest.py` | Backtest con comisiones, deslizamiento y resultados en R; avisa si hay menos de 30 operaciones |
| `weekly.py` | Modo fin de semana: órdenes condicionales S1 sobre velas diarias, estado de cada orden y informe semanal en Markdown |
| `report.py` | Informe diario en PDF: resumen, velas con EMA 20/50, entrada, stop, objetivo y rango proyectado de 1σ por activo (`python -m tradingagent.report`) |
| `risk.py` | Tamaño de posición a partir del stop, con tope de apalancamiento |

## Uso

```bash
pip install -e ".[dev]"
pytest
python -m tradingagent.cli --exchange binance --symbol BTC/USDT --strategy s1 --days 730
python -m tradingagent.cli --exchange bingx --symbol "NCCOGOLD2USD/USDT:USDT" --strategy i1 --days 60

# Informe semanal de órdenes (para correr los sábados)
python -m tradingagent.cli --weekly --exchange binance --symbols "BTC/USDT,ETH/USDT" --equity 100 --risk-pct 1
```

Por ahora el intradía (I1) está en pausa: la versión 1 es swing con velas diarias, con órdenes que se cargan el fin de semana.

## Pipeline automático (noticias → debate → juez → ejecución)

```bash
pip install -e ".[dev,llm]"
python -m tradingagent.pipeline run --broker paper --llm anthropic   # una vez al día, tras las 00:00 UTC
```

Recoge noticias de Reddit, X (vía puente RSS), TradingView y medios; tres agentes (alcista, bajista,
lateral) debaten; un juez lo cruza con el setup técnico, y una regla en código decide si se opera.
Ejecución en paper por defecto, testnet/demo de Binance y BingX opcional, real bloqueado.
Detalle en [docs/pipeline.md](docs/pipeline.md).

## Limitaciones conocidas

- La descarga en vivo desde Binance y BingX **no se ha probado**: el entorno donde se escribió el código no tiene acceso a esas APIs. Los tests usan un exchange simulado y datos sintéticos.
- En BingX (ccxt) el oro es `NCCOGOLD2USD/USDT:USDT` y la plata `NCCOXAG2USD/USDT:USDT` (confirmado contra el mercado real el 2026-11-04). Si Binance no responde (bloqueo regional 451), las velas de BTC/ETH se bajan de BingX.
- I1 usa 13:30 UTC como apertura (EE. UU. en invierno); en horario de verano son las 12:30 UTC.
- Los resultados del backtest son hipótesis a validar, no una promesa de rentabilidad.
