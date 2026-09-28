"""Genera la lista fija de las N acciones de mayor capitalización cotizadas en EE. UU.

Se ejecuta a mano (o con el workflow "Generar lista de acciones") y guarda universo_fijo.json, que es
la lista que vigila alertas_bolsa.py. No se recalcula en cada ejecución diaria.

Método:
1. Del buscador de acciones de nasdaq.com (no es una API oficial) se obtienen todas las acciones de
   NYSE, Nasdaq y NYSE American con su capitalización y su último precio.
2. Número de acciones = capitalización / último precio.
3. Capitalización al cierre de la fecha de referencia = número de acciones × cierre de ese día
   (Yahoo Finance, sin ajustar). Se ordena y se toman las N mayores.

    python universo.py                      # última sesión cerrada, 500 acciones
    python universo.py --fecha 2026-09-25   # cierre de un día concreto
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

AQUI = Path(__file__).resolve().parent
FICHERO = AQUI / "universo_fijo.json"
URL_NASDAQ = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true"
CABECERAS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Sufijos del nombre que distinguen clases de acciones de la misma empresa (GOOGL/GOOG, FOXA/FOX…).
_SUFIJOS = re.compile(
    r"\s*(,|\(|\bclass\b|\bseries\b|\bcommon\b|\bcapital stock\b|\bordinary\b|\bamerican depositary\b"
    r"|\bdepositary\b|\bsubordinate\b|\bvoting\b|\bshares\b).*$",
    re.IGNORECASE,
)


def _a_numero(valor: Any) -> float:
    try:
        return float(str(valor).replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def empresa(nombre: str) -> str:
    """Nombre de la empresa sin la clase de acción, para no contar dos veces GOOGL y GOOG."""
    return _SUFIJOS.sub("", nombre).strip().lower()


def candidatas(filas: list[dict[str, Any]], solo_eeuu: bool = False) -> list[dict[str, Any]]:
    """Acciones ordenadas por capitalización actual, una por empresa.

    - Quita preferentes, warrants y similares (símbolos con ^, espacios o más de un /).
    - Una sola clase de acción por empresa: la de mayor capitalización.
    - `solo_eeuu`: excluye empresas extranjeras que cotizan en EE. UU. (ADR como TSM o ASML).
    """
    lista = []
    for f in filas:
        simbolo = str(f.get("symbol", "")).strip().upper()
        cap, precio = _a_numero(f.get("marketCap")), _a_numero(f.get("lastsale"))
        if not simbolo or cap <= 0 or precio <= 0 or "^" in simbolo or " " in simbolo or simbolo.count("/") > 1:
            continue
        pais = str(f.get("country", "")).strip()
        if solo_eeuu and pais not in ("United States", ""):
            continue
        lista.append({"ticker": simbolo.replace("/", "-"), "nombre": str(f.get("name", simbolo)).strip(),
                      "pais": pais, "cap_actual": cap, "acciones": cap / precio})
    lista.sort(key=lambda c: c["cap_actual"], reverse=True)
    vistas, salida = set(), []
    for c in lista:
        e = empresa(c["nombre"])
        if e not in vistas:
            vistas.add(e)
            salida.append(c)
    return salida


def descargar_nasdaq(timeout: int = 60) -> list[dict[str, Any]]:
    r = requests.get(URL_NASDAQ, headers=CABECERAS, timeout=timeout)
    r.raise_for_status()
    datos = r.json().get("data") or {}
    filas = datos.get("rows") or (datos.get("table") or {}).get("rows") or []
    if not filas:
        raise ValueError("nasdaq.com no ha devuelto filas")
    return filas


def ultima_sesion_cerrada(ahora: datetime | None = None) -> date:
    """Último día laborable antes de hoy (hora de Nueva York). No tiene en cuenta festivos: si ese día
    fue festivo, cierres_del_dia no encontrará datos y conviene pasar --fecha."""
    dia = (ahora or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    while dia.weekday() >= 5:
        dia -= timedelta(days=1)
    return dia


def cierres_del_dia(tickers: list[str], fecha: date, tanda: int = 100) -> dict[str, float]:
    """Cierre sin ajustar de cada ticker en `fecha` (Yahoo Finance). Los que no tienen dato no aparecen."""
    import yfinance as yf

    salida: dict[str, float] = {}
    for i in range(0, len(tickers), tanda):
        if i:
            time.sleep(2)
        parte = tickers[i:i + tanda]
        bruto = yf.download(parte, start=fecha - timedelta(days=7), end=fecha + timedelta(days=1), interval="1d",
                            auto_adjust=False, progress=False, group_by="ticker", threads=True)
        for t in parte:
            try:
                serie = (bruto[t] if isinstance(bruto.columns, pd.MultiIndex) else bruto)["Close"]
            except KeyError:
                continue
            fila = serie[serie.index.date == fecha].dropna()
            if not fila.empty:
                salida[t] = float(fila.iloc[0])
    return salida


def ranking(cands: list[dict[str, Any]], cierres: dict[str, float], cantidad: int) -> list[dict[str, Any]]:
    """Capitalización al cierre = acciones × cierre. Las que no tienen cierre de ese día se descartan."""
    con_cierre = [{**c, "cierre": cierres[c["ticker"]], "capitalizacion": c["acciones"] * cierres[c["ticker"]]}
                  for c in cands if c["ticker"] in cierres]
    con_cierre.sort(key=lambda c: c["capitalizacion"], reverse=True)
    return con_cierre[:cantidad]


def generar(cantidad: int, fecha: date, solo_eeuu: bool, margen: float = 1.5) -> dict[str, Any]:
    cands = candidatas(descargar_nasdaq(), solo_eeuu)
    print(f"nasdaq.com: {len(cands)} acciones (una por empresa)")
    # Solo se consultan las mayores por capitalización actual: con un margen del 50 % sobra para que
    # los movimientos de precio desde la fecha de referencia no cambien quién entra en las N primeras.
    cands = cands[:int(cantidad * margen)]
    cierres = cierres_del_dia([c["ticker"] for c in cands], fecha)
    faltan = [c["ticker"] for c in cands if c["ticker"] not in cierres]
    print(f"Yahoo: cierre del {fecha} para {len(cierres)} de {len(cands)}. Sin dato: {', '.join(faltan) or '-'}")
    top = ranking(cands, cierres, cantidad)
    if len(top) < cantidad:
        raise SystemExit(f"Solo {len(top)} acciones con cierre del {fecha}: ¿fue festivo? Prueba con --fecha.")
    return {
        "fecha_referencia": fecha.isoformat(),
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "metodo": "acciones en circulación (capitalización / último precio de nasdaq.com) × cierre sin ajustar "
                  "de Yahoo Finance en fecha_referencia",
        "solo_eeuu": solo_eeuu,
        "sin_cierre": faltan,
        "acciones": [{"posicion": i + 1, "ticker": c["ticker"], "nombre": c["nombre"], "pais": c["pais"],
                      "cierre": round(c["cierre"], 4), "capitalizacion": round(c["capitalizacion"])}
                     for i, c in enumerate(top)],
    }


def cargar(path: Path = FICHERO) -> tuple[list[str], str]:
    """Tickers de la lista fija y su fecha de referencia."""
    d = json.loads(path.read_text(encoding="utf-8"))
    return [a["ticker"] for a in d["acciones"]], d["fecha_referencia"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fecha", type=date.fromisoformat, help="fecha de cierre AAAA-MM-DD (por defecto la última sesión)")
    p.add_argument("--cantidad", type=int, default=500)
    p.add_argument("--solo-eeuu", action="store_true", help="excluir empresas extranjeras (ADR)")
    p.add_argument("--salida", type=Path, default=FICHERO)
    a = p.parse_args()
    fecha = a.fecha or ultima_sesion_cerrada()
    d = generar(a.cantidad, fecha, a.solo_eeuu)
    a.salida.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Guardadas {len(d['acciones'])} acciones al cierre del {fecha} en {a.salida.name}")
    for x in d["acciones"][:10] + d["acciones"][-3:]:
        print(f"  {x['posicion']:>3}. {x['ticker']:<6} {x['capitalizacion'] / 1e9:>9,.1f} mil M$  {x['nombre']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
