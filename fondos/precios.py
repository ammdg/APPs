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


FUENTES = [("ft", de_ft), ("quefondos", de_quefondos)]


def ultimo_precio(sesion: requests.Session, fondo: dict) -> tuple[dict | None, list[str]]:
    errores = []
    for nombre, f in FUENTES:
        try:
            p = f(sesion, fondo["isin"], fondo.get("moneda", "EUR"))
            if p["nav"] <= 0:
                raise SinPrecio(f"{nombre}: precio no válido {p['nav']}")
            return p, errores
        except Exception as e:  # cualquier fallo de una fuente: se prueba la siguiente
            errores.append(f"{nombre}: {e}")
    return None, errores


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
    cambiado, avisos = False, []
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
        reg["fuente"] = precio["fuente"]
        if precio.get("moneda"):
            reg["moneda"] = precio["moneda"]
        if anadir(reg["historial"], precio["fecha"], precio["nav"]):
            cambiado = True
        print(f"{isin} {precio['fecha']} {precio['nav']} ({precio['fuente']})")
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
