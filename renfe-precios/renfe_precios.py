"""Vigila los precios de un trayecto en venta.renfe.com (y en avión, vía Google Flights) y avisa cuando
entran en un rango.

La consulta a Renfe está adaptada de renfe-bot (https://github.com/emartinez-dev/renfe-bot,
licencia MIT, (c) 2023 Francisco Enrique Martínez Díaz). Usa el backend DWR que emplea la
propia web de venta; no es una API pública y puede dejar de funcionar si Renfe la cambia.

Uso:
    python renfe_precios.py                     # una comprobación (lo que hace GitHub Actions)
    python renfe_precios.py --cada 60           # bucle local: comprueba cada 60 minutos
    python renfe_precios.py --buscar-estacion valencia
    python renfe_precios.py --probar-aviso      # envía una notificación de prueba
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
import sys
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Iterator

import json5
import requests

AQUI = Path(__file__).resolve().parent
ESTACIONES_PATH = AQUI / "estaciones.json"

SEARCH_URL = "https://venta.renfe.com/vol/buscarTren.do?Idioma=es&Pais=ES"
DWR_ENDPOINT = "https://venta.renfe.com/vol/dwr/call/plaincall/"
SYSTEM_ID_URL = f"{DWR_ENDPOINT}__System.generateId.dwr"
UPDATE_SESSION_URL = f"{DWR_ENDPOINT}buyEnlacesManager.actualizaObjetosSesion.dwr"
TRAIN_LIST_URL = f"{DWR_ENDPOINT}trainEnlacesManager.getTrainsList.dwr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
FECHA = "%d/%m/%Y"
ZONA = ZoneInfo("Europe/Madrid")
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


class ErrorRenfe(Exception):
    """Renfe respondió algo que no sabemos interpretar."""


# --------------------------------------------------------------------------- estaciones


@dataclass(frozen=True)
class Estacion:
    nombre: str
    codigo: str  # campo "clave" de Renfe, p. ej. "0071,60000,00600"


def _normaliza(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes).strip().upper()


def cargar_estaciones() -> dict[str, dict[str, Any]]:
    return json.loads(ESTACIONES_PATH.read_text(encoding="utf-8"))


def buscar_estaciones(texto: str) -> list[str]:
    buscado = _normaliza(texto)
    return [nombre for nombre in cargar_estaciones() if buscado in _normaliza(nombre)]


def obtener_estacion(nombre: str) -> Estacion:
    estaciones = cargar_estaciones()
    exacta = {_normaliza(k): v for k, v in estaciones.items()}.get(_normaliza(nombre))
    if exacta is None:
        parecidas = buscar_estaciones(nombre)[:10]
        pista = f" ¿Quizá: {', '.join(parecidas)}?" if parecidas else ""
        raise SystemExit(f"No encuentro la estación '{nombre}'.{pista}")
    return Estacion(nombre=exacta["desgEstacion"], codigo=exacta["clave"])


# --------------------------------------------------------------------------- consulta a Renfe


@dataclass(frozen=True)
class Tren:
    fecha: date
    salida: str  # "HH:MM"
    llegada: str
    duracion_min: int
    precio: float | None
    disponible: bool
    tipo: str
    sentido: str = ""  # "ORIGEN>DESTINO", para no mezclar ida y vuelta en el estado

    @property
    def clave(self) -> str:
        return f"{self.fecha.isoformat()} {self.salida} {self.tipo} {self.sentido}".rstrip()


def _contador() -> Iterator[int]:
    n = 0
    while True:
        yield n
        n += 1


def _tokenify(numero: int) -> str:
    charmap = "1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ*$"
    buf = []
    while numero > 0:
        buf.append(charmap[numero & 0x3F])
        numero //= 64
    return "".join(buf)


def extraer_token_dwr(texto: str) -> str:
    m = re.search(r'r\.handleCallback\("[^"]+","[^"]+","([^"]+)"\)', texto)
    if not m:
        raise ErrorRenfe("No se encontró el token DWR en la respuesta de Renfe")
    return m.group(1)


def extraer_lista_trenes(texto: str) -> dict[str, Any]:
    m = re.search(r"r\.handleCallback\([^,]+,\s*[^,]+,\s*(\{.*\})\);", texto, re.DOTALL)
    if not m:
        raise ErrorRenfe("La respuesta de Renfe no contiene la lista de trenes")
    return json5.loads(m.group(1))


def _a_precio(valor: Any) -> float | None:
    if valor in (None, ""):
        return None
    try:
        return float(str(valor).replace(".", "").replace(",", ".")) if "," in str(valor) else float(valor)
    except ValueError:
        return None


def _minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _duracion_por_horas(salida: str, llegada: str) -> int:
    """Duración a partir de las horas; si llega pasada la medianoche, suma un día."""
    return (_minutos(llegada) - _minutos(salida)) % (24 * 60)


def parsear_trenes(datos: dict[str, Any], fecha: date, sentido: str = "") -> list[Tren]:
    trenes = []
    # listadoTrenes[0] es la ida; solo pedimos ida.
    for t in datos["listadoTrenes"][0]["listviajeViewEnlaceBean"]:
        precio = _a_precio(t.get("tarifaMinima"))
        disponible = (
            not t.get("completo")
            and t.get("razonNoDisponible") in ("", "8", None)
            and precio is not None
            and not t.get("soloPlazaH")
        )
        trenes.append(
            Tren(
                fecha=fecha,
                salida=t["horaSalida"],
                llegada=t["horaLlegada"],
                duracion_min=int(t.get("duracionViajeTotalEnMinutos") or 0)
                or _duracion_por_horas(t["horaSalida"], t["horaLlegada"]),
                precio=precio,
                disponible=bool(disponible),
                tipo=t.get("tipoTrenUno") or "N/A",
                sentido=sentido,
            )
        )
    return trenes


def consultar_renfe(origen: Estacion, destino: Estacion, fecha: date, timeout: int = 30) -> list[Tren]:
    """Replica los pasos que hace el navegador en venta.renfe.com para un viaje de ida, 1 adulto."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    search_id = "_" + "".join(random.choices(string.ascii_letters + string.digits, k=4))
    batch = _contador()
    f = fecha.strftime(FECHA)
    page = f"page=%2Fvol%2FbuscarTrenEnlaces.do%3Fc%3D{search_id}\n"

    busqueda = {
        "origen": {"code": origen.codigo, "name": origen.nombre},
        "destino": {"code": destino.codigo, "name": destino.nombre},
        "pasajerosAdultos": 1,
        "pasajerosNinos": 0,
        "pasajerosSpChild": 0,
    }
    s.cookies.set("Search", str(busqueda), domain=".renfe.com", path="/")

    r = s.post(
        SEARCH_URL,
        timeout=timeout,
        data={
            "tipoBusqueda": "autocomplete",
            "currenLocation": "menuBusqueda",
            "vengoderenfecom": "SI",
            "desOrigen": origen.nombre,
            "desDestino": destino.nombre,
            "cdgoOrigen": origen.codigo,
            "cdgoDestino": destino.codigo,
            "idiomaBusqueda": "ES",
            "FechaIdaSel": f,
            "FechaVueltaSel": "",
            "_fechaIdaVisual": f,
            "_fechaVueltaVisual": "",
            "adultos_": "1",
            "ninos_": "0",
            "ninosMenores": "0",
            "codPromocional": "",
            "plazaH": "false",
            "sinEnlace": "false",
            "asistencia": "false",
            "franjaHoraI": "",
            "franjaHoraV": "",
            "Idioma": "es",
            "Pais": "ES",
        },
    )
    r.raise_for_status()

    def generate_id() -> requests.Response:
        return s.post(
            SYSTEM_ID_URL,
            timeout=timeout,
            data=(
                "callCount=1\nc0-scriptName=__System\nc0-methodName=generateId\nc0-id=0\n"
                f"batchId={next(batch)}\ninstanceId=0\n{page}scriptSessionId=\nwindowName=\n"
            ),
        )

    generate_id()
    r = generate_id()
    r.raise_for_status()
    token = extraer_token_dwr(r.text)
    s.cookies.set("DWRSESSIONID", token, path="/vol", domain="venta.renfe.com")
    script_session_id = (
        f"{token}/{_tokenify(int(time.time() * 1000))}-{_tokenify(int(random.random() * 1e16))}"
    )

    r = s.post(
        UPDATE_SESSION_URL,
        timeout=timeout,
        data=(
            "callCount=1\nwindowName=\nc0-scriptName=buyEnlacesManager\n"
            "c0-methodName=actualizaObjetosSesion\nc0-id=0\n"
            f"c0-e1=string:{search_id}\nc0-e2=string:\n"
            "c0-param0=array:[reference:c0-e1,reference:c0-e2]\n"
            f"batchId={next(batch)}\ninstanceId=0\n{page}scriptSessionId={script_session_id}\n"
        ),
    )
    r.raise_for_status()

    r = s.post(
        TRAIN_LIST_URL,
        timeout=timeout,
        data=(
            "callCount=1\nwindowName=\nc0-scriptName=trainEnlacesManager\n"
            "c0-methodName=getTrainsList\nc0-id=0\n"
            "c0-e1=string:false\nc0-e2=string:false\nc0-e3=string:false\n"
            "c0-e4=string:\nc0-e5=string:\nc0-e6=string:\nc0-e7=string:\n"
            f"c0-e8=string:{urllib.parse.quote_plus(f)}\nc0-e9=string:\n"
            "c0-e10=string:1\nc0-e11=string:0\nc0-e12=string:0\n"
            "c0-e13=string:I\nc0-e14=string:\n"
            "c0-param0=Object_Object:{atendo:reference:c0-e1, sinEnlace:reference:c0-e2, "
            "plazaH:reference:c0-e3, tipoFranjaI:reference:c0-e4, tipoFranjaV:reference:c0-e5, "
            "horaFranjaIda:reference:c0-e6, horaFranjaVuelta:reference:c0-e7, "
            "fechaSalida:reference:c0-e8, fechaVuelta:reference:c0-e9, adultos:reference:c0-e10, "
            "ninos:reference:c0-e11, ninosMenores:reference:c0-e12, trayecto:reference:c0-e13, "
            "idaVuelta:reference:c0-e14}\n"
            f"batchId={next(batch)}\ninstanceId=0\n{page}scriptSessionId={script_session_id}\n"
        ),
    )
    r.raise_for_status()
    return parsear_trenes(extraer_lista_trenes(r.text), fecha, f"{origen.codigo}>{destino.codigo}")


# --------------------------------------------------------------------------- consulta de vuelos


def _precio_legible(itinerario: Any) -> bool:
    try:
        itinerario[1][0][1]
        return True
    except (TypeError, IndexError):
        return False


def juntar_mejores_opciones(html: str) -> str:
    """Hace que fast-flights lea también el bloque "Mejores opciones" de Google Flights.

    Google reparte los vuelos en dos listas: "mejores opciones" (payload[2]) y el resto (payload[3]).
    fast-flights 3.1.0 solo lee la segunda, y en la primera suelen estar los más baratos. Aquí se juntan
    ambas en payload[3] y se quitan los itinerarios cuyo precio no se puede leer, que harían fallar la
    lectura de toda la página. Si el HTML no tiene la forma esperada, se devuelve igual.
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
    todos = [k for k in [*mejores, *otros] if _precio_legible(k)]
    if not todos:
        return html
    if payload[3]:
        payload[3][0] = todos
    else:
        payload[3] = [todos]
    js = cabeza + "data:" + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") + "," + cola
    return html[: m.start(2)] + js + html[m.end(2) :]


def obtener_vuelos(consulta: Any) -> list[Any]:
    """Descarga la página de Google Flights y la lee con fast-flights, incluidas las "mejores opciones"."""
    from fast_flights import fetch_flights_html
    from fast_flights.parser import parse

    return parse(juntar_mejores_opciones(fetch_flights_html(consulta)))


def consultar_vuelos(viaje: Viaje, fecha: date) -> list[Tren]:
    """Vuelos de ida (1 adulto, turista) según Google Flights, vía la librería fast-flights.

    Se reutiliza Tren para que filtros, avisos y estado funcionen igual que con los trenes.
    """
    from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query

    consulta = create_query(
        flights=[
            FlightQuery(
                date=fecha.isoformat(),
                from_airport=viaje.origen,
                to_airport=viaje.destino,
                max_stops=0 if viaje.solo_directos else None,
                airlines=viaje.aerolineas,
            )
        ],
        trip="one-way",
        passengers=Passengers(adults=1),
        language="es",
        currency="EUR",
    )
    try:
        resultados = obtener_vuelos(consulta)
    except FlightsNotFound:
        return []

    vuelos = []
    for r in resultados:
        tramos = r.flights
        if not tramos or (viaje.solo_directos and len(tramos) > 1):
            continue
        salida, llegada = tramos[0].departure, tramos[-1].arrival
        if date(*salida.date) != fecha:
            continue
        hs, hl = "%02d:%02d" % salida.time, "%02d:%02d" % llegada.time
        vuelos.append(
            Tren(
                fecha=fecha,
                salida=hs,
                llegada=hl,
                duracion_min=sum(t.duration for t in tramos) or _duracion_por_horas(hs, hl),
                precio=float(r.price) if r.price else None,
                disponible=bool(r.price),
                tipo=", ".join(r.airlines) or "N/A",
                sentido=f"{viaje.origen}>{viaje.destino} avion",
            )
        )
    # Google puede repetir el mismo vuelo con varias tarifas: nos quedamos con la más barata.
    mas_baratos: dict[str, Tren] = {}
    for v in vuelos:
        if v.clave not in mas_baratos or (v.precio or 1e9) < (mas_baratos[v.clave].precio or 1e9):
            mas_baratos[v.clave] = v
    return list(mas_baratos.values())


# --------------------------------------------------------------------------- filtros y avisos


@dataclass
class Viaje:
    """Un sentido del viaje: origen → destino en unas fechas, con su franja horaria y su rango de precio.

    En tren, origen/destino son nombres de estación de Renfe; en avión, códigos de aeropuerto (MAD, PNA).
    """

    origen: str
    destino: str
    fechas: list[date]
    precio_min: float
    precio_max: float | None  # None = sin límite
    hora_desde: str = "00:00"
    hora_hasta: str = "23:59"
    tipos_tren: list[str] | None = None  # p. ej. ["AVE", "ALVIA"]; None = todos
    duracion_max: int | None = None  # minutos; None = sin límite
    medio: str = "tren"  # "tren" o "avion"
    aerolineas: list[str] | None = None  # códigos IATA, p. ej. ["IB"]; None = todas
    solo_directos: bool = True

    @classmethod
    def desde_dict(cls, d: dict[str, Any], defecto: dict[str, Any], medio: str = "tren") -> "Viaje":
        d = {**defecto, **d}
        fechas = d["fechas"] if isinstance(d.get("fechas"), list) else [d["fecha"]]
        viaje = cls(
            origen=d["origen"],
            destino=d["destino"],
            fechas=[datetime.strptime(x, "%Y-%m-%d").date() for x in fechas],
            precio_min=float(d.get("precio_min", 0)),
            precio_max=float(d["precio_max"]) if d.get("precio_max") is not None else None,
            hora_desde=d.get("hora_desde", "00:00"),
            hora_hasta=d.get("hora_hasta", "23:59"),
            duracion_max=_a_duracion(d.get("duracion_max")),
            tipos_tren=[t.upper() for t in d["tipos_tren"]] if d.get("tipos_tren") else None,
            medio=medio,
            aerolineas=[a.upper() for a in d["aerolineas"]] if d.get("aerolineas") else None,
            solo_directos=bool(d.get("solo_directos", True)),
        )
        if medio == "avion":
            viaje.origen, viaje.destino = viaje.origen.upper(), viaje.destino.upper()
        if viaje.precio_max is not None and viaje.precio_min > viaje.precio_max:
            raise SystemExit(f"{viaje.origen} → {viaje.destino}: precio_min mayor que precio_max")
        return viaje


def _a_duracion(valor: Any) -> int | None:
    """Acepta minutos (200) o "horas:minutos" ("3:30"). None o "" = sin límite."""
    if valor in (None, ""):
        return None
    if isinstance(valor, str) and ":" in valor:
        return _minutos(valor)
    return int(valor)


def formatear_duracion(minutos: int) -> str:
    return f"{minutos // 60}h{minutos % 60:02d}"


def cargar_config(path: Path) -> list[Viaje]:
    """config.json tiene una lista "viajes" (tren) y, opcionalmente, una sección "vuelos" con su
    propia lista "viajes". Los campos fuera de cada lista valen para todos los de esa lista."""
    d = json.loads(path.read_text(encoding="utf-8"))
    defecto = {k: v for k, v in d.items() if k not in ("viajes", "vuelos")}
    viajes = [Viaje.desde_dict(v, defecto) for v in d.get("viajes", [defecto])]
    vuelos = d.get("vuelos") or {}
    if vuelos.get("activo", True):
        defecto_vuelos = {k: v for k, v in vuelos.items() if k not in ("viajes", "activo")}
        viajes += [Viaje.desde_dict(v, defecto_vuelos, medio="avion") for v in vuelos.get("viajes", [])]
    return viajes


def trenes_en_rango(trenes: list[Tren], cfg: Viaje) -> list[Tren]:
    return [
        t
        for t in trenes
        if t.disponible
        and t.precio is not None
        and cfg.precio_min <= t.precio
        and (cfg.precio_max is None or t.precio <= cfg.precio_max)
        and cfg.hora_desde <= t.salida <= cfg.hora_hasta
        and (cfg.tipos_tren is None or t.tipo.upper() in cfg.tipos_tren)
        and (cfg.duracion_max is None or t.duracion_min <= cfg.duracion_max)
    ]


def cargar_estado(path: Path) -> dict[str, float]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def nuevos_para_avisar(en_rango: list[Tren], estado: dict[str, float]) -> list[Tren]:
    """Solo avisa de trenes que no se habían notificado o que han bajado de precio desde el aviso."""
    return [t for t in en_rango if t.clave not in estado or t.precio < estado[t.clave]]


def actualizar_estado(estado: dict[str, float], en_rango: list[Tren], hoy: date) -> dict[str, float]:
    # Olvida trenes que han salido del rango (si vuelven a entrar, se avisará de nuevo) y fechas pasadas.
    vigentes = {t.clave: t.precio for t in en_rango}
    return {
        k: min(v, estado.get(k, v))
        for k, v in vigentes.items()
        if date.fromisoformat(k.split()[0]) >= hoy
    }


def formatear_aviso(origen: str, destino: str, trenes: list[Tren], medio: str = "tren") -> str:
    que = "✈️ {o} → {d}: {n} vuelo(s)" if medio == "avion" else "🚆 {o} → {d}: {n} tren(es)"
    lineas = [que.format(o=origen, d=destino, n=len(trenes)) + " en tu rango de precio"]
    for t in sorted(trenes, key=lambda t: (t.fecha, t.salida)):
        lineas.append(
            f"• {t.fecha.strftime('%d/%m')} {t.salida}-{t.llegada} ({formatear_duracion(t.duracion_min)}) "
            f"{t.tipo}: {t.precio:.2f} €"
        )
    lineas.append("Compra: " + ("https://www.iberia.com" if medio == "avion" else "https://www.renfe.com"))
    return "\n".join(lineas)


@dataclass
class Consulta:
    """Resultado de consultar un sentido en una fecha, para el resumen horario."""

    origen: str
    destino: str
    fecha: date
    trenes: list[Tren]
    en_rango: set[str]  # claves de los trenes que cumplen todos los filtros
    nuevos: set[str]  # claves de los que son nuevos o han bajado de precio
    error: str | None = None
    medio: str = "tren"


def _euros(precio: float) -> str:
    return f"{precio:.2f} €".replace(".", ",")


def formatear_resumen(consultas: list[Consulta], ahora: datetime) -> str:
    """Listado de todos los trenes con plazas; ✅ = cumple tus filtros, 🆕 = novedad desde el último aviso."""
    hay_novedades = any(c.nuevos for c in consultas)
    cabecera = "🔔 ¡Novedades!" if hay_novedades else "🕐 Sin cambios"
    lineas = [f"{cabecera} · {ahora:%H:%M} del {ahora:%d/%m}"]
    for c in consultas:
        avion = c.medio == "avion"
        icono = "✈️" if avion else "🚆"
        lineas += ["", f"{icono} {c.origen} → {c.destino} · {DIAS[c.fecha.weekday()]} {c.fecha:%d/%m}"]
        if c.error:
            lineas.append(f"⚠️ No se pudo consultar {'Google Flights' if avion else 'Renfe'}: {c.error}")
            continue
        disponibles = sorted(
            (t for t in c.trenes if t.disponible and t.precio is not None), key=lambda t: t.salida
        )
        if not disponibles:
            lineas.append("Ningún vuelo encontrado." if avion else "Ningún tren con plazas.")
        for t in disponibles:
            marca = "✅" if t.clave in c.en_rango else "▫️"
            nuevo = " 🆕" if t.clave in c.nuevos else ""
            lineas.append(
                f"{marca} {t.salida}-{t.llegada} ({formatear_duracion(t.duracion_min)}) "
                f"{t.tipo} {_euros(t.precio)}{nuevo}"
            )
        completos = len(c.trenes) - len(disponibles)
        if completos:
            lineas.append(f"(+{completos} sin plazas)")
    lineas += ["", "✅ cumple tus filtros · 🆕 nuevo o más barato", "Trenes: https://www.renfe.com"]
    if any(c.medio == "avion" for c in consultas):
        lineas.append("Vuelos: https://www.iberia.com (precios de Google Flights)")
    return "\n".join(lineas)


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
            headers={"Title": "Precios tren y avión", "Tags": "train"},
            timeout=20,
        )
        r.raise_for_status()
        usados.append("ntfy")
    if not usados:
        print("(Sin canal de aviso configurado: define TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID o NTFY_TOPIC)")
    return usados


# --------------------------------------------------------------------------- orquestación


def comprobar(viajes: list[Viaje], estado_path: Path, resumen: bool = False) -> int:
    """Consulta todos los viajes. Avisa si hay novedades o, con resumen=True, siempre con el listado."""
    hoy = date.today()
    en_rango: list[Tren] = []
    avisos: list[str] = []
    resultados: list[Consulta] = []
    consultas = errores = 0
    estado = cargar_estado(estado_path)
    for viaje in viajes:
        avion = viaje.medio == "avion"
        if avion:
            nombre_o, nombre_d = viaje.origen, viaje.destino
        else:
            origen, destino = obtener_estacion(viaje.origen), obtener_estacion(viaje.destino)
            nombre_o, nombre_d = origen.nombre, destino.nombre
        fuente = "Google Flights" if avion else "Renfe"
        del_viaje: list[Tren] = []
        de_cada_fecha: list[tuple[Consulta, list[Tren]]] = []
        for fecha in viaje.fechas:
            etiqueta = f"{'✈️' if avion else '🚆'} {nombre_o} → {nombre_d} {fecha}"
            if fecha < hoy:
                print(f"{etiqueta}: fecha pasada, se ignora")
                continue
            consultas += 1
            try:
                trenes = consultar_vuelos(viaje, fecha) if avion else consultar_renfe(origen, destino, fecha)
            except Exception as e:  # noqa: BLE001 - una fuente caída no debe impedir avisar de la otra
                errores += 1
                print(f"{etiqueta}: ERROR consultando {fuente}: {e!r}", file=sys.stderr)
                resultados.append(
                    Consulta(nombre_o, nombre_d, fecha, [], set(), set(), type(e).__name__, viaje.medio)
                )
                continue
            con_precio = [t for t in trenes if t.disponible and t.precio is not None]
            minimo = min((t.precio for t in con_precio), default=None)
            print(
                f"{etiqueta}: {len(trenes)} {'vuelos' if avion else 'trenes'}, {len(con_precio)} con precio"
                + (f", más barato {minimo:.2f} €" if minimo is not None else "")
            )
            filtrados = trenes_en_rango(trenes, viaje)
            del_viaje += filtrados
            consulta = Consulta(
                nombre_o, nombre_d, fecha, trenes, {t.clave for t in filtrados}, set(), medio=viaje.medio
            )
            resultados.append(consulta)
            de_cada_fecha.append((consulta, filtrados))
            time.sleep(2)  # no encadenar peticiones
        nuevos = nuevos_para_avisar(del_viaje, estado)
        for consulta, filtrados in de_cada_fecha:
            consulta.nuevos = {t.clave for t in filtrados if t in nuevos}
        tope = f"{viaje.precio_max:.2f}" if viaje.precio_max is not None else "∞"
        print(
            f"{nombre_o} → {nombre_d} en rango "
            f"[{viaje.precio_min:.2f}-{tope} €, {viaje.hora_desde}-{viaje.hora_hasta}"
            + (f", máx. {formatear_duracion(viaje.duracion_max)}" if viaje.duracion_max else "")
            + "]: "
            f"{len(del_viaje)}; nuevos: {len(nuevos)}"
        )
        if nuevos:
            avisos.append(formatear_aviso(nombre_o, nombre_d, nuevos, viaje.medio))
        en_rango += del_viaje

    if resumen and resultados:
        texto = formatear_resumen(resultados, datetime.now(ZONA))
    else:
        texto = "\n\n".join(avisos)
    if texto:
        print(texto)
        try:
            notificar(texto)
        except requests.RequestException as e:
            # Sin guardar estado: la próxima hora se reintentará el aviso.
            print(f"ERROR enviando el aviso: {e!r}", file=sys.stderr)
            return 1
    estado_path.write_text(
        json.dumps(actualizar_estado(estado, en_rango, hoy), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # Falla solo si no se pudo consultar nada, para que GitHub avise del problema.
    return 1 if consultas and errores == consultas else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=AQUI / "config.json")
    p.add_argument("--estado", type=Path, default=AQUI / "estado.json")
    p.add_argument("--cada", type=int, metavar="MINUTOS", help="repetir en bucle cada N minutos")
    p.add_argument("--buscar-estacion", metavar="TEXTO")
    p.add_argument("--probar-aviso", action="store_true")
    a = p.parse_args()

    if a.buscar_estacion:
        for nombre in buscar_estaciones(a.buscar_estacion):
            print(nombre)
        return 0
    if a.probar_aviso:
        return 0 if notificar("✅ Prueba del vigilante de precios Renfe") else 1

    viajes = cargar_config(a.config)
    resumen = bool(json.loads(a.config.read_text(encoding="utf-8")).get("resumen_cada_hora", False))
    if not a.cada:
        return comprobar(viajes, a.estado, resumen)
    while True:
        print(f"--- {datetime.now():%Y-%m-%d %H:%M}")
        comprobar(viajes, a.estado, resumen)
        time.sleep(a.cada * 60)


if __name__ == "__main__":
    sys.exit(main())
