# Vigilante de precios Renfe

Comprueba cada hora los precios de un trayecto en venta.renfe.com y te avisa (Telegram o
[ntfy](https://ntfy.sh)) cuando algún tren tiene plazas y su precio está en tu rango.

- Solo avisa una vez por tren. Vuelve a avisar si el precio baja o si el tren sale del rango y
  vuelve a entrar.
- Consulta: 1 adulto, solo ida, tarifa mínima que muestra Renfe para cada tren.
- La forma de consultar a Renfe está adaptada de
  [renfe-bot](https://github.com/emartinez-dev/renfe-bot) (MIT). Usa el backend interno de la web
  de venta, **no una API pública**, así que puede romperse si Renfe cambia su web.

## 1. Configura el trayecto: `config.json`

```json
{
  "origen": "MADRID (TODAS)",
  "destino": "BARCELONA (TODAS)",
  "fechas": ["2026-10-16", "2026-10-17"],
  "precio_min": 0,
  "precio_max": 40,
  "hora_desde": "06:00",
  "hora_hasta": "22:00",
  "tipos_tren": null
}
```

Los nombres de estación tienen que ser exactamente los de Renfe. Para buscarlos:

```bash
python renfe_precios.py --buscar-estacion sevilla
```

`tipos_tren` puede ser `null` (todos) o una lista, p. ej. `["AVE", "AVLO"]`. El texto es el que
devuelve Renfe; revisa la salida de una ejecución para ver los valores reales.

## 2. Elige cómo recibir los avisos

**Telegram**: habla con @BotFather, crea un bot y copia el token. Escribe algo a tu bot y abre
`https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat.id`.

**ntfy** (sin cuenta): instala la app ntfy, suscríbete a un tema con un nombre difícil de adivinar
(p. ej. `renfe-a8f3k2x9`). Es público para quien conozca el nombre.

## 3a. Ejecutarlo cada hora en GitHub Actions (sin ordenador encendido)

1. En el repo: *Settings → Secrets and variables → Actions* → añade `TELEGRAM_BOT_TOKEN` y
   `TELEGRAM_CHAT_ID`, y/o `NTFY_TOPIC`.
2. El workflow `.github/workflows/renfe-precios.yml` se ejecuta cada hora. GitHub solo ejecuta
   los `schedule` de la **rama por defecto**, así que hay que fusionar esto en ella.
3. Pruébalo a mano en *Actions → Precios Renfe → Run workflow*.

Ten en cuenta que GitHub puede retrasar las ejecuciones programadas y desactiva los `schedule` de
repos sin actividad durante 60 días.

## 3b. O ejecutarlo en tu ordenador

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...   # o NTFY_TOPIC=...
python renfe_precios.py --probar-aviso
python renfe_precios.py --cada 60
```

## Pruebas

```bash
pip install pytest && python -m pytest -q
```
