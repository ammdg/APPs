# Buscador de vuelos baratos

Una vez al día busca en Google Flights el **billete de ida y vuelta** más barato desde Madrid a una lista
de destinos y te manda el ranking por Telegram (o ntfy), usando los mismos secretos que el
vigilante de Renfe.

## Qué recibes

```
✈️ Vuelos baratos desde MAD · ida 19/20 → vuelta 26/27 06/2027 · 4 pers.
🕐 27/09 07:12 · 80 de 91 destinos con billete de ida y vuelta

✅ DIRECTOS por debajo de 150 €/persona ida y vuelta:
• Berlín (BER): 137 €/pers · 548 € total 📉 antes 140 €
   ida sáb 19/06 20:10-23:10 Iberia
   vuelta sáb 26/06, horario a elegir en Google Flights
...
```

- ✅ DIRECTOS: billete de ida y vuelta sin escalas. 🔁 CON 1 ESCALA: el billete más barato con escala
  (de como mucho `escala_max_minutos`) en algún trayecto, solo si sale más barato que el directo.
  Se descartan los billetes separados y el "autotransbordo".
- 🆕 destino que aparece por primera vez; 📉/📈 el precio ha bajado/subido desde el día anterior.
- Precio = billete de ida y vuelta más barato (nunca dos de solo ida), por persona; el total
  multiplica por el número de pasajeros. Google da el precio del billete completo y el horario de la
  ida; el de la vuelta se elige al reservar (si la ida es directa y el billete lleva escala, la escala
  está en la vuelta y el mensaje dice "con escala").
- Se consulta 1 adulto: Google no garantiza que ese precio esté disponible para todos los pasajeros.
- Sin equipaje facturado. Los precios pueden no coincidir al céntimo con la web de la aerolínea.

## Mapa

Cada mañana, además del mensaje, se publica un mapa interactivo en https://ammdg.github.io/APPs/ con los
destinos y su precio por persona (billete de ida y vuelta). Al pinchar en una ciudad se ven los vuelos
(horario de la ida, fecha de la vuelta, total para todos) y un enlace a Google Flights con esa búsqueda.
Se puede cambiar el precio máximo y ver solo directos o solo con escala. Es una página pública.

Para que funcione hay que activarlo una vez: *Settings → Pages → Build and deployment → Source:
GitHub Actions*. Los datos salen de `mapa/datos.json`, que genera `vuelos_baratos.py` junto con el mensaje;
las coordenadas de los aeropuertos están en `coordenadas.json` (de OurAirports).

## Configuración: `config.json`

| Campo | Qué es |
|---|---|
| `origen` | Aeropuerto de salida (código IATA) |
| `fechas_ida`, `fechas_vuelta` | Fechas posibles (AAAA-MM-DD); se elige la combinación más barata |
| `pasajeros` | Para calcular el total |
| `precio_max_persona` | Límite de "barato", ida y vuelta por persona |
| `incluir_escalas` | `true` = además de los directos, busca vuelos con 1 escala (apartado 🔁) |
| `escala_max_minutos` | Espera máxima en la escala, en minutos (180 = 3 h) |
| `mostrar_por_encima` | Cuántos destinos por encima del límite enseñar como referencia en cada apartado (directos y con escala) |
| `pausa_segundos` | Espera entre consultas a Google (no bajarla mucho) |
| `url_mapa` | Enlace al mapa que se añade al final del mensaje (vacío = sin enlace) |
| `destinos` | Código IATA → nombre. Añade o quita los que quieras |

Google Flights solo admite un destino y unas fechas por consulta: cada destino son 4 combinaciones de
fechas × (directo + con escala) = 8 consultas; con ~90 destinos, unas 730. Para que no tarde casi una
hora, GitHub Actions reparte los destinos en 4 partes que se consultan a la vez y un último paso junta
los resultados y manda el mensaje. Si Google falla 6 veces seguidas en una parte, esa parte se
interrumpe y el mensaje lo indica con ⚠️.

La lista de destinos inicial es aproximada: si un destino no tiene vuelo directo, simplemente no sale.

## Ejecución

- Automática: `.github/workflows/vuelos-baratos.yml`, cada día a las 07:00 hora de Madrid, en verano
  y en invierno (el mensaje llega unos minutos después, lo que tarda la búsqueda).
- Manual: *Actions → Vuelos baratos → Run workflow*.
- Para pararlo: *Actions → Vuelos baratos → ⋯ → Disable workflow*.
- En local: `pip install -r requirements.txt && python vuelos_baratos.py` (todo en un proceso, ~45 min).

Pruebas: `pip install pytest && python -m pytest -q`.

## Exploración mundial (puntual)

`explorar.py` + `.github/workflows/explorar-mundo.yml` (*Actions → Explorar aeropuertos del mundo → Run
workflow*, solo a mano) consultan **todos** los aeropuertos de `aeropuertos_mundo.json`: 3.202 aeropuertos
medianos y grandes con vuelos regulares y código IATA, sin España, sacados de
[OurAirports](https://github.com/davidmegginson/ourairports-data) (dominio público), con su distancia a Madrid.

1. **Ida** en 20 partes a la vez: ida más barata (directa o 1 escala ≤ 3 h) de cada aeropuerto.
2. **Vuelta** en 10 partes, solo para los que tienen ida por debajo del precio máximo.
3. **Informe** por Telegram (en varios mensajes si hace falta): destinos por debajo de 150 €/persona,
   cuántos hay por tramos de distancia y el más lejano. Los que bajan de 250 € se guardan en
   `exploracion.json` (artefacto de la ejecución) para ampliar la lista de la búsqueda diaria.

Usa las fechas, escalas y precio de `config.json`. Si Google corta una parte, el informe lista los
aeropuertos que no se pudieron consultar.
