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
    escala: str | None = None  # aeropuerto de la escala; None = directo
    espera_min: int | None = None  # minutos de espera en la escala


@dataclass(frozen=True)
class Opciones:
    """Lo más barato de un día: directo y con una escala (cualquiera de los dos puede faltar)."""

    directo: Vuelo | None = None
    escala: Vuelo | None = None


@dataclass
class Config:
    origen: str
    fechas_ida: list[date]
    fechas_vuelta: list[date]
    destinos: dict[str, str]  # código IATA -> nombre
    pasajeros: int = 1
    precio_max_persona: float = 150
    incluir_escalas: bool = False  # además de los directos, vuelos con 1 escala
    escala_max_minutos: int = 180  # espera máxima en la escala
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
            incluir_escalas=bool(d.get("incluir_escalas", False)),
            escala_max_minutos=int(d.get("escala_max_minutos", 180)),
            mostrar_por_encima=int(d.get("mostrar_por_encima", 5)),
            pausa_segundos=float(d.get("pausa_segundos", 2)),
        )


@dataclass(frozen=True)
class Combinacion:
    ida: Vuelo
    vuelta: Vuelo

    @property
    def precio(self) -> float:
        """Ida y vuelta, por persona."""
        return self.ida.precio + self.vuelta.precio

    @property
    def con_escala(self) -> bool:
        return self.ida.escala is not None or self.vuelta.escala is not None


def _mas_barato(*vuelos: Vuelo | None) -> Vuelo | None:
    # min() se queda con el primero en caso de empate: se pasan antes los directos.
    return min((v for v in vuelos if v), key=lambda v: v.precio, default=None)


@dataclass
class Resultado:
    destino: str
    nombre: str
    ida_directo: Vuelo | None = None
    ida_escala: Vuelo | None = None
    vuelta_directo: Vuelo | None = None
    vuelta_escala: Vuelo | None = None

    @property
    def ida(self) -> Vuelo | None:
        """La ida más barata, directa o con escala."""
        return _mas_barato(self.ida_directo, self.ida_escala)

    @property
    def directo(self) -> Combinacion | None:
        if self.ida_directo and self.vuelta_directo:
            return Combinacion(self.ida_directo, self.vuelta_directo)
        return None

    @property
    def con_escala(self) -> Combinacion | None:
        """La combinación más barata que lleva escala en algún trayecto, solo si sale más barata que la
        directa (o no hay directa): si no, no aporta nada."""
        ida = _mas_barato(self.ida_directo, self.ida_escala)
        vuelta = _mas_barato(self.vuelta_directo, self.vuelta_escala)
        if not ida or not vuelta:
            return None
        combinacion = Combinacion(ida, vuelta)
        return combinacion if combinacion.con_escala else None

    @property
    def mejor(self) -> Combinacion | None:
        return min((c for c in (self.directo, self.con_escala) if c), key=lambda c: c.precio, default=None)


# --------------------------------------------------------------------------- Google Flights


def _minutos_entre(llegada, salida) -> int:
    a = datetime(*llegada.date, *llegada.time)
    b = datetime(*salida.date, *salida.time)
    return int((b - a).total_seconds() // 60)


def buscar_opciones(origen: str, destino: str, fecha: date, cfg: Config) -> Opciones:
    """Lo más barato de un día (1 adulto, turista, solo ida): directo y, si se piden, con 1 escala corta.

    Una sola consulta a Google devuelve ambos tipos ("1 escala como máximo" incluye los directos).
    """
    from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, fetch_flights_html
    from fast_flights.parser import parse

    consulta = create_query(
        flights=[
            FlightQuery(
                date=fecha.isoformat(),
                from_airport=origen,
                to_airport=destino,
                max_stops=1 if cfg.incluir_escalas else 0,
                max_layover_minutes=cfg.escala_max_minutos if cfg.incluir_escalas else None,
            )
        ],
        trip="one-way",
        passengers=Passengers(adults=1),
        language="es",
        currency="EUR",
        # Sin billetes separados ni "autotransbordo": si se pierde la conexión, nadie responde.
        hide_separate_and_self_transfer=True,
    )
    # Los errores de red se propagan (cuentan como fallo de Google). En cambio, cuando un día no hay
    # vuelos que cumplan el filtro, Google devuelve la página sin lista y fast-flights 3.1.0 falla al
    # leerla con TypeError/IndexError: eso es "sin vuelos", no un error.
    html = fetch_flights_html(consulta)
    if "ds:1" not in html:  # el bloque de datos que lee fast-flights
        raise RuntimeError("Google Flights no devolvió resultados (¿bloqueo o cambio en la web?)")
    try:
        resultados = parse(html)
    except (FlightsNotFound, TypeError, IndexError):
        return Opciones()

    directo: Vuelo | None = None
    escala: Vuelo | None = None
    for r in resultados:
        tramos = r.flights
        if not tramos or not r.price or len(tramos) > 2:
            continue
        if date(*tramos[0].departure.date) != fecha:
            continue
        datos = dict(
            fecha=fecha,
            salida="%02d:%02d" % tramos[0].departure.time,
            llegada="%02d:%02d" % tramos[-1].arrival.time,
            precio=float(r.price),
            aerolinea=", ".join(r.airlines) or "?",
        )
        if len(tramos) == 1:
            directo = _mas_barato(directo, Vuelo(**datos))
        elif cfg.incluir_escalas:
            espera = _minutos_entre(tramos[0].arrival, tramos[1].departure)
            if 0 <= espera <= cfg.escala_max_minutos:
                vuelo = Vuelo(**datos, escala=tramos[0].to_airport.code, espera_min=espera)
                escala = _mas_barato(escala, vuelo)
    return Opciones(directo, escala)


# --------------------------------------------------------------------------- búsqueda


Buscador = Callable[[str, str, date, Config], Opciones]


def buscar(cfg: Config, buscador: Buscador = buscar_opciones) -> tuple[list[Resultado], list[str]]:
    """Devuelve los resultados por destino y una lista de avisos (errores) para el mensaje."""
    avisos: list[str] = []
    errores_seguidos = 0
    bloqueado = False

    def consultar(o: str, d: str, fechas: list[date]) -> tuple[Vuelo | None, Vuelo | None]:
        """Directo y con escala más baratos entre todas las fechas."""
        nonlocal errores_seguidos, bloqueado
        directos, escalas = [], []
        for f in fechas:
            if bloqueado:
                break
            try:
                op = buscador(o, d, f, cfg)
                directos.append(op.directo)
                escalas.append(op.escala)
                errores_seguidos = 0
            except Exception as e:  # noqa: BLE001 - un destino que falla no debe parar el resto
                errores_seguidos += 1
                print(f"ERROR {o}→{d} {f}: {e!r}", file=sys.stderr)
                if errores_seguidos >= MAX_ERRORES_SEGUIDOS:
                    bloqueado = True
                    avisos.append(f"Google Flights falló {errores_seguidos} veces seguidas "
                                  f"({type(e).__name__}); búsqueda interrumpida.")
            finally:
                time.sleep(cfg.pausa_segundos * random.uniform(0.7, 1.3))
        return _mas_barato(*directos), _mas_barato(*escalas)

    def texto(v: Vuelo | None) -> str:
        return f"{v.precio:.0f} €" if v else "—"

    resultados = [Resultado(codigo, nombre) for codigo, nombre in cfg.destinos.items()]

    # Fase 1: idas.
    for r in resultados:
        r.ida_directo, r.ida_escala = consultar(cfg.origen, r.destino, cfg.fechas_ida)
        print(f"ida {r.destino}: directo {texto(r.ida_directo)} · escala {texto(r.ida_escala)}")

    # Fase 2: vueltas, solo si la ida ya cabe en el presupuesto (o para tener referencias por encima).
    candidatos = sorted((r for r in resultados if r.ida), key=lambda r: r.ida.precio)
    baratos = [r for r in candidatos if r.ida.precio < cfg.precio_max_persona]
    referencia = [r for r in candidatos if r not in baratos][: cfg.mostrar_por_encima]
    for r in baratos + referencia:
        r.vuelta_directo, r.vuelta_escala = consultar(r.destino, cfg.origen, cfg.fechas_vuelta)
        print(f"vuelta {r.destino}: directo {texto(r.vuelta_directo)} · escala {texto(r.vuelta_escala)}")

    return resultados, avisos


# --------------------------------------------------------------------------- historial y mensaje


def cargar_historial(path: Path) -> dict[str, float]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _clave(r: Resultado, c: Combinacion) -> str:
    return f"{r.destino}+escala" if c.con_escala else r.destino


def _euros(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def _tramo(v: Vuelo) -> str:
    texto = f"{DIAS[v.fecha.weekday()]} {v.fecha:%d/%m} {v.salida}-{v.llegada} {v.aerolinea}"
    if v.escala:
        texto += f" (escala {v.escala} {v.espera_min // 60}h{v.espera_min % 60:02d})"
    return texto


def formatear_mensaje(
    cfg: Config, resultados: list[Resultado], historial: dict[str, float], avisos: list[str], ahora: datetime
) -> str:
    limite = cfg.precio_max_persona
    directos = sorted(((r, r.directo) for r in resultados if r.directo), key=lambda x: x[1].precio)
    escalas = sorted(((r, r.con_escala) for r in resultados if r.con_escala), key=lambda x: x[1].precio)
    directos_baratos = [x for x in directos if x[1].precio < limite]
    escalas_baratas = [x for x in escalas if x[1].precio < limite]
    # Referencia en cada apartado: los siguientes más baratos por encima del límite, sin repetir
    # destinos que ya tienen alguna opción por debajo.
    con_barato = {r.destino for r, _ in directos_baratos + escalas_baratas}
    directos_caros = [x for x in directos if x[1].precio >= limite and x[0].destino not in con_barato]
    escalas_caras = [x for x in escalas if x[1].precio >= limite and x[0].destino not in con_barato]
    directos_caros = directos_caros[: cfg.mostrar_por_encima]
    escalas_caras = escalas_caras[: cfg.mostrar_por_encima]

    ida = "/".join(f"{f:%d}" for f in cfg.fechas_ida)
    vuelta = "/".join(f"{f:%d}" for f in cfg.fechas_vuelta)
    lineas = [
        f"✈️ Vuelos baratos desde {cfg.origen} · ida {ida} → vuelta {vuelta} "
        f"{cfg.fechas_vuelta[0]:%m/%Y} · {cfg.pasajeros} pers.",
        f"🕐 {ahora:%d/%m %H:%M} · {sum(1 for r in resultados if r.ida)} de {len(cfg.destinos)} destinos "
        "con vuelo de ida",
    ]

    def bloque(r: Resultado, c: Combinacion) -> list[str]:
        anterior = historial.get(_clave(r, c))
        marca = ""
        if anterior is None:
            marca = " 🆕"
        elif c.precio < anterior - 0.5:
            marca = f" 📉 antes {_euros(anterior)}"
        elif c.precio > anterior + 0.5:
            marca = f" 📈 antes {_euros(anterior)}"
        return [
            f"• {r.nombre} ({r.destino}): {_euros(c.precio)}/pers · {_euros(c.precio * cfg.pasajeros)} total{marca}",
            f"   ida {_tramo(c.ida)}",
            f"   vuelta {_tramo(c.vuelta)}",
        ]

    lineas += ["", f"✅ DIRECTOS por debajo de {_euros(limite)}/persona ida y vuelta:"]
    for r, c in directos_baratos:
        lineas += bloque(r, c)
    if not directos_baratos:
        lineas.append("Ninguno hoy.")
    if directos_caros:
        lineas += ["", "Siguientes directos más baratos (por encima del límite):"]
        for r, c in directos_caros:
            lineas += bloque(r, c)

    if cfg.incluir_escalas:
        horas = f"{cfg.escala_max_minutos // 60}h" + (
            f"{cfg.escala_max_minutos % 60:02d}" if cfg.escala_max_minutos % 60 else "")
        lineas += ["", f"🔁 CON 1 ESCALA (máx. {horas}) por debajo de {_euros(limite)}/persona, "
                       "más baratos que el directo:"]
        for r, c in escalas_baratas:
            lineas += bloque(r, c)
        if not escalas_baratas:
            lineas.append("Ninguno hoy.")
        if escalas_caras:
            lineas += ["", "Siguientes con escala más baratos (por encima del límite):"]
            for r, c in escalas_caras:
                lineas += bloque(r, c)
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
    historial = {}
    for r in resultados:
        for c in (r.directo, r.con_escala):
            if c:
                historial[_clave(r, c)] = c.precio
    return historial


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


def ejecutar(cfg: Config, historial_path: Path, buscador: Buscador = buscar_opciones) -> int:
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
