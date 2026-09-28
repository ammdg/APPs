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

## 1. Estrategia WATCH / BUY

Cada acción tiene un estado que cambia con dos condiciones, `watch` y `buy`:

| Estado actual | `buy` | `watch` | Nuevo estado | Alerta |
|---|---|---|---|---|
| NEUTRAL | sí | – | BUY_ACTIVE | 🟢 BUY |
| NEUTRAL | no | sí | WATCH_ACTIVE | 👀 WATCH |
| WATCH_ACTIVE | sí | – | BUY_ACTIVE | 🟢 BUY |
| WATCH_ACTIVE | no | no | NEUTRAL | – |
| BUY_ACTIVE | – | no | NEUTRAL | – |
| (resto de casos) | | | sin cambio | – |

Tras un BUY no vuelve a avisar hasta que `watch` deje de cumplirse del todo.

```json
"estrategia": {
  "activa": true,
  "watch": "rsi14 < 35",
  "buy": "rsi14 < 35 and macd > macd_senal"
}
```

(Esas condiciones son solo un ejemplo.) El estado no se guarda de un día para otro: se reconstruye
en cada ejecución recorriendo todo el histórico descargado (2 años por defecto) desde NEUTRAL, y se
avisa si la última vela emite BUY o WATCH. Así no depende de que GitHub conserve ningún fichero.
Es equivalente a guardarlo siempre que en el histórico haya al menos un día sin `watch`.

`python alertas_bolsa.py --ver AAPL` muestra el estado actual y las últimas señales que habría dado
la estrategia en el histórico, útil para comprobar si se comporta como esperas.

## 2. Acciones y reglas sueltas: `config.json`

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

## 3. Cómo recibir los avisos

Igual que en `renfe-precios` (los mismos secretos sirven):

**Telegram**: crea un bot con @BotFather, escríbele algo y abre
`https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat.id`.

**ntfy** (sin cuenta): suscríbete en la app a un tema con un nombre difícil de adivinar.

## 4a. Ejecutarlo en GitHub Actions

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

## 4b. O en tu ordenador

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
