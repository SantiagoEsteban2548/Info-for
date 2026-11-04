# Pipeline automático: noticias → debate → juez → ejecución

## Flujo (todos los días, tras el cierre de la vela diaria de las 00:00 UTC)

0. **Puesta al día (código).** Antes de analizar, las órdenes abiertas en paper se actualizan
   con las velas nuevas: se activan, cierran en stop u objetivo, se invalidan o vencen (7 velas).
1. **Técnico (código).** `weekly.pending_orders` busca un setup S1 en cada activo. Si no hay
   setup, si el activo ya tiene posición u orden abierta, o si esa misma señal ya se colocó
   otro día, el activo se salta y no se gasta nada en modelo.
2. **Noticias (código).** `news.collect_news` junta los últimos 7 días de:
   - Reddit (JSON público, sin clave).
   - X, a través de un puente RSS que configures (`TRADINGAGENT_X_RSS`, p. ej. RSSHub propio).
     X no tiene API de lectura gratuita; sin puente, esta fuente queda vacía.
   - TradingView: feed RSS de ideas por símbolo. No es oficial y puede dejar de funcionar.
   - RSS de medios: CoinDesk y Cointelegraph (cripto), Kitco y FXStreet (metales).
   Una fuente caída queda anotada en el informe y el resto sigue.
3. **Debate (modelo).** Tres agentes, alcista, bajista y lateral, exponen su caso citando las
   noticias (N1, N2...) y en una segunda ronda rebaten a los otros dos.
4. **Juez (modelo).** Lee el debate y el contexto técnico y da veredicto (alcista, bajista o
   lateral), convicción de 0 a 100, si coincide con el setup y alertas.
5. **Decisión (código, `debate.decide`).** Se opera solo si hay setup, el juez coincide con su
   dirección y la convicción es al menos 60. Riesgo 1%; 3% si la convicción es al menos 80 y no
   hay alertas, con tope por activo (2% cripto, 3% metales).
6. **Límites (código).** Máximo 2 posiciones entre todos los exchanges, apalancamiento máximo
   3x aislado, nocional mínimo 5 USD, no repetir activo, no abrir nada si existe el archivo
   `STOP` o si la pérdida acumulada supera el 10% del capital.
7. **Ejecución.** Entrada stop-market + stop loss + take profit.

## Por qué el modelo no puede operar solo

Los agentes y el juez nunca proponen precios ni crean operaciones: solo confirman o vetan una
idea técnica que salió del código. Las noticias son texto de terceros y se pasan como datos;
los prompts avisan que no se obedecen instrucciones que aparezcan dentro. Así, un post
manipulado en Reddit o X puede como mucho frenar una operación, no inventarla.

## Modos de ejecución

| Modo | Qué hace | Requisitos |
| --- | --- | --- |
| `paper` (por defecto) | Libro local `state/paper_ledger.json`; `update` lo cierra con velas reales | Nada |
| `testnet` | Cuenta demo de Binance (futuros USDⓈ-M) o BingX (VST) | Claves de la cuenta demo en `BINANCE_API_KEY/SECRET`, `BINGX_API_KEY/SECRET` |
| `live` | Dinero real | **Bloqueado.** Solo tras resultados en paper/testnet y tu visto bueno explícito |

## Coste

Todo es gratis salvo el modelo. Con `--llm anthropic` son 7 llamadas por activo con setup
(6 de debate + juez). Al correr a diario, una misma señal puede debatirse hasta 3 días seguidos
si el juez la rechaza (con noticias nuevas cada día); una vez colocada ya no se debate. Con el modelo por defecto (`claude-opus-5-5`, esfuerzo bajo) es una
estimación de unos 0,25 USD por activo con setup y día; con `TRADINGAGENT_MODEL=claude-haiku-5-5` baja
a menos de 1 céntimo, con análisis menos fino. Necesita `ANTHROPIC_API_KEY`. `--llm keywords` es un sustituto
gratuito por conteo de palabras, mucho más tosco, útil para probar el circuito.

## Programarlo en Windows

Una tarea diaria a las 21:15 hora de Argentina (00:15 UTC, justo después del cierre de la vela
diaria). Cada corrida ya actualiza las órdenes antes de buscar ideas, así que no hace falta otra.

```bat
schtasks /create /tn "tradingagent-diario" /sc daily /st 21:15 ^
  /tr "cmd /c cd /d C:\ruta\Info-for && python -m tradingagent.pipeline run --broker paper"
```

La PC tiene que estar encendida a esa hora. Si no lo está, Windows no la corre ese día; se
puede marcar "Ejecutar la tarea lo antes posible después de perder un inicio programado" en
el Programador de tareas.

## Pendiente

- Probar contra Reddit, TradingView, los RSS y los testnets reales (el entorno de desarrollo no
  llega a esas APIs).
- Enviar el informe por email y Telegram.
- En testnet/real: cancelar órdenes vencidas o invalidadas entre semana.
