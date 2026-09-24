"""Busca los vuelos de ida y vuelta más baratos desde un aeropuerto a muchos destinos y los manda a Telegram.

Los precios salen de Google Flights con la librería fast-flights (https://github.com/AWeirdDev/flights).
Google solo admite un destino por consulta, así que se busca destino a destino en dos fases:

1. Ida: se consultan todas las fechas de ida de cada destino.
2. Vuelta: solo para los destinos cuya ida más barata ya está por debajo del precio máximo.

El precio de ida y vuelta es la suma de la ida y la vuelta más baratas (dos billetes de solo ida),
por persona, y el total se calcula multiplicando por el número de pasajeros.

Uso:
    python vuelos_baratos.py                 # una búsqueda (lo que hace GitHub Actions)
    python vuelos_baratos.py --probar-aviso  # envía una notificación de prueba
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

AQUI = Path(__file__).resolve().parent
ZONA = ZoneInfo("Europe/Madrid")
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
LIMITE_TELEGRAM = 4000  # Telegram corta en 4096 caracteres
MAX_ERRORES_SEGUIDOS = 6  # si Google falla tantas veces seguidas, probablemente nos ha bloqueado


@dataclass(frozen=True)
class Vuelo:
    fecha: date
    salida: str  # "HH:MM"
    llegada: str
    precio: float  # por persona
    aerolinea: str


@dataclass
class Config:
    origen: str
    fechas_ida: list[date]
    fechas_vuelta: list[date]
    destinos: dict[str, str]  # código IATA -> nombre
    pasajeros: int = 1
    precio_max_persona: float = 150
    solo_directos: bool = True
    mostrar_por_encima: int = 5  # cuántos destinos por encima del precio máximo enseñar como referencia
    pausa_segundos: float = 2

    @classmethod
    def desde_archivo(cls, path: Path) -> "Config":
        d = json.loads(path.read_text(encoding="utf-8"))
        fechas = lambda xs: [datetime.strptime(x, "%Y-%m-%d").date() for x in xs]  # noqa: E731
        return cls(
            origen=d["origen"].upper(),
            fechas_ida=fechas(d["fechas_ida"]),
            fechas_vuelta=fechas(d["fechas_vuelta"]),
            destinos={k.upper(): v for k, v in d["destinos"].items()},
            pasajeros=int(d.get("pasajeros", 1)),
            precio_max_persona=float(d.get("precio_max_persona", 150)),
            solo_directos=bool(d.get("solo_directos", True)),
            mostrar_por_encima=int(d.get("mostrar_por_encima", 5)),
            pausa_segundos=float(d.get("pausa_segundos", 2)),
        )


@dataclass
class Resultado:
    destino: str
    nombre: str
    ida: Vuelo | None = None
    vuelta: Vuelo | None = None

    @property
    def precio(self) -> float | None:
        """Precio de ida y vuelta por persona; None si falta alguno de los dos."""
        if self.ida is None or self.vuelta is None:
            return None
        return self.ida.precio + self.vuelta.precio


# --------------------------------------------------------------------------- Google Flights


def buscar_vuelo_mas_barato(origen: str, destino: str, fecha: date, solo_directos: bool) -> Vuelo | None:
    """El vuelo más barato de un día (1 adulto, turista, solo ida). None si no hay ninguno."""
    from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, get_flights

    consulta = create_query(
        flights=[
            FlightQuery(
                date=fecha.isoformat(),
                from_airport=origen,
                to_airport=destino,
                max_stops=0 if solo_directos else None,
            )
        ],
        trip="one-way",
        passengers=Passengers(adults=1),
        language="es",
        currency="EUR",
    )
    try:
        resultados = get_flights(consulta)
    except FlightsNotFound:
        return None

    mejor: Vuelo | None = None
    for r in resultados:
        tramos = r.flights
        if not tramos or not r.price or (solo_directos and len(tramos) > 1):
            continue
        if date(*tramos[0].departure.date) != fecha:
            continue
        vuelo = Vuelo(
            fecha=fecha,
            salida="%02d:%02d" % tramos[0].departure.time,
            llegada="%02d:%02d" % tramos[-1].arrival.time,
            precio=float(r.price),
            aerolinea=", ".join(r.airlines) or "?",
        )
        if mejor is None or vuelo.precio < mejor.precio:
            mejor = vuelo
    return mejor


# --------------------------------------------------------------------------- búsqueda


Buscador = Callable[[str, str, date, bool], "Vuelo | None"]


def buscar(cfg: Config, buscador: Buscador = buscar_vuelo_mas_barato) -> tuple[list[Resultado], list[str]]:
    """Devuelve los resultados por destino y una lista de avisos (errores) para el mensaje."""
    avisos: list[str] = []
    errores_seguidos = 0
    bloqueado = False

    def consultar(o: str, d: str, f: date) -> Vuelo | None:
        nonlocal errores_seguidos, bloqueado
        if bloqueado:
            return None
        try:
            v = buscador(o, d, f, cfg.solo_directos)
            errores_seguidos = 0
            return v
        except Exception as e:  # noqa: BLE001 - un destino que falla no debe parar el resto
            errores_seguidos += 1
            print(f"ERROR {o}→{d} {f}: {e!r}", file=sys.stderr)
            if errores_seguidos >= MAX_ERRORES_SEGUIDOS:
                bloqueado = True
                avisos.append(f"Google Flights falló {errores_seguidos} veces seguidas ({type(e).__name__}); "
                              "búsqueda interrumpida.")
            return None
        finally:
            time.sleep(cfg.pausa_segundos * random.uniform(0.7, 1.3))

    def mas_barato(vuelos: list[Vuelo | None]) -> Vuelo | None:
        return min((v for v in vuelos if v), key=lambda v: v.precio, default=None)

    resultados = [Resultado(codigo, nombre) for codigo, nombre in cfg.destinos.items()]

    # Fase 1: idas.
    for r in resultados:
        r.ida = mas_barato([consultar(cfg.origen, r.destino, f) for f in cfg.fechas_ida])
        print(f"ida {r.destino}: " + (f"{r.ida.precio:.0f} €" if r.ida else "sin vuelos"))

    # Fase 2: vueltas, solo si la ida ya cabe en el presupuesto (o para tener referencias por encima).
    candidatos = sorted((r for r in resultados if r.ida), key=lambda r: r.ida.precio)
    baratos = [r for r in candidatos if r.ida.precio < cfg.precio_max_persona]
    referencia = [r for r in candidatos if r not in baratos][: cfg.mostrar_por_encima]
    for r in baratos + referencia:
        r.vuelta = mas_barato([consultar(r.destino, cfg.origen, f) for f in cfg.fechas_vuelta])
        print(f"vuelta {r.destino}: " + (f"{r.vuelta.precio:.0f} €" if r.vuelta else "sin vuelos"))

    return resultados, avisos


# --------------------------------------------------------------------------- historial y mensaje


def cargar_historial(path: Path) -> dict[str, float]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _euros(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def _fecha(v: Vuelo) -> str:
    return f"{DIAS[v.fecha.weekday()]} {v.fecha:%d/%m} {v.salida}-{v.llegada}"


def formatear_mensaje(
    cfg: Config, resultados: list[Resultado], historial: dict[str, float], avisos: list[str], ahora: datetime
) -> str:
    completos = sorted((r for r in resultados if r.precio is not None), key=lambda r: r.precio)
    baratos = [r for r in completos if r.precio < cfg.precio_max_persona]
    caros = [r for r in completos if r not in baratos][: cfg.mostrar_por_encima]

    ida = "/".join(f"{f:%d}" for f in cfg.fechas_ida)
    vuelta = "/".join(f"{f:%d}" for f in cfg.fechas_vuelta)
    lineas = [
        f"✈️ Vuelos baratos desde {cfg.origen} · ida {ida} → vuelta {vuelta} "
        f"{cfg.fechas_vuelta[0]:%m/%Y} · {cfg.pasajeros} pers.",
        f"🕐 {ahora:%d/%m %H:%M} · {sum(1 for r in resultados if r.ida)} de {len(cfg.destinos)} destinos "
        "con vuelo de ida"
        + (" · solo directos" if cfg.solo_directos else ""),
        "",
    ]
    if baratos:
        lineas.append(f"✅ Por debajo de {_euros(cfg.precio_max_persona)}/persona ida y vuelta:")
    else:
        lineas.append(f"Ningún destino por debajo de {_euros(cfg.precio_max_persona)}/persona hoy.")

    def bloque(r: Resultado) -> list[str]:
        anterior = historial.get(r.destino)
        marca = ""
        if anterior is None:
            marca = " 🆕"
        elif r.precio < anterior - 0.5:
            marca = f" 📉 antes {_euros(anterior)}"
        elif r.precio > anterior + 0.5:
            marca = f" 📈 antes {_euros(anterior)}"
        return [
            f"• {r.nombre} ({r.destino}): {_euros(r.precio)}/pers · {_euros(r.precio * cfg.pasajeros)} total{marca}",
            f"   ida {_fecha(r.ida)} {r.ida.aerolinea} · vuelta {_fecha(r.vuelta)} {r.vuelta.aerolinea}",
        ]

    for r in baratos:
        lineas += bloque(r)
    if caros:
        lineas += ["", "Los siguientes más baratos (por encima del límite):"]
        for r in caros:
            lineas += bloque(r)
    for a in avisos:
        lineas += ["", f"⚠️ {a}"]
    lineas += [
        "",
        f"Precio = ida + vuelta más baratas, por persona (1 adulto) × {cfg.pasajeros}. "
        "Google Flights; sin equipaje facturado. Comprueba que quedan plazas para todos antes de comprar.",
    ]
    texto = "\n".join(lineas)
    if len(texto) > LIMITE_TELEGRAM:
        texto = texto[: LIMITE_TELEGRAM - 30].rsplit("\n", 1)[0] + "\n… (lista recortada)"
    return texto


def actualizar_historial(resultados: list[Resultado]) -> dict[str, float]:
    return {r.destino: r.precio for r in resultados if r.precio is not None}


# --------------------------------------------------------------------------- aviso


def notificar(texto: str) -> list[str]:
    """Envía el aviso por los canales configurados en variables de entorno. Devuelve los usados."""
    usados = []
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        usados.append("telegram")
    topic = os.getenv("NTFY_TOPIC")
    if topic:
        servidor = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        r = requests.post(
            f"{servidor}/{topic}",
            data=texto.encode("utf-8"),
            headers={"Title": "Vuelos baratos", "Tags": "airplane"},
            timeout=20,
        )
        r.raise_for_status()
        usados.append("ntfy")
    if not usados:
        print("(Sin canal de aviso configurado: define TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID o NTFY_TOPIC)")
    return usados


def ejecutar(cfg: Config, historial_path: Path, buscador: Buscador = buscar_vuelo_mas_barato) -> int:
    historial = cargar_historial(historial_path)
    resultados, avisos = buscar(cfg, buscador)
    texto = formatear_mensaje(cfg, resultados, historial, avisos, datetime.now(ZONA))
    print(texto)
    notificar(texto)
    nuevo = actualizar_historial(resultados)
    if nuevo:  # si todo falló, se conserva el historial anterior
        historial_path.write_text(json.dumps(nuevo, indent=2, ensure_ascii=False), encoding="utf-8")
    # Falla (y GitHub lo marca en rojo) solo si no se obtuvo ningún vuelo.
    return 0 if any(r.ida for r in resultados) else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=AQUI / "config.json")
    p.add_argument("--historial", type=Path, default=AQUI / "historial.json")
    p.add_argument("--probar-aviso", action="store_true")
    a = p.parse_args()
    if a.probar_aviso:
        return 0 if notificar("✅ Prueba del buscador de vuelos baratos") else 1
    return ejecutar(Config.desde_archivo(a.config), a.historial)


if __name__ == "__main__":
    sys.exit(main())
