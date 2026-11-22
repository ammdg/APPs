# Mi cartera de fondos

Página web (`index.html`, sin servidor) que muestra el valor y la rentabilidad de tu cartera de fondos
con el **último valor liquidativo publicado** de cada fondo. Se publica con GitHub Pages en
https://ammdg.github.io/APPs/fondos/ (cuando está en la rama `main`).

## «Tiempo real» en fondos

Un fondo de inversión tiene **un único precio al día** (valor liquidativo), que la gestora publica
después del cierre, normalmente esa noche o al día siguiente. La web muestra siempre el más reciente que
haya y su fecha; se recarga sola cada 5 minutos y al volver a abrirla.

## Cómo funciona

- `fondos.json`: ISIN y nombre de cada fondo cuyo precio se descarga. **Es público** (repositorio y
  Pages): revela qué fondos tienes, pero no cuánto.
- `precios.py`: cada 2 horas (lunes a sábado) el workflow `.github/workflows/fondos.yml` descarga el
  último valor liquidativo de cada ISIN y lo añade a `precios.json` (solo precios, con su fecha).
  Fuentes: FT Markets (redondea a 2 decimales), Quefondos y Yahoo Finance (estas dos dan el valor
  liquidativo completo). Se usa el valor completo solo si cuadra con el de FT (diferencia ≤ 0,5 % el
  mismo día); si no, el de FT. De Yahoo solo se usan las series de fondos (símbolos `0P...`). Si una
  fuente falla, se usan las demás. **No son APIs oficiales**: si cambian su web, la descarga
  puede romperse; en ese caso la web avisa y puedes poner el precio a mano.
- Tus **participaciones e importes invertidos se guardan solo en tu navegador** (`localStorage`,
  clave `fondos.v1`). No se suben a ningún sitio. Exporta una copia de vez en cuando (botón «Datos»).

## Qué calcula

- Valor de la cartera = participaciones × último valor liquidativo de cada fondo.
- Resultado = valor − invertido (en € y %). «Invertido» es tu coste, como en la app de tu banco.
- Último día: variación entre los dos últimos precios de cada fondo.
- Gráfica: valor de la cartera con tus participaciones **actuales** aplicadas a los precios pasados (no
  conoce las fechas de tus aportaciones, así que no es tu rentabilidad real del periodo). El historial se
  va acumulando desde que empieza a funcionar el workflow.
- Compras y ventas: en la ficha de cada fondo. Una venta reduce lo invertido a coste medio.

## Añadir un fondo

1. Añade su ISIN y nombre a `fondos.json` (para que se descargue su precio).
2. En la web: «+ Añadir» con ISIN, participaciones e invertido.

Comprobar a mano qué devuelve cada fuente para un ISIN:

```bash
python precios.py --probar IE000ZYRH0Q7
```

Pruebas (sin red): `python -m pytest -q test_precios.py`
