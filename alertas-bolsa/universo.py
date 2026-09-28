"""Lista de acciones a vigilar: las N de mayor capitalización cotizadas en EE. UU.

Fuente principal: el buscador de acciones de nasdaq.com (NYSE, Nasdaq y NYSE American, con su
capitalización). No es una API oficial documentada. Si falla, se usa la última lista buena guardada
y, si no hay, la lista del S&P 500 incluida en el repositorio.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import requests

AQUI = Path(__file__).resolve().parent
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


def seleccionar(filas: list[dict[str, Any]], cantidad: int, solo_eeuu: bool = False) -> list[str]:
    """Ordena por capitalización y devuelve los símbolos (formato Yahoo) de las `cantidad` mayores.

    - Quita preferentes, warrants y similares (símbolos con ^, espacios o más de un /).
    - Una sola clase de acción por empresa: la de mayor capitalización.
    - `solo_eeuu`: excluye empresas extranjeras que cotizan en EE. UU. (ADR como TSM o ASML).
    """
    candidatas = []
    for f in filas:
        simbolo = str(f.get("symbol", "")).strip().upper()
        cap = _a_numero(f.get("marketCap"))
        if not simbolo or cap <= 0 or "^" in simbolo or " " in simbolo or simbolo.count("/") > 1:
            continue
        if solo_eeuu and str(f.get("country", "")).strip() not in ("United States", ""):
            continue
        candidatas.append((cap, simbolo.replace("/", "-"), empresa(str(f.get("name", simbolo)))))
    candidatas.sort(reverse=True)
    vistas, salida = set(), []
    for _, simbolo, emp in candidatas:
        if emp in vistas:
            continue
        vistas.add(emp)
        salida.append(simbolo)
        if len(salida) == cantidad:
            break
    return salida


def descargar_nasdaq(timeout: int = 60) -> list[dict[str, Any]]:
    r = requests.get(URL_NASDAQ, headers=CABECERAS, timeout=timeout)
    r.raise_for_status()
    datos = r.json().get("data") or {}
    filas = datos.get("rows") or (datos.get("table") or {}).get("rows") or []
    if not filas:
        raise ValueError("nasdaq.com no ha devuelto filas")
    return filas


def obtener(cantidad: int, solo_eeuu: bool, cache: Path, respaldo: Path) -> tuple[list[str], str]:
    """Devuelve (tickers, descripción de la fuente usada)."""
    try:
        tickers = seleccionar(descargar_nasdaq(), cantidad, solo_eeuu)
        if len(tickers) < cantidad * 0.9:
            raise ValueError(f"solo {len(tickers)} acciones con capitalización")
        cache.write_text(json.dumps({"fecha": date.today().isoformat(), "tickers": tickers}, indent=0),
                         encoding="utf-8")
        return tickers, f"nasdaq.com, {len(tickers)} mayores por capitalización"
    except (requests.RequestException, ValueError) as e:
        print(f"⚠️ No se ha podido obtener la lista de nasdaq.com: {e}")
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        return d["tickers"][:cantidad], f"lista guardada del {d['fecha']} (nasdaq.com falló)"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    d = json.loads(respaldo.read_text(encoding="utf-8"))
    return d["tickers"], "S&P 500 de respaldo (nasdaq.com falló y no hay lista guardada)"
