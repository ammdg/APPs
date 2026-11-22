"""Descarga el último valor liquidativo de cada fondo de fondos.json y lo añade a precios.json.

Fuentes (no son APIs oficiales, son páginas web públicas; pueden cambiar sin aviso):
  1. FT Markets (markets.ft.com), ficha del fondo por ISIN.
  2. Quefondos (quefondos.com), ficha del fondo por ISIN, como reserva.

precios.json solo contiene precios públicos de los fondos, nunca participaciones ni importes.

Uso:
  python precios.py              # actualiza precios.json
  python precios.py --probar ISIN  # muestra lo que devuelve cada fuente para un ISIN, sin guardar
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent
FONDOS = AQUI / "fondos.json"
PRECIOS = AQUI / "precios.json"
MAX_PUNTOS = 2000  # unos 8 años de días hábiles por fondo

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MESES_EN = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class SinPrecio(Exception):
    pass


# ---------------------------------------------------------------- números y fechas

def numero(texto: str, decimal: str) -> float:
    """'44,769.80' (decimal='.') o '44.769,80' (decimal=',') -> 44769.8"""
    t = texto.strip().replace("\xa0", "").replace(" ", "")
    if decimal == ".":
        t = t.replace(",", "")
    else:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def fecha_ft(texto: str) -> date:
    """'Nov 20 2026' o 'Nov 20, 2026' -> date(2026, 11, 20)"""
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", texto)
    if not m or m.group(1).lower() not in MESES_EN:
        raise SinPrecio(f"fecha FT no reconocida: {texto!r}")
    return date(int(m.group(3)), MESES_EN[m.group(1).lower()], int(m.group(2)))


def texto_plano(pagina: str) -> str:
    sin_scripts = re.sub(r"<(script|style)\b.*?</\1>", " ", pagina, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", sin_scripts)))


# ---------------------------------------------------------------- FT Markets

def parsear_ft(pagina: str) -> dict:
    valores = re.findall(r'mod-ui-data-list__value"\s*>\s*([^<]+)<', pagina)
    if not valores:
        raise SinPrecio("FT: no aparece el precio")
    nav = numero(valores[0], ".")
    plano = texto_plano(pagina)
    m = re.search(r"as of ([A-Za-z]{3}[a-z]*\.? \d{1,2},? \d{4})", plano)
    if not m:
        raise SinPrecio("FT: no aparece la fecha del precio")
    moneda = re.search(r"Price \(([A-Z]{3})\)", plano)
    return {"fecha": fecha_ft(m.group(1)).isoformat(), "nav": nav,
            "moneda": moneda.group(1) if moneda else None, "fuente": "ft"}


def de_ft(sesion: requests.Session, isin: str, moneda: str) -> dict:
    r = sesion.get("https://markets.ft.com/data/funds/tearsheet/summary",
                   params={"s": f"{isin}:{moneda}"}, timeout=30)
    r.raise_for_status()
    return parsear_ft(r.text)


# ---------------------------------------------------------------- Quefondos

def parsear_quefondos(pagina: str) -> dict:
    plano = texto_plano(pagina)
    m = re.search(r"Valor liquidativo\s*:?\s*([\d.]+,\d+)\s*([A-Z]{3})?", plano)
    if not m:
        raise SinPrecio("Quefondos: no aparece el valor liquidativo")
    f = re.search(r"Fecha\s*:?\s*(\d{2})/(\d{2})/(\d{4})", plano)
    if not f:
        raise SinPrecio("Quefondos: no aparece la fecha")
    return {"fecha": date(int(f.group(3)), int(f.group(2)), int(f.group(1))).isoformat(),
            "nav": numero(m.group(1), ","), "moneda": m.group(2), "fuente": "quefondos"}


def de_quefondos(sesion: requests.Session, isin: str, moneda: str) -> dict:
    r = sesion.get("https://www.quefondos.com/es/fondos/ficha/index.html",
                   params={"isin": isin}, timeout=30)
    r.raise_for_status()
    return parsear_quefondos(r.text)


# ---------------------------------------------------------------- Morningstar

# Claves públicas que usan las propias webs de Morningstar para sus gráficas (no es una API oficial).
MS_SERIES = [
    ("https://tools.morningstar.es/api/rest.svc/timeseries_price/2nhcdckzon", "es"),
    ("https://tools.morningstar.co.uk/api/rest.svc/timeseries_price/t92wz0sj7c", "uk"),
]


def morningstar_id(sesion: requests.Session, isin: str) -> str:
    r = sesion.get("https://www.morningstar.es/es/util/SecuritySearch.ashx",
                   params={"q": isin, "limit": 1, "preferedList": ""}, timeout=30)
    r.raise_for_status()
    m = re.search(r'"i"\s*:\s*"([A-Z0-9]{10})"', r.text)
    if not m:
        raise SinPrecio(f"Morningstar: no encuentro el fondo ({r.text[:120]!r})")
    return m.group(1)


def parsear_serie_morningstar(datos) -> dict:
    """COMPACTJSON: [[milisegundos_epoch, precio], ...] -> último punto."""
    if isinstance(datos, dict):  # algunas variantes envuelven la serie
        datos = next((v for v in datos.values() if isinstance(v, list)), [])
    puntos = [p for p in datos if isinstance(p, list) and len(p) >= 2 and p[1] is not None]
    if not puntos:
        raise SinPrecio("Morningstar: serie vacía")
    ms, nav = puntos[-1][:2]
    fecha = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    return {"fecha": fecha.isoformat(), "nav": float(nav), "moneda": None, "fuente": "morningstar"}


def de_morningstar(sesion: requests.Session, isin: str, moneda: str) -> dict:
    secid = morningstar_id(sesion, isin)
    desde = date.fromordinal(date.today().toordinal() - 20).isoformat()
    errores = []
    for url, nombre in MS_SERIES:
        try:
            r = sesion.get(url, params={"id": secid, "currencyId": moneda, "idtype": "Morningstar",
                                        "frequency": "daily", "startDate": desde,
                                        "outputType": "COMPACTJSON"}, timeout=30)
            r.raise_for_status()
            p = parsear_serie_morningstar(r.json())
            p["moneda"] = moneda
            return p
        except Exception as e:
            errores.append(f"{nombre} {secid}: {str(e)[:120]}")
    raise SinPrecio("Morningstar: " + " | ".join(errores))


FUENTES = [("ft", de_ft), ("quefondos", de_quefondos), ("morningstar", de_morningstar)]

# FT redondea a 2 decimales; Quefondos y Morningstar suelen dar el valor liquidativo completo. Se usan
# solo si cuadran con el de FT, para no fiarse a ciegas de una página que podría leerse mal.
TOLERANCIA_MISMO_DIA = 0.005  # 0,5 %: el redondeo de FT nunca se acerca a esto
TOLERANCIA_OTRO_DIA = 0.05  # 5 %: si la otra fuente tiene un día más reciente, solo se descarta lo absurdo


def elegir(ft: dict | None, precisas) -> tuple[dict | None, str | None]:
    """Decide qué precio usar entre FT y las fuentes precisas (dict o lista, por preferencia).
    Devuelve (precio, aviso)."""
    if isinstance(precisas, dict) or precisas is None:
        precisas = [precisas] if precisas else []
    if not ft:
        return (precisas[0] if precisas else None), None
    validas, avisos = [], []
    for precisa in precisas:
        dif = abs(precisa["nav"] - ft["nav"]) / ft["nav"]
        if precisa["fecha"] == ft["fecha"]:
            if dif <= TOLERANCIA_MISMO_DIA:
                validas.append(precisa)
            else:
                avisos.append(f"{precisa['fuente']} da {precisa['nav']} y FT {ft['nav']} el {ft['fecha']}: "
                              "no cuadran")
        elif precisa["fecha"] > ft["fecha"]:
            if dif <= TOLERANCIA_OTRO_DIA:
                validas.append(precisa)
            else:
                avisos.append(f"{precisa['fuente']} da {precisa['nav']} ({precisa['fecha']}), muy lejos de FT "
                              f"{ft['nav']} ({ft['fecha']})")
    aviso = "; ".join(avisos) or None
    if not validas:
        return ft, aviso
    mejor = max(validas, key=lambda p: p["fecha"])  # max devuelve el primero en caso de empate
    return mejor, aviso


def ultimo_precio(sesion: requests.Session, fondo: dict) -> tuple[dict | None, list[str]]:
    errores, obtenidos = [], {}
    for nombre, f in FUENTES:
        try:
            p = f(sesion, fondo["isin"], fondo.get("moneda", "EUR"))
            if p["nav"] <= 0:
                raise SinPrecio(f"precio no válido {p['nav']}")
            obtenidos[nombre] = p
            print(f"  {fondo['isin']} {nombre}: {p['fecha']} {p['nav']}")
        except Exception as e:  # cualquier fallo de una fuente: se usa la otra
            errores.append(f"{nombre}: {e}")
    precisas = [obtenidos[n] for n, _ in FUENTES if n != "ft" and n in obtenidos]
    precio, aviso = elegir(obtenidos.get("ft"), precisas)
    if aviso:
        errores.append(aviso)
    return precio, errores


# ---------------------------------------------------------------- historial

def anadir(historial: list, fecha: str, nav: float) -> bool:
    """Añade o corrige el punto de esa fecha. Devuelve True si cambió algo."""
    for punto in historial:
        if punto[0] == fecha:
            if punto[1] == nav:
                return False
            punto[1] = nav
            return True
    historial.append([fecha, nav])
    historial.sort(key=lambda p: p[0])
    del historial[:-MAX_PUNTOS]
    return True


def actualizar(datos: dict, fondos: list, obtener) -> tuple[bool, list[str]]:
    cambiado, avisos, avisos_fuente = False, [], []
    actuales = datos.setdefault("fondos", {})
    for fondo in fondos:
        isin = fondo["isin"]
        reg = actuales.setdefault(isin, {"historial": []})
        reg["nombre"] = fondo["nombre"]
        precio, errores = obtener(fondo)
        if precio is None:
            avisos.append(f"{isin} {fondo['nombre']}: sin precio ({'; '.join(errores)})")
            reg["error"] = "; ".join(errores)[:300]
            continue
        reg.pop("error", None)
        if errores:
            avisos_fuente.append(f"{isin}: {'; '.join(errores)}")
        reg["fuente"] = precio["fuente"]
        if precio.get("moneda"):
            reg["moneda"] = precio["moneda"]
        if anadir(reg["historial"], precio["fecha"], precio["nav"]):
            cambiado = True
        print(f"{isin} {precio['fecha']} {precio['nav']} ({precio['fuente']})")
    for a in avisos_fuente:
        print("Nota", a)
    return cambiado, avisos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probar", metavar="ISIN")
    args = ap.parse_args()

    sesion = requests.Session()
    sesion.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"})

    if args.probar:
        for nombre, f in FUENTES:
            try:
                print(nombre, f(sesion, args.probar, "EUR"))
            except Exception as e:
                print(nombre, "ERROR", e)
        return 0

    fondos = json.loads(FONDOS.read_text(encoding="utf-8"))["fondos"]
    datos = json.loads(PRECIOS.read_text(encoding="utf-8")) if PRECIOS.exists() else {}

    def obtener(fondo):
        precio, errores = ultimo_precio(sesion, fondo)
        time.sleep(1.5)
        return precio, errores

    cambiado, avisos = actualizar(datos, fondos, obtener)
    # Solo se reescribe si hay algún precio nuevo, para no hacer un commit en cada consulta.
    if cambiado or not PRECIOS.exists():
        datos["actualizado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        PRECIOS.write_text(json.dumps(datos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for a in avisos:
        print("AVISO", a, file=sys.stderr)
    print("Cambios en precios:", "sí" if cambiado else "no")
    # Falla solo si no se consiguió ningún precio: así se nota en Actions si las fuentes se rompen.
    return 1 if avisos and len(avisos) == len(fondos) else 0


if __name__ == "__main__":
    sys.exit(main())
