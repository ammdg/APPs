# Alertas de bolsa (mercado americano)

Revisa una lista de acciones de EE. UU. con velas diarias al cierre del mercado y te avisa (Telegram o
[ntfy](https://ntfy.sh)) con señales WATCH / BUY de una estrategia por estados, y opcionalmente
con reglas sueltas sobre indicadores técnicos.

- Datos: Yahoo Finance vía la librería [yfinance](https://github.com/ranaroussi/yfinance). **No es
  una API oficial**: puede fallar o cambiar sin previo aviso. Precios ajustados por dividendos y splits.
- Por defecto avisa cuando la regla **pasa de no cumplirse a cumplirse** (p. ej. el día en que el RSI
  baja de 30), no todos los días que siga por debajo. Nunca avisa dos veces de la misma regla con la
  misma vela.
- Es una herramienta informativa, no consejo de inversión.

## 1. Estrategia WATCH / BUY: `estrategia_score.py`

La lógica está en `estrategia_score.py` (código aportado, sin cambios). Los umbrales se cambian
arriba de ese fichero (`SCORE_WATCH`, `SCORE_BUY`, `RSI_CORRECTION`…). Resumen:

1. **Filtro de tendencia**: cierre > SMA200, SMA50 > SMA200 y SMA200 subiendo respecto a hace 20
   sesiones. Si no se cumple, no hay señal y el estado vuelve a NEUTRAL.
2. **Puntuación (0-100)**: tendencia 30 · caída entre -5 % y -15 % desde el máximo de 60 sesiones 20 ·
   RSI mínimo de 10 sesiones < 45: 10 · RSI cruza 50 hoy: 10 · cierre > SMA20: 7,5 · cierre > máximo
   de los 3 cierres anteriores: 7,5 · volumen > 1,2 × media de 20: 15.
3. **WATCH**: puntuación ≥ 60. **BUY**: puntuación ≥ 70, RSI cruza 50 hoy y cierre > SMA20 y > máximo
   de los 3 cierres anteriores.
4. **Estados**: NEUTRAL → WATCH_ACTIVE → BUY_ACTIVE. Cada señal se emite solo al entrar en el estado;
   tras un BUY no vuelve a avisar hasta que la puntuación baje de 60 o se rompa la tendencia.

El programa ejecuta la estrategia sobre los 2 años descargados de cada acción y avisa si la
**última vela** es WATCH o BUY. El estado se reconstruye así en cada ejecución; no se guarda.

`python alertas_bolsa.py --ver AAPL` muestra la puntuación actual y las últimas señales que habría
dado en el histórico.

Para desactivarla: `"estrategia": {"activa": false}` en `config.json`.

## 2. Qué acciones: lista fija de las 500 de mayor capitalización

`universo_fijo.json` contiene las 500 empresas de mayor capitalización cotizadas en EE. UU. (NYSE,
Nasdaq y NYSE American) **al cierre del viernes 25/09/2026**. Es fija: el análisis diario solo la
lee y no la recalcula. Cada acción lleva su posición, nombre, país, cierre y capitalización.

Cómo se calculó (`universo.py`):

1. Del buscador de acciones de nasdaq.com (no es una API oficial): todas las acciones con su
   capitalización y último precio → número de acciones = capitalización / precio.
2. Capitalización al cierre del 25/09 = número de acciones × cierre sin ajustar de ese día (Yahoo).
3. Una sola clase de acción por empresa: la empresa se ordena por su capitalización y se vigila la
   clase con más dinero negociado (BRK-B y no BRK-A). Se detecta por el nombre, así que en algún
   caso raro podría fallar.
4. Solo acciones: sin ETF, preferentes ni warrants. **Incluye empresas extranjeras** que cotizan en
   EE. UU. (ADR y similares: TSMC, ASML…); en la lista actual son 139 de 500.

Limitaciones: el número de acciones es el que usa nasdaq.com el día en que se generó (28/09/2026),
no necesariamente el del 25/09, así que el orden cerca del puesto 500 es aproximado.

Para regenerarla (otra fecha, o solo empresas de EE. UU.): *Actions → Generar lista de acciones
(bolsa) → Run workflow*, o en local `python universo.py --fecha 2026-09-25 [--solo-eeuu]`.

Los `tickers` de `config.json` se añaden a la lista. Si quitas el bloque `universo`, solo se vigilan
esos. Con 500 acciones, Yahoo se consulta en tandas de 100 y la ejecución tarda unos minutos.

## 3. Reglas sueltas (opcionales, desactivadas)

```json
{
  "tickers": ["AAPL", "MSFT", "NVDA", "BRK-B"],
  "reglas": [
    { "nombre": "RSI sobrevendido", "si": "rsi14 < 30" },
    { "nombre": "Cruce dorado", "si": "sma50 > sma200" },
    { "nombre": "Caída fuerte", "si": "var1 < -5" },
    { "nombre": "Rebote con volumen", "si": "var1 > 3 and vol_rel > 2" },
    { "nombre": "Apple barata", "si": "precio < 150", "tickers": ["AAPL"] },
    { "nombre": "Desactivada", "si": "precio >= max52", "activa": false }
  ]
}
```

- `tickers`: símbolos de Yahoo (Berkshire es `BRK-B`). La lista incluida es una selección de grandes
  empresas de EE. UU. (solo acciones, sin ETF); **no es un índice oficial**, cámbiala a tu gusto.
- `si`: la condición. Admite números, variables, `+ - * /`, `< <= > >=` (también encadenadas:
  `20 < rsi14 < 30`) y `and`, `or`, `not`.
- `tickers` dentro de una regla: solo se aplica a esos. Sin él, a todos.
- `"modo": "siempre"`: avisa en cada vela en que se cumpla, no solo al empezar a cumplirse.
- `"activa": false`: la desactiva sin borrarla.

### Variables

| Variable | Qué es |
|---|---|
| `precio`, `apertura`, `maximo`, `minimo`, `volumen` | datos de la vela del día |
| `smaN`, `emaN` | media móvil simple / exponencial de N sesiones (`sma50`, `ema20`…) |
| `rsiN` | RSI de Wilder de N sesiones (`rsi14`) |
| `varN` | variación % en N sesiones (`var1` = hoy, `var5` ≈ semana) |
| `vol_rel` | volumen de hoy / media de 20 sesiones |
| `macd`, `macd_senal`, `macd_hist` | MACD (12, 26, 9) |
| `bb_sup`, `bb_media`, `bb_inf` | Bandas de Bollinger (20, 2) |
| `max52`, `min52` | máximo / mínimo de 252 sesiones |

`python alertas_bolsa.py --ayuda-variables` imprime la lista. Si no hay suficientes datos (p. ej.
`sma200` de una empresa que cotiza desde hace 3 meses), la regla se considera no cumplida.

Para ajustar umbrales, mira los valores actuales de un ticker:

```bash
python alertas_bolsa.py --ver NVDA
```

## 4. Cómo recibir los avisos

Igual que en `renfe-precios` (los mismos secretos sirven):

**Telegram**: crea un bot con @BotFather, escríbele algo y abre
`https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat.id`.

**ntfy** (sin cuenta): suscríbete en la app a un tema con un nombre difícil de adivinar.

## 5a. Ejecutarlo en GitHub Actions

1. *Settings → Secrets and variables → Actions*: `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`, y/o `NTFY_TOPIC`.
2. `.github/workflows/alertas-bolsa.yml` se ejecuta de lunes a viernes a las 21:37 UTC, después del
   cierre de Nueva York en verano y en invierno. Los días festivos no hay vela nueva y no avisa.
   GitHub solo ejecuta los `schedule` de la **rama por defecto**, así que hay que fusionarlo en ella.
3. Pruébalo en *Actions → Alertas de bolsa → Run workflow*.

GitHub puede retrasar las ejecuciones programadas y desactiva los `schedule` de repos sin
actividad durante 60 días.

**Más frecuencia:** puedes cambiar el `cron` (p. ej. `"7 14-20 * * 1-5"` = cada hora en horario de
mercado). Durante la sesión la última vela está **sin cerrar**: el precio y los indicadores cambian
hasta el cierre, así que una señal intradía puede desaparecer al final del día. Cada regla avisa como
mucho una vez por día y ticker.

## 5b. O en tu ordenador

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...   # o NTFY_TOPIC=...
python alertas_bolsa.py --probar-aviso
python alertas_bolsa.py            # una vez
python alertas_bolsa.py --cada 60  # cada hora
```

## Pruebas

```bash
pip install pytest && python -m pytest -q
```

Las pruebas usan velas inventadas, sin red.
