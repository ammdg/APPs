"""Vigila los precios de un trayecto en venta.renfe.com y avisa cuando entran en un rango.

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


# --------------------------------------------------------------------------- filtros y avisos


@dataclass
class Viaje:
    """Un sentido del viaje: origen → destino en unas fechas, con su franja horaria y su rango de precio."""

    origen: str
    destino: str
    fechas: list[date]
    precio_min: float
    precio_max: float
    hora_desde: str = "00:00"
    hora_hasta: str = "23:59"
    tipos_tren: list[str] | None = None  # p. ej. ["AVE", "ALVIA"]; None = todos
    duracion_max: int | None = None  # minutos; None = sin límite

    @classmethod
    def desde_dict(cls, d: dict[str, Any], defecto: dict[str, Any]) -> "Viaje":
        d = {**defecto, **d}
        fechas = d["fechas"] if isinstance(d.get("fechas"), list) else [d["fecha"]]
        viaje = cls(
            origen=d["origen"],
            destino=d["destino"],
            fechas=[datetime.strptime(x, "%Y-%m-%d").date() for x in fechas],
            precio_min=float(d.get("precio_min", 0)),
            precio_max=float(d["precio_max"]),
            hora_desde=d.get("hora_desde", "00:00"),
            hora_hasta=d.get("hora_hasta", "23:59"),
            duracion_max=_a_duracion(d.get("duracion_max")),
            tipos_tren=[t.upper() for t in d["tipos_tren"]] if d.get("tipos_tren") else None,
        )
        if viaje.precio_min > viaje.precio_max:
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
    """config.json tiene una lista "viajes"; los campos fuera de la lista valen para todos."""
    d = json.loads(path.read_text(encoding="utf-8"))
    defecto = {k: v for k, v in d.items() if k != "viajes"}
    return [Viaje.desde_dict(v, defecto) for v in d.get("viajes", [defecto])]


def trenes_en_rango(trenes: list[Tren], cfg: Viaje) -> list[Tren]:
    return [
        t
        for t in trenes
        if t.disponible
        and t.precio is not None
        and cfg.precio_min <= t.precio <= cfg.precio_max
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


def formatear_aviso(origen: str, destino: str, trenes: list[Tren]) -> str:
    lineas = [f"🚆 {origen} → {destino}: {len(trenes)} tren(es) en tu rango de precio"]
    for t in sorted(trenes, key=lambda t: (t.fecha, t.salida)):
        lineas.append(
            f"• {t.fecha.strftime('%d/%m')} {t.salida}-{t.llegada} ({formatear_duracion(t.duracion_min)}) "
            f"{t.tipo}: {t.precio:.2f} €"
        )
    lineas.append("Compra: https://www.renfe.com")
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


def _euros(precio: float) -> str:
    return f"{precio:.2f} €".replace(".", ",")


def formatear_resumen(consultas: list[Consulta], ahora: datetime) -> str:
    """Listado de todos los trenes con plazas; ✅ = cumple tus filtros, 🆕 = novedad desde el último aviso."""
    hay_novedades = any(c.nuevos for c in consultas)
    cabecera = "🔔 ¡Novedades!" if hay_novedades else "🕐 Sin cambios"
    lineas = [f"{cabecera} · {ahora:%H:%M} del {ahora:%d/%m}"]
    for c in consultas:
        lineas += ["", f"🚆 {c.origen} → {c.destino} · {DIAS[c.fecha.weekday()]} {c.fecha:%d/%m}"]
        if c.error:
            lineas.append(f"⚠️ No se pudo consultar Renfe: {c.error}")
            continue
        disponibles = sorted(
            (t for t in c.trenes if t.disponible and t.precio is not None), key=lambda t: t.salida
        )
        if not disponibles:
            lineas.append("Ningún tren con plazas.")
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
    lineas += ["", "✅ cumple tus filtros · 🆕 nuevo o más barato", "Compra: https://www.renfe.com"]
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
            headers={"Title": "Precio Renfe en rango", "Tags": "train"},
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
        origen, destino = obtener_estacion(viaje.origen), obtener_estacion(viaje.destino)
        del_viaje: list[Tren] = []
        de_cada_fecha: list[tuple[Consulta, list[Tren]]] = []
        for fecha in viaje.fechas:
            etiqueta = f"{origen.nombre} → {destino.nombre} {fecha}"
            if fecha < hoy:
                print(f"{etiqueta}: fecha pasada, se ignora")
                continue
            consultas += 1
            try:
                trenes = consultar_renfe(origen, destino, fecha)
            except (requests.RequestException, ErrorRenfe, KeyError, ValueError) as e:
                errores += 1
                print(f"{etiqueta}: ERROR consultando Renfe: {e!r}", file=sys.stderr)
                resultados.append(
                    Consulta(origen.nombre, destino.nombre, fecha, [], set(), set(), type(e).__name__)
                )
                continue
            con_precio = [t for t in trenes if t.disponible and t.precio is not None]
            minimo = min((t.precio for t in con_precio), default=None)
            print(
                f"{etiqueta}: {len(trenes)} trenes, {len(con_precio)} con plazas"
                + (f", más barato {minimo:.2f} €" if minimo is not None else "")
            )
            filtrados = trenes_en_rango(trenes, viaje)
            del_viaje += filtrados
            consulta = Consulta(origen.nombre, destino.nombre, fecha, trenes, {t.clave for t in filtrados}, set())
            resultados.append(consulta)
            de_cada_fecha.append((consulta, filtrados))
            time.sleep(2)  # no encadenar peticiones a Renfe
        nuevos = nuevos_para_avisar(del_viaje, estado)
        for consulta, filtrados in de_cada_fecha:
            consulta.nuevos = {t.clave for t in filtrados if t in nuevos}
        print(
            f"{origen.nombre} → {destino.nombre} en rango "
            f"[{viaje.precio_min:.2f}-{viaje.precio_max:.2f} €, {viaje.hora_desde}-{viaje.hora_hasta}"
            + (f", máx. {formatear_duracion(viaje.duracion_max)}" if viaje.duracion_max else "")
            + "]: "
            f"{len(del_viaje)}; nuevos: {len(nuevos)}"
        )
        if nuevos:
            avisos.append(formatear_aviso(origen.nombre, destino.nombre, nuevos))
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
