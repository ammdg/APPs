"""Diagnóstico: qué devuelve Google Flights para unas rutas y qué lee de ello fast-flights.

Uso: python diagnostico.py MAD-HAJ:2027-06-19 HAJ-MAD:2027-06-27 ...
Solo lee la página; no manda avisos.
"""

from __future__ import annotations

import json
import sys
from datetime import date

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from fast_flights.parser import parse
from selectolax.lexbor import LexborHTMLParser


def resumen_itinerario(k) -> str:
    try:
        vuelo = k[0]
        tramos = vuelo[2]
        ruta = " > ".join([tramos[0][3]] + [t[6] for t in tramos])
        horas = f"{tramos[0][8]}->{tramos[-1][10]}"
        try:
            precio = k[1][0][1]
        except (TypeError, IndexError):
            precio = "SIN PRECIO"
        return f"{ruta} {horas} {vuelo[1]} precio={precio}"
    except Exception as e:  # noqa: BLE001
        return f"(no se pudo leer: {type(e).__name__}: {e})"


def diagnosticar(origen: str, destino: str, fecha: date, max_stops: int, layover: int | None) -> None:
    print(f"\n===== {origen}-{destino} {fecha} max_stops={max_stops} layover={layover}")
    consulta = create_query(
        flights=[FlightQuery(date=fecha.isoformat(), from_airport=origen, to_airport=destino,
                             max_stops=max_stops, max_layover_minutes=layover)],
        trip="one-way", passengers=Passengers(adults=1), language="es", currency="EUR",
        hide_separate_and_self_transfer=True,
    )
    html = fetch_flights_html(consulta)
    print("tamaño html:", len(html), "| tiene ds:1:", "ds:1" in html)
    try:
        r = parse(html)
        print("fast-flights parse ->", len(r), "itinerarios")
    except Exception as e:  # noqa: BLE001
        print("fast-flights parse -> EXCEPCIÓN", type(e).__name__, e)

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        print("sin bloque ds:1")
        return
    datos = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if datos.endswith("errorHasStatus: true"):
        print("Google responde errorHasStatus")
        return
    payload = json.loads(datos)
    print("longitud payload:", len(payload))
    for i in (2, 3):
        try:
            lista = payload[i][0] if payload[i] else None
        except (TypeError, IndexError):
            lista = None
        print(f"payload[{i}][0]:", "None" if lista is None else f"{len(lista)} itinerarios")
        for k in (lista or [])[:6]:
            print("   ", resumen_itinerario(k))


def main() -> int:
    for arg in sys.argv[1:]:
        ruta, f = arg.split(":")
        o, d = ruta.split("-")
        fecha = date.fromisoformat(f)
        for max_stops, layover in ((1, 180), (1, None)):
            try:
                diagnosticar(o, d, fecha, max_stops, layover)
            except Exception as e:  # noqa: BLE001
                print("ERROR", type(e).__name__, e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
