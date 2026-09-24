# Buscador de vuelos baratos

Una vez al día busca en Google Flights los vuelos de ida y vuelta más baratos desde Madrid a una lista
de destinos y te manda el ranking por Telegram (o ntfy), usando los mismos secretos que el
vigilante de Renfe.

## Qué recibes

```
✈️ Vuelos baratos desde MAD · ida 19/20 → vuelta 26/27 06/2027 · 4 pers.
🕐 24/09 08:17 · 87 de 109 destinos con vuelo de ida · solo directos

✅ Por debajo de 150 €/persona ida y vuelta:
• Lisboa (LIS): 95 €/pers · 380 € total 📉 antes 120 €
   ida dom 20/06 08:00-09:10 TAP · vuelta dom 27/06 20:00-21:10 Iberia
...
Los siguientes más baratos (por encima del límite):
...
```

- ✅ DIRECTOS: ida y vuelta sin escalas. 🔁 CON 1 ESCALA: la combinación más barata con escala
  (de como mucho `escala_max_minutos`) en algún trayecto, solo si sale más barata que la directa.
  Se descartan los billetes separados y el "autotransbordo". Una sola consulta a Google trae ambos tipos.
- 🆕 destino que aparece por primera vez; 📉/📈 el precio ha bajado/subido desde el día anterior.
- Precio = ida más barata + vuelta más barata (dos billetes de solo ida), por persona; el total
  multiplica por el número de pasajeros. Un billete de ida y vuelta puede salir distinto.
- Se consulta 1 adulto: Google no garantiza que ese precio esté disponible para todos los pasajeros.
- Sin equipaje facturado. Los precios pueden no coincidir al céntimo con la web de la aerolínea.

## Configuración: `config.json`

| Campo | Qué es |
|---|---|
| `origen` | Aeropuerto de salida (código IATA) |
| `fechas_ida`, `fechas_vuelta` | Fechas posibles (AAAA-MM-DD); se elige la combinación más barata |
| `pasajeros` | Para calcular el total |
| `precio_max_persona` | Límite de "barato", ida y vuelta por persona |
| `incluir_escalas` | `true` = además de los directos, busca vuelos con 1 escala (apartado 🔁) |
| `escala_max_minutos` | Espera máxima en la escala, en minutos (180 = 3 h) |
| `mostrar_por_encima` | Cuántos destinos por encima del límite enseñar como referencia |
| `pausa_segundos` | Espera entre consultas a Google (no bajarla mucho) |
| `destinos` | Código IATA → nombre. Añade o quita los que quieras |

Google Flights solo admite un destino por consulta, así que se hacen dos fases: primero la ida de
todos los destinos y después la vuelta solo de los que ya caben en el presupuesto. Con ~90 destinos
son unas 200-250 consultas y tarda ~15-20 minutos. Si Google falla 6 veces seguidas, la búsqueda se
interrumpe y el mensaje lo indica con ⚠️.

La lista de destinos inicial es aproximada: si un destino no tiene vuelo directo, simplemente no sale.

## Ejecución

- Automática: `.github/workflows/vuelos-baratos.yml`, cada día a las 07:00 hora de Madrid, en verano
  y en invierno (el mensaje llega unos minutos después, lo que tarda la búsqueda).
- Manual: *Actions → Vuelos baratos → Run workflow*.
- Para pararlo: *Actions → Vuelos baratos → ⋯ → Disable workflow*.
- En local: `pip install -r requirements.txt && python vuelos_baratos.py`.

Pruebas: `pip install pytest && python -m pytest -q`.
