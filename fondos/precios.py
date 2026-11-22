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


# ---------------------------------------------------------------- Yahoo Finance

def yahoo_simbolo(sesion: requests.Session, isin: str) -> str:
    r = sesion.get("https://query2.finance.yahoo.com/v1/finance/search",
                   params={"q": isin, "quotesCount": 5, "newsCount": 0}, timeout=30)
    r.raise_for_status()
    # Solo las series "0P..." son el valor liquidativo del fondo (datos de Morningstar). Otros símbolos
    # (p. ej. "XXXX.F") son cotizaciones en bolsa de otro producto y no valen.
    simbolos = [q["symbol"] for q in r.json().get("quotes", []) if q.get("symbol", "").startswith("0P")]
    if not simbolos:
        raise SinPrecio("Yahoo: no tiene el valor liquidativo de este fondo")
    return simbolos[0]


def parsear_chart_yahoo(datos: dict, moneda: str) -> dict:
    res = datos["chart"]["result"][0]
    meta = res.get("meta", {})
    if meta.get("currency") and meta["currency"] != moneda:
        raise SinPrecio(f"Yahoo: moneda {meta['currency']}, no {moneda}")
    cierres = res["indicators"]["quote"][0]["close"]
    puntos = [(t, c) for t, c in zip(res["timestamp"], cierres) if c is not None]
    if not puntos:
        raise SinPrecio("Yahoo: serie vacía")
    t, nav = puntos[-1]
    # Yahoo guarda los precios en coma flotante de 32 bits (12.591 llega como 12.590999603...):
    # se redondea a 7 cifras significativas, que es la precisión real de ese formato.
    nav = float(f"{float(nav):.7g}")
    return {"fecha": datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), "nav": nav,
            "moneda": meta.get("currency"), "fuente": "yahoo"}


def de_yahoo(sesion: requests.Session, isin: str, moneda: str) -> dict:
    simbolo = yahoo_simbolo(sesion, isin)
    r = sesion.get(f"https://query2.finance.yahoo.com/v8/finance/chart/{simbolo}",
                   params={"range": "1mo", "interval": "1d"}, timeout=30)
    r.raise_for_status()
    p = parsear_chart_yahoo(r.json(), moneda)
    print(f"    (yahoo {isin} -> {simbolo})")
    return p


# ---------------------------------------------------------------- Finect

def parsear_finect(pagina: str) -> dict:
    """Precio: el de la ficha schema.org (offers.price). Fecha: la de "lastQuote" con ese mismo precio,
    dentro de los datos internos de la página (vienen como JSON escapado)."""
    precio = moneda = None
    for ld in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', pagina, flags=re.S):
        try:
            oferta = json.loads(ld).get("offers") or {}
        except (ValueError, AttributeError):
            continue
        if oferta.get("price"):
            precio, moneda = float(oferta["price"]), oferta.get("priceCurrency")
            break
    if not precio:
        raise SinPrecio("Finect: no aparece el precio")
    plano = pagina.replace('\\"', '"')
    for f, p in re.findall(r'"lastQuote":\{"datetime":"(\d{4}-\d{2}-\d{2})T[^"]*","price":([\d.]+)', plano):
        if abs(float(p) - precio) < 1e-9:
            return {"fecha": f, "nav": precio, "moneda": moneda, "fuente": "finect"}
    raise SinPrecio("Finect: no aparece la fecha del precio")


def de_finect(sesion: requests.Session, isin: str, moneda: str) -> dict:
    # Finect redirige ISIN-<cualquier texto> a la ficha del fondo.
    r = sesion.get(f"https://www.finect.com/fondos-inversion/{isin}-x", timeout=30)
    r.raise_for_status()
    if isin not in r.url:
        raise SinPrecio(f"Finect: redirige a otra página ({r.url})")
    return parsear_finect(r.text)


# Tras FT, por preferencia en caso de empate de fecha.
FUENTES = [("ft", de_ft), ("finect", de_finect), ("quefondos", de_quefondos), ("yahoo", de_yahoo)]

# FT redondea a 2 decimales; Finect, Quefondos y Yahoo (series de Morningstar) suelen dar el valor
# liquidativo completo. Se usan solo si cuadran con el de FT, para no fiarse a ciegas de una página que podría leerse mal.
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


# ---------------------------------------------------------------- histórico (carga puntual)

def serie_yahoo(sesion: requests.Session, isin: str, moneda: str, desde: date) -> list:
    simbolo = yahoo_simbolo(sesion, isin)
    inicio = int(datetime(desde.year, desde.month, desde.day, tzinfo=timezone.utc).timestamp())
    r = sesion.get(f"https://query2.finance.yahoo.com/v8/finance/chart/{simbolo}",
                   params={"period1": inicio, "period2": int(time.time()), "interval": "1d"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    if res.get("meta", {}).get("currency") not in (None, moneda):
        raise SinPrecio(f"Yahoo: moneda {res['meta']['currency']}")
    cierres = res["indicators"]["quote"][0]["close"]
    return [[datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), float(f"{float(c):.7g}")]
            for t, c in zip(res["timestamp"], cierres) if c is not None]


def parsear_historico_ft(html_tabla: str) -> list:
    """Filas del histórico de FT: fecha, apertura, máximo, mínimo, cierre, volumen."""
    puntos = []
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", html_tabla, flags=re.S):
        celdas = [texto_plano(c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", fila, flags=re.S)]
        if len(celdas) < 5:
            continue
        try:
            puntos.append([fecha_ft(celdas[0]).isoformat(), numero(celdas[4], ".")])
        except (SinPrecio, ValueError):
            continue
    return sorted(puntos)


def serie_ft(sesion: requests.Session, isin: str, moneda: str, desde: date) -> list:
    r = sesion.get("https://markets.ft.com/data/funds/tearsheet/historical",
                   params={"s": f"{isin}:{moneda}"}, timeout=30)
    r.raise_for_status()
    m = re.search(r'xid["\s:=]+"?(\d+)', html.unescape(r.text))
    if not m:
        raise SinPrecio("FT: no encuentro el identificador del fondo")
    r = sesion.get("https://markets.ft.com/data/equities/ajax/get-historical-prices",
                   params={"startDate": desde.strftime("%Y/%m/%d"),
                           "endDate": date.today().strftime("%Y/%m/%d"), "symbol": m.group(1)}, timeout=30)
    r.raise_for_status()
    puntos = parsear_historico_ft(r.json().get("html", ""))
    if not puntos:
        raise SinPrecio("FT: histórico vacío")
    return puntos


def cargar_historico(datos: dict, fondos: list, desde: date, obtener_serie) -> bool:
    """Añade los puntos desde `desde` que falten. No toca los días ya guardados."""
    cambiado = False
    for fondo in fondos:
        reg = datos.setdefault("fondos", {}).setdefault(fondo["isin"], {"historial": []})
        reg.setdefault("nombre", fondo["nombre"])
        serie, fuente = obtener_serie(fondo)
        if not serie:
            print(f"AVISO {fondo['isin']}: sin histórico", file=sys.stderr)
            continue
        guardadas = {p[0]: p[1] for p in reg["historial"]}
        nuevos = 0
        for fecha, nav in serie:
            if fecha < desde.isoformat() or nav <= 0:
                continue
            if fecha in guardadas:
                if abs(guardadas[fecha] - nav) / nav > TOLERANCIA_MISMO_DIA:
                    print(f"  ojo {fondo['isin']} {fecha}: guardado {guardadas[fecha]}, {fuente} {nav}")
                continue
            reg["historial"].append([fecha, nav])
            nuevos += 1
        reg["historial"].sort(key=lambda p: p[0])
        cambiado = cambiado or nuevos > 0
        h = [p for p in reg["historial"] if p[0] >= desde.isoformat()]
        print(f"{fondo['isin']} {fuente}: {nuevos} días nuevos; {len(h)} desde {desde} "
              f"({h[0][0]} {h[0][1]} … {h[-1][0]} {h[-1][1]})")
    return cambiado


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probar", metavar="ISIN")
    ap.add_argument("--historico-desde", metavar="AAAA-MM-DD",
                    help="carga una vez el histórico diario desde esa fecha (Yahoo; si no, FT)")
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

    if args.historico_desde:
        desde = date.fromisoformat(args.historico_desde)

        def obtener_serie(fondo):
            for nombre, f in (("yahoo", serie_yahoo), ("ft", serie_ft)):
                try:
                    return f(sesion, fondo["isin"], fondo.get("moneda", "EUR"), desde), nombre
                except Exception as e:
                    print(f"  {fondo['isin']} {nombre}: {str(e)[:150]}")
                finally:
                    time.sleep(1.5)
            return [], None

        cambiado = cargar_historico(datos, fondos, desde, obtener_serie)
        avisos = []
    else:
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
