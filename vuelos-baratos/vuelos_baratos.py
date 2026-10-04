"""Busca el billete de ida y vuelta más barato desde un aeropuerto a muchos destinos y lo manda a Telegram.

Los precios salen de Google Flights con la librería fast-flights (https://github.com/AWeirdDev/flights).
Siempre se valora el billete de ida y vuelta (no dos de solo ida): para cada destino y cada combinación de
fechas de ida y vuelta se pide a Google el billete más barato solo con directos y, si se piden escalas,
con 1 escala corta como máximo. El precio es por persona (1 adulto) y el total se multiplica por el número
de pasajeros. Google solo da el horario de la ida; el de la vuelta se elige al reservar.

Uso:
    python vuelos_baratos.py                          # búsqueda completa en un solo proceso
    python vuelos_baratos.py --parte 0 --de 4         # una parte de los destinos -> parte_0.json
    python vuelos_baratos.py --informe parte_*.json   # junta las partes y manda el mensaje
    python vuelos_baratos.py --probar-aviso           # envía una notificación de prueba
"""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import os
import random
import re
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
    url_mapa: str = ""  # enlace al mapa (GitHub Pages) que se añade al mensaje

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
            url_mapa=d.get("url_mapa", ""),
        )


@dataclass(frozen=True)
class Combinacion:
    ida: Vuelo
    vuelta: Vuelo
    # True = un solo billete de ida y vuelta: ida.precio ya es el total y de la vuelta solo se sabe la fecha
    # (y, si Google la ofrece solo con escala, escala="?").
    billete_unico: bool = False

    @property
    def precio(self) -> float:
        """Ida y vuelta, por persona."""
        return self.ida.precio if self.billete_unico else self.ida.precio + self.vuelta.precio

    @property
    def con_escala(self) -> bool:
        return self.ida.escala is not None or self.vuelta.escala is not None


def _mas_barato(*vuelos: Vuelo | None) -> Vuelo | None:
    # min() se queda con el primero en caso de empate: se pasan antes los directos.
    return min((v for v in vuelos if v), key=lambda v: v.precio, default=None)


def _mas_barata(*combinaciones: Combinacion | None) -> Combinacion | None:
    return min((c for c in combinaciones if c), key=lambda c: c.precio, default=None)


@dataclass
class Resultado:
    destino: str
    nombre: str
    ida_directo: Vuelo | None = None
    ida_escala: Vuelo | None = None
    vuelta_directo: Vuelo | None = None
    vuelta_escala: Vuelo | None = None
    billete_directo: Combinacion | None = None  # billete de ida y vuelta, ambos trayectos directos
    billete_escala: Combinacion | None = None  # billete de ida y vuelta con 1 escala como máximo

    @property
    def ida(self) -> Vuelo | None:
        """La ida más barata, directa o con escala."""
        return _mas_barato(self.ida_directo, self.ida_escala)

    @property
    def directo(self) -> Combinacion | None:
        """Lo más barato con los dos trayectos directos: dos solo idas o un billete de ida y vuelta."""
        sueltos = None
        if self.ida_directo and self.vuelta_directo:
            sueltos = Combinacion(self.ida_directo, self.vuelta_directo)
        return _mas_barata(sueltos, self.billete_directo)

    @property
    def con_escala(self) -> Combinacion | None:
        """La combinación más barata que lleva escala en algún trayecto, solo si sale más barata que la
        directa (o no hay directa): si no, no aporta nada."""
        ida = _mas_barato(self.ida_directo, self.ida_escala)
        vuelta = _mas_barato(self.vuelta_directo, self.vuelta_escala)
        sueltos = Combinacion(ida, vuelta) if ida and vuelta else None
        opciones = [c for c in (sueltos, self.billete_escala) if c and c.con_escala]
        mejor = _mas_barata(*opciones)
        directo = self.directo
        if mejor and directo and directo.precio <= mejor.precio:
            return None
        return mejor

    @property
    def mejor(self) -> Combinacion | None:
        return min((c for c in (self.directo, self.con_escala) if c), key=lambda c: c.precio, default=None)


# --------------------------------------------------------------------------- Google Flights


def _minutos_entre(llegada, salida) -> int:
    a = datetime(*llegada.date, *llegada.time)
    b = datetime(*salida.date, *salida.time)
    return int((b - a).total_seconds() // 60)


def _con_precio(itinerario: Any) -> bool:
    try:
        return itinerario[1][0][1] is not None
    except (TypeError, IndexError):
        return False


def juntar_mejores_opciones(html: str) -> str:
    """Hace que fast-flights lea también el bloque "Mejores opciones" de Google Flights.

    Google reparte los vuelos en dos listas: "mejores opciones" (payload[2]) y el resto (payload[3]).
    fast-flights 3.1.0 solo lee la segunda, y en la primera suelen estar los más baratos (p. ej. MAD-OPO a
    32 € salía a 44 €). Aquí se juntan ambas en payload[3] y se quitan los itinerarios sin precio, que
    harían fallar la lectura de toda la página. Si el HTML no tiene la forma esperada, se devuelve igual.
    """
    m = re.search(r'(<script[^>]*class="ds:1"[^>]*>)(.*?)(</script>)', html, re.S)
    if not m:
        return html
    try:
        cabeza, resto = m.group(2).split("data:", 1)
        datos, cola = resto.rsplit(",", 1)
        payload = json.loads(datos)
        mejores = (payload[2] or [None])[0] or []
        otros = (payload[3] or [None])[0] or []
    except (ValueError, TypeError, IndexError, KeyError):
        return html
    todos = [k for k in [*mejores, *otros] if _con_precio(k)]
    if not todos:
        return html
    if payload[3]:
        payload[3][0] = todos
    else:
        payload[3] = [todos]
    js = cabeza + "data:" + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") + "," + cola
    return html[: m.start(2)] + js + html[m.end(2) :]


def _itinerarios(tramos: list[tuple[str, str, date]], escalas: bool, cfg: Config) -> list[Any]:
    """Consulta Google Flights (1 adulto, turista) y devuelve los itinerarios que lee fast-flights.

    Un tramo = solo ida; dos tramos = billete de ida y vuelta (los precios son del billete completo y los
    itinerarios, los de la ida). escalas=False pide solo directos; True, 1 escala corta como máximo.
    """
    from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, fetch_flights_html
    from fast_flights.parser import parse

    consulta = create_query(
        flights=[
            FlightQuery(
                date=fecha.isoformat(),
                from_airport=o,
                to_airport=d,
                max_stops=1 if escalas else 0,
                max_layover_minutes=cfg.escala_max_minutos if escalas else None,
            )
            for o, d, fecha in tramos
        ],
        trip="round-trip" if len(tramos) == 2 else "one-way",
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
        return list(parse(juntar_mejores_opciones(html)))
    except (FlightsNotFound, TypeError, IndexError):
        return []


def _vuelo(r: Any, fecha: date, cfg: Config) -> Vuelo | None:
    """El itinerario como Vuelo si sale ese día y es directo o con 1 escala dentro del límite."""
    tramos = r.flights
    if not tramos or not r.price or len(tramos) > 2:
        return None
    if date(*tramos[0].departure.date) != fecha:
        return None
    datos = dict(
        fecha=fecha,
        salida="%02d:%02d" % tramos[0].departure.time,
        llegada="%02d:%02d" % tramos[-1].arrival.time,
        precio=float(r.price),
        aerolinea=", ".join(r.airlines) or "?",
    )
    if len(tramos) == 1:
        return Vuelo(**datos)
    espera = _minutos_entre(tramos[0].arrival, tramos[1].departure)
    if cfg.incluir_escalas and 0 <= espera <= cfg.escala_max_minutos:
        return Vuelo(**datos, escala=tramos[0].to_airport.code, espera_min=espera)
    return None


def buscar_opciones(origen: str, destino: str, fecha: date, cfg: Config) -> Opciones:
    """Lo más barato de un día (1 adulto, turista, solo ida): directo y, si se piden, con 1 escala corta.

    Una sola consulta a Google devuelve ambos tipos ("1 escala como máximo" incluye los directos).
    """
    directo: Vuelo | None = None
    escala: Vuelo | None = None
    for r in _itinerarios([(origen, destino, fecha)], cfg.incluir_escalas, cfg):
        v = _vuelo(r, fecha, cfg)
        if v and v.escala:
            escala = _mas_barato(escala, v)
        elif v:
            directo = _mas_barato(directo, v)
    return Opciones(directo, escala)


def buscar_billete(origen: str, destino: str, ida: date, vuelta: date, escalas: bool,
                   cfg: Config) -> Combinacion | None:
    """El billete de ida y vuelta más barato para esas fechas (1 adulto), solo directos o con escala.

    Google solo devuelve el horario de la ida; de la vuelta se sabe la fecha. Con escalas=True solo se
    devuelve si lleva escala en algún trayecto (si no, ya lo cubre la consulta de directos).
    """
    mejor: Combinacion | None = None
    for r in _itinerarios([(origen, destino, ida), (destino, origen, vuelta)], escalas, cfg):
        v = _vuelo(r, ida, cfg)
        if not v:
            continue
        # Si la ida es directa en la consulta con escalas, la escala está en la vuelta.
        escala_vuelta = "?" if escalas and not v.escala else None
        c = Combinacion(v, Vuelo(vuelta, "", "", 0.0, v.aerolinea, escala=escala_vuelta), billete_unico=True)
        mejor = _mas_barata(mejor, c)
    return mejor


# --------------------------------------------------------------------------- búsqueda


Buscador = Callable[[str, str, date, Config], Opciones]
BuscadorBillete = Callable[[str, str, date, date, bool, Config], "Combinacion | None"]


def partes_de(destinos: dict[str, str], parte: int, de: int) -> dict[str, str]:
    """Reparte los destinos en `de` partes (para consultarlas a la vez en varios jobs)."""
    codigos = list(destinos)[parte::de]
    return {c: destinos[c] for c in codigos}


def buscar(
    cfg: Config, buscador_billete: BuscadorBillete = buscar_billete
) -> tuple[list[Resultado], list[str]]:
    """Billete de ida y vuelta más barato de cada destino (directo y con escala) entre todas las
    combinaciones de fechas. Devuelve los resultados y una lista de avisos (errores) para el mensaje."""
    avisos: list[str] = []
    errores_seguidos = 0
    bloqueado = False

    def intentar(descripcion: str, consulta: Callable[[], Any]) -> Any:
        """Hace una consulta a Google; si falla, lo apunta y devuelve None (un destino que falla no debe
        parar el resto). Tras MAX_ERRORES_SEGUIDOS fallos seguidos se deja de consultar."""
        nonlocal errores_seguidos, bloqueado
        if bloqueado:
            return None
        try:
            resultado = consulta()
            errores_seguidos = 0
            return resultado
        except Exception as e:  # noqa: BLE001
            errores_seguidos += 1
            print(f"ERROR {descripcion}: {e!r}", file=sys.stderr)
            if errores_seguidos >= MAX_ERRORES_SEGUIDOS:
                bloqueado = True
                avisos.append(f"Google Flights falló {errores_seguidos} veces seguidas "
                              f"({type(e).__name__}); búsqueda interrumpida.")
            return None
        finally:
            time.sleep(cfg.pausa_segundos * random.uniform(0.7, 1.3))

    def billete(d: str, escalas: bool) -> Combinacion | None:
        opciones = [
            intentar(f"{cfg.origen}⇄{d} {i}/{v}", lambda: buscador_billete(cfg.origen, d, i, v, escalas, cfg))
            for i in cfg.fechas_ida
            for v in cfg.fechas_vuelta
        ]
        return _mas_barata(*opciones)

    def texto(c: Combinacion | None) -> str:
        return f"{c.precio:.0f} €" if c else "—"

    resultados = []
    for codigo, nombre in cfg.destinos.items():
        r = Resultado(codigo, nombre)
        r.billete_directo = billete(codigo, escalas=False)
        if cfg.incluir_escalas:
            r.billete_escala = billete(codigo, escalas=True)
        print(f"{codigo}: directo {texto(r.billete_directo)} · escala {texto(r.billete_escala)}")
        resultados.append(r)
    return resultados, avisos


# --------------------------------------------------------------------------- partes (varios jobs a la vez)


def _vuelo_a_dict(v: Vuelo) -> dict:
    return {**dataclasses.asdict(v), "fecha": v.fecha.isoformat()}


def _vuelo_de_dict(d: dict) -> Vuelo:
    return Vuelo(**{**d, "fecha": date.fromisoformat(d["fecha"])})


def _combinacion_a_dict(c: Combinacion | None) -> dict | None:
    if not c:
        return None
    return {"ida": _vuelo_a_dict(c.ida), "vuelta": _vuelo_a_dict(c.vuelta), "billete_unico": c.billete_unico}


def _combinacion_de_dict(d: dict | None) -> Combinacion | None:
    if not d:
        return None
    return Combinacion(_vuelo_de_dict(d["ida"]), _vuelo_de_dict(d["vuelta"]), d["billete_unico"])


def guardar_parte(path: Path, resultados: list[Resultado], avisos: list[str]) -> None:
    datos = {
        "resultados": [
            {"destino": r.destino, "nombre": r.nombre, "directo": _combinacion_a_dict(r.billete_directo),
             "escala": _combinacion_a_dict(r.billete_escala)}
            for r in resultados
        ],
        "avisos": avisos,
    }
    path.write_text(json.dumps(datos, indent=1, ensure_ascii=False), encoding="utf-8")


def leer_partes(cfg: Config, paths: list[Path]) -> tuple[list[Resultado], list[str]]:
    """Junta las partes en el orden de config.json. Si falta alguna, lo avisa."""
    por_destino: dict[str, Resultado] = {}
    avisos: list[str] = []
    for path in paths:
        datos = json.loads(path.read_text(encoding="utf-8"))
        avisos += datos["avisos"]
        for x in datos["resultados"]:
            por_destino[x["destino"]] = Resultado(
                x["destino"], x["nombre"], billete_directo=_combinacion_de_dict(x["directo"]),
                billete_escala=_combinacion_de_dict(x["escala"]))
    faltan = [c for c in cfg.destinos if c not in por_destino]
    if faltan:
        avisos.append(f"Sin datos de {len(faltan)} destinos (falló alguna parte): {', '.join(faltan)}.")
    return [por_destino[c] for c in cfg.destinos if c in por_destino], avisos


# --------------------------------------------------------------------------- historial y mensaje


def cargar_historial(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


MOSTRADOS = "_mostrados"  # en el historial: destinos que salieron en la lista del último mensaje


def mostrados_antes(historial: dict[str, Any], limite: float) -> set[str]:
    """Destinos que salieron en la lista (por debajo del límite) del mensaje anterior.

    Los historiales anteriores a este cambio no tienen MOSTRADOS: se deducen de sus precios.
    """
    if MOSTRADOS in historial:
        return set(historial[MOSTRADOS])
    return {k.removeprefix("iv:").split("+")[0] for k, v in historial.items()
            if isinstance(v, (int, float)) and v < limite}


def _clave(r: Resultado, c: Combinacion) -> str:
    # "iv:" = precio de billete de ida y vuelta (antes se guardaba la suma de dos solo idas; así no se
    # comparan precios de distinto tipo).
    return f"iv:{r.destino}+escala" if c.con_escala else f"iv:{r.destino}"


def _euros(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def _tramo(v: Vuelo) -> str:
    dia = f"{DIAS[v.fecha.weekday()]} {v.fecha:%d/%m}"
    if not v.salida:  # vuelta de un billete de ida y vuelta: Google no da el horario
        return dia + (" (con escala)" if v.escala else "")
    texto = f"{dia} {v.salida}-{v.llegada} {v.aerolinea}"
    if v.escala:
        texto += f" (escala {v.escala} {v.espera_min // 60}h{v.espera_min % 60:02d})"
    return texto


def formatear_mensaje(
    cfg: Config, resultados: list[Resultado], historial: dict[str, Any], avisos: list[str], ahora: datetime
) -> str:
    """El mensaje en HTML de Telegram: los destinos que no salían en el mensaje anterior van en negrita (🆕)."""
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
        f"🕐 {ahora:%d/%m %H:%M} · {sum(1 for r in resultados if r.mejor)} de {len(cfg.destinos)} destinos "
        "con billete de ida y vuelta",
    ]

    vistos = mostrados_antes(historial, limite)
    negrita: set[int] = set()  # índices de las líneas que van en negrita

    def bloque(r: Resultado, c: Combinacion) -> list[str]:
        anterior = historial.get(_clave(r, c))
        nuevo = c.precio < limite and r.destino not in vistos
        marca = " 🆕" if nuevo else ""
        if anterior is not None and c.precio < anterior - 0.5:
            marca += f" 📉 antes {_euros(anterior)}"
        elif anterior is not None and c.precio > anterior + 0.5:
            marca += f" 📈 antes {_euros(anterior)}"
        if nuevo:
            negrita.add(len(lineas))
        return [
            f"• {r.nombre} ({r.destino}): {_euros(c.precio)}/pers · {_euros(c.precio * cfg.pasajeros)} total{marca}",
            f"   ida {_tramo(c.ida)} · vuelta {_tramo(c.vuelta)}",
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
    if cfg.url_mapa:
        lineas += ["", f"🗺️ Mapa: {cfg.url_mapa}"]
    for a in avisos:
        lineas += ["", f"⚠️ {a}"]
    lineas += [
        "",
        f"Precio = billete de ida y vuelta más barato, por persona (1 adulto) × {cfg.pasajeros}. Google Flights "
        "solo da el horario de la ida; el de la vuelta se elige al reservar. Sin equipaje facturado. "
        "Comprueba que quedan plazas para todos antes de comprar.",
    ]
    if negrita:
        lineas.insert(2, "En negrita y con 🆕: destinos que no estaban en el mensaje anterior.")
        negrita = {i + 1 if i >= 2 else i for i in negrita}
    html_lineas = [f"<b>{_html(l)}</b>" if i in negrita else _html(l) for i, l in enumerate(lineas)]
    texto = "\n".join(html_lineas)
    if len(texto) > LIMITE_TELEGRAM:  # se corta por líneas enteras, así no queda ninguna etiqueta abierta
        texto = texto[: LIMITE_TELEGRAM - 30].rsplit("\n", 1)[0] + "\n… (lista recortada)"
    return texto


def _html(texto: str) -> str:
    return html.escape(texto, quote=False)


def texto_plano(mensaje: str) -> str:
    """El mensaje sin las etiquetas HTML (para ntfy y por si Telegram rechaza el formato)."""
    return html.unescape(re.sub(r"</?b>", "", mensaje))


def enlace_google_flights(origen: str, destino: str, ida: date, vuelta: date) -> str:
    q = f"Flights from {origen} to {destino} on {ida.isoformat()} through {vuelta.isoformat()}"
    return "https://www.google.com/travel/flights?hl=es&curr=EUR&q=" + requests.utils.quote(q)


def datos_mapa(cfg: Config, resultados: list[Resultado], ahora: datetime, coordenadas: dict) -> dict:
    """Lo que pinta el mapa (mapa/index.html): origen y, por destino, su mejor billete directo y con escala."""

    def billete(c: Combinacion | None) -> dict | None:
        if not c:
            return None
        return {
            "precio": round(c.precio, 2),
            "total": round(c.precio * cfg.pasajeros, 2),
            "ida": {"fecha": c.ida.fecha.isoformat(), "salida": c.ida.salida, "llegada": c.ida.llegada,
                    "aerolinea": c.ida.aerolinea, "escala": c.ida.escala, "espera_min": c.ida.espera_min},
            "vuelta": {"fecha": c.vuelta.fecha.isoformat(), "con_escala": bool(c.vuelta.escala)},
            "google_flights": enlace_google_flights(cfg.origen, r.destino, c.ida.fecha, c.vuelta.fecha),
        }

    destinos = []
    for r in resultados:
        if r.destino not in coordenadas or not r.mejor:
            continue
        destinos.append({
            "codigo": r.destino, "nombre": r.nombre, "lat": coordenadas[r.destino][0],
            "lon": coordenadas[r.destino][1], "directo": billete(r.directo), "escala": billete(r.con_escala),
        })
    return {
        "actualizado": ahora.isoformat(timespec="minutes"),
        "origen": {"codigo": cfg.origen, "lat": coordenadas.get(cfg.origen, [0, 0])[0],
                   "lon": coordenadas.get(cfg.origen, [0, 0])[1]},
        "pasajeros": cfg.pasajeros,
        "precio_max_persona": cfg.precio_max_persona,
        "fechas_ida": [f.isoformat() for f in cfg.fechas_ida],
        "fechas_vuelta": [f.isoformat() for f in cfg.fechas_vuelta],
        "destinos": sorted(destinos, key=lambda d: min(b["precio"] for b in (d["directo"], d["escala"]) if b)),
    }


def actualizar_historial(resultados: list[Resultado], limite: float = float("inf")) -> dict[str, Any]:
    historial: dict[str, Any] = {}
    for r in resultados:
        for c in (r.directo, r.con_escala):
            if c:
                historial[_clave(r, c)] = c.precio
    if historial:
        historial[MOSTRADOS] = sorted(r.destino for r in resultados if r.mejor and r.mejor.precio < limite)
    return historial


# --------------------------------------------------------------------------- aviso


def notificar(texto: str) -> list[str]:
    """Envía el aviso (HTML de Telegram) por los canales configurados en variables de entorno.

    Devuelve los canales usados. Si Telegram rechaza el formato, se reenvía como texto plano.
    """
    usados = []
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        base = {"chat_id": chat, "disable_web_page_preview": True}
        r = requests.post(url, json={**base, "text": texto, "parse_mode": "HTML"}, timeout=20)
        if r.status_code == 400:
            print(f"Telegram rechazó el HTML ({r.text[:200]}); se envía sin formato.", file=sys.stderr)
            r = requests.post(url, json={**base, "text": texto_plano(texto)}, timeout=20)
        r.raise_for_status()
        usados.append("telegram")
    topic = os.getenv("NTFY_TOPIC")
    if topic:
        servidor = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        r = requests.post(
            f"{servidor}/{topic}",
            data=texto_plano(texto).encode("utf-8"),
            headers={"Title": "Vuelos baratos", "Tags": "airplane"},
            timeout=20,
        )
        r.raise_for_status()
        usados.append("ntfy")
    if not usados:
        print("(Sin canal de aviso configurado: define TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID o NTFY_TOPIC)")
    return usados


MAPA_DATOS = AQUI / "mapa" / "datos.json"


def guardar_datos_mapa(path: Path, datos: dict) -> None:
    """Guarda los datos del mapa (públicos: GitHub Pages los sirve tal cual)."""
    path.write_text(json.dumps(datos, indent=1, ensure_ascii=False), encoding="utf-8")


def informar(cfg: Config, historial_path: Path, resultados: list[Resultado], avisos: list[str],
             mapa_path: Path = MAPA_DATOS) -> int:
    """Manda el mensaje, guarda los datos del mapa y actualiza el historial."""
    historial = cargar_historial(historial_path)
    ahora = datetime.now(ZONA)
    coordenadas = json.loads((AQUI / "coordenadas.json").read_text(encoding="utf-8"))
    guardar_datos_mapa(mapa_path, datos_mapa(cfg, resultados, ahora, coordenadas))
    texto = formatear_mensaje(cfg, resultados, historial, avisos, ahora)
    print(texto)
    notificar(texto)
    nuevo = actualizar_historial(resultados, cfg.precio_max_persona)
    if nuevo:  # si todo falló, se conserva el historial anterior
        historial_path.write_text(json.dumps(nuevo, indent=2, ensure_ascii=False), encoding="utf-8")
    # Falla (y GitHub lo marca en rojo) solo si no se obtuvo ningún vuelo.
    return 0 if any(r.mejor for r in resultados) else 1


def ejecutar(cfg: Config, historial_path: Path, buscador_billete: BuscadorBillete = buscar_billete,
             mapa_path: Path = MAPA_DATOS) -> int:
    """Búsqueda completa en un solo proceso (para usarlo en tu ordenador)."""
    resultados, avisos = buscar(cfg, buscador_billete)
    return informar(cfg, historial_path, resultados, avisos, mapa_path)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=AQUI / "config.json")
    p.add_argument("--historial", type=Path, default=AQUI / "historial.json")
    p.add_argument("--probar-aviso", action="store_true")
    p.add_argument("--parte", type=int, help="consulta solo esta parte de los destinos y la guarda en parte_N.json")
    p.add_argument("--de", type=int, default=1, help="número total de partes")
    p.add_argument("--informe", nargs="+", type=Path, metavar="PARTE_JSON",
                   help="junta las partes ya consultadas y manda el mensaje")
    a = p.parse_args()
    if a.probar_aviso:
        return 0 if notificar("✅ Prueba del buscador de vuelos baratos") else 1
    cfg = Config.desde_archivo(a.config)
    if a.parte is not None:
        cfg.destinos = partes_de(cfg.destinos, a.parte, a.de)
        resultados, avisos = buscar(cfg)
        guardar_parte(AQUI / f"parte_{a.parte}.json", resultados, avisos)
        return 0
    if a.informe:
        return informar(cfg, a.historial, *leer_partes(cfg, a.informe))
    return ejecutar(cfg, a.historial)


if __name__ == "__main__":
    sys.exit(main())
