"""Exploración puntual de todos los aeropuertos del mundo (aeropuertos_mundo.json, sacados de OurAirports).

Se lanza a mano desde GitHub Actions (workflow "Explorar aeropuertos del mundo") repartida en varias partes
que se ejecutan a la vez. Usa la configuración de config.json (fechas, escalas, precio máximo) y la misma
consulta a Google Flights que la búsqueda diaria.

    python explorar.py ida --parte 3 --de 20     # idas de 1/20 de los aeropuertos -> ida_3.json
    python explorar.py vuelta --parte 3 --de 10  # vueltas de los candidatos (ida < límite) -> vuelta_3.json
    python explorar.py informe                   # junta todo, escribe exploracion.json/.md y avisa
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import random
import sys
import time
from datetime import date, datetime
from pathlib import Path

import vuelos_baratos as vb

AQUI = Path(__file__).resolve().parent
AEROPUERTOS = AQUI / "aeropuertos_mundo.json"
MARGEN_LISTA = 250  # en el informe se guardan los destinos por debajo de esto, para la búsqueda diaria


# --------------------------------------------------------------------------- (de)serialización


def _vuelo_a_dict(v: vb.Vuelo | None) -> dict | None:
    if v is None:
        return None
    d = dataclasses.asdict(v)
    d["fecha"] = v.fecha.isoformat()
    return d


def _vuelo_de_dict(d: dict | None) -> vb.Vuelo | None:
    if d is None:
        return None
    return vb.Vuelo(**{**d, "fecha": date.fromisoformat(d["fecha"])})


def cargar_aeropuertos() -> dict[str, dict]:
    return json.loads(AEROPUERTOS.read_text(encoding="utf-8"))


def parte_de(codigos: list[str], parte: int, de: int) -> list[str]:
    # Reparto alterno: cada parte recibe aeropuertos cercanos y lejanos, así tardan parecido.
    return codigos[parte::de]


# --------------------------------------------------------------------------- consultas


def _km(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def consultar_lote(cfg: vb.Config, pares: list[tuple[str, str]], fechas: list[date], buscador=None) -> dict:
    """Consulta cada (origen, destino) en todas las fechas y guarda el directo y el de escala más baratos.

    Devuelve {"resultados": {destino: {...}}, "sin_consultar": [...], "errores": n}. Si Google falla
    vb.MAX_ERRORES_SEGUIDOS veces seguidas, se para y el resto queda en "sin_consultar".
    """
    buscador = buscador or vb.buscar_opciones
    resultados: dict[str, dict] = {}
    sin_consultar: list[str] = []
    errores = seguidos = 0
    for i, (o, d) in enumerate(pares):
        clave = d if o == cfg.origen else o
        if seguidos >= vb.MAX_ERRORES_SEGUIDOS:
            sin_consultar += [x if a == cfg.origen else a for a, x in pares[i:]]
            print(f"Interrumpido tras {seguidos} errores seguidos; quedan {len(pares) - i} sin consultar.")
            break
        directos, escalas, fallos = [], [], 0
        for f in fechas:
            try:
                op = buscador(o, d, f, cfg)
                directos.append(op.directo)
                escalas.append(op.escala)
                seguidos = 0
            except Exception as e:  # noqa: BLE001
                errores += 1
                seguidos += 1
                fallos += 1
                print(f"ERROR {o}→{d} {f}: {e!r}", file=sys.stderr)
            finally:
                time.sleep(cfg.pausa_segundos * random.uniform(0.7, 1.3))
        if fallos == len(fechas):
            sin_consultar.append(clave)
            continue
        directo, escala = vb._mas_barato(*directos), vb._mas_barato(*escalas)
        resultados[clave] = {"directo": _vuelo_a_dict(directo), "escala": _vuelo_a_dict(escala)}
        mejor = vb._mas_barato(directo, escala)
        print(f"{i + 1}/{len(pares)} {o}→{d}: " + (f"{mejor.precio:.0f} €" if mejor else "—"))
    return {"resultados": resultados, "sin_consultar": sin_consultar, "errores": errores}


def leer_partes(patron: str) -> dict:
    total = {"resultados": {}, "sin_consultar": [], "errores": 0}
    for f in sorted(glob.glob(patron)):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        total["resultados"].update(d["resultados"])
        total["sin_consultar"] += d["sin_consultar"]
        total["errores"] += d["errores"]
    return total


def candidatos(cfg: vb.Config, idas: dict) -> list[str]:
    """Destinos cuya ida más barata (directa o con escala) ya cabe en el presupuesto."""
    out = []
    for codigo, r in idas["resultados"].items():
        mejor = vb._mas_barato(_vuelo_de_dict(r["directo"]), _vuelo_de_dict(r["escala"]))
        if mejor and mejor.precio < cfg.precio_max_persona:
            out.append(codigo)
    return sorted(out)


# --------------------------------------------------------------------------- informe


def construir_resultados(idas: dict, vueltas: dict, aeropuertos: dict) -> list[vb.Resultado]:
    res = []
    for codigo, ida in idas["resultados"].items():
        vuelta = vueltas["resultados"].get(codigo, {"directo": None, "escala": None})
        info = aeropuertos.get(codigo, {})
        nombre = f"{info.get('nombre', codigo)}, {info.get('pais', '?')}"
        res.append(vb.Resultado(
            codigo, nombre,
            ida_directo=_vuelo_de_dict(ida["directo"]), ida_escala=_vuelo_de_dict(ida["escala"]),
            vuelta_directo=_vuelo_de_dict(vuelta["directo"]), vuelta_escala=_vuelo_de_dict(vuelta["escala"]),
        ))
    return res


def _linea(r: vb.Resultado, c: vb.Combinacion, aeropuertos: dict, pasajeros: int) -> str:
    km = aeropuertos.get(r.destino, {}).get("km", 0)
    tipo = "directo" if not c.con_escala else "escala " + "/".join(
        v.escala for v in (c.ida, c.vuelta) if v.escala)
    return (f"• {r.nombre} ({r.destino}) {vb._euros(c.precio)}/pers · {vb._euros(c.precio * pasajeros)} total"
            f" · {_km(km)} km · {tipo}")


def informe(cfg: vb.Config, idas: dict, vueltas: dict, aeropuertos: dict) -> tuple[list[str], dict]:
    resultados = construir_resultados(idas, vueltas, aeropuertos)
    combos = sorted(((r, r.mejor) for r in resultados if r.mejor), key=lambda x: x[1].precio)
    baratos = [(r, c) for r, c in combos if c.precio < cfg.precio_max_persona]
    margen = [(r, c) for r, c in combos if c.precio < MARGEN_LISTA]
    con_ida = sum(1 for r in resultados if r.ida)
    sin = idas["sin_consultar"]

    # Distancia: cuántos baratos hay por tramos y el más lejano.
    tramos = [(0, 1000), (1000, 2000), (2000, 3000), (3000, 4000), (4000, 6000), (6000, 25000)]
    por_tramo = []
    for a, b in tramos:
        n = sum(1 for r, _ in baratos if a <= aeropuertos.get(r.destino, {}).get("km", 0) < b)
        total = sum(1 for k, v in aeropuertos.items() if a <= v["km"] < b)
        por_tramo.append(f"{a // 1000}-{b // 1000} mil km: {n} de {total}")
    lejano = max(baratos, key=lambda x: aeropuertos.get(x[0].destino, {}).get("km", 0), default=None)

    cab = [
        f"🌍 EXPLORACIÓN MUNDIAL desde {cfg.origen} · ida {'/'.join(f'{f:%d}' for f in cfg.fechas_ida)} → "
        f"vuelta {'/'.join(f'{f:%d}' for f in cfg.fechas_vuelta)} {cfg.fechas_vuelta[0]:%m/%Y}",
        f"{len(aeropuertos)} aeropuertos · {con_ida} con vuelo de ida (directo o 1 escala ≤ "
        f"{cfg.escala_max_minutos // 60}h) · {len(baratos)} por debajo de {vb._euros(cfg.precio_max_persona)}/pers",
    ]
    if sin:
        cab.append(f"⚠️ {len(sin)} aeropuertos sin consultar (Google cortó): {', '.join(sin[:30])}"
                   + ("…" if len(sin) > 30 else ""))
    if vueltas["sin_consultar"]:
        cab.append(f"⚠️ {len(vueltas['sin_consultar'])} vueltas sin consultar.")
    cab += ["", "Baratos por distancia a Madrid:"] + [f"  {t}" for t in por_tramo]
    if lejano:
        cab.append(f"  Más lejano: {lejano[0].nombre} ({lejano[0].destino}), "
                   f"{_km(aeropuertos[lejano[0].destino]['km'])} km")
    cuerpo = ["", f"✅ Por debajo de {vb._euros(cfg.precio_max_persona)}/persona ida y vuelta "
              f"({cfg.pasajeros} pers.):"]
    cuerpo += [_linea(r, c, aeropuertos, cfg.pasajeros) for r, c in baratos] or ["Ninguno."]
    cuerpo += ["", f"Guardados {len(margen)} destinos por debajo de {MARGEN_LISTA} € para la búsqueda diaria."]
    lineas = cab + cuerpo

    datos = {
        "fecha": datetime.now(vb.ZONA).isoformat(timespec="minutes"),
        "aeropuertos": len(aeropuertos), "con_ida": con_ida,
        "sin_consultar_ida": sin, "sin_consultar_vuelta": vueltas["sin_consultar"],
        "errores": idas["errores"] + vueltas["errores"],
        "destinos": [
            {"codigo": r.destino, "nombre": r.nombre, "km": aeropuertos.get(r.destino, {}).get("km"),
             "precio_persona": c.precio, "con_escala": c.con_escala,
             "ida": _vuelo_a_dict(c.ida), "vuelta": _vuelo_a_dict(c.vuelta)}
            for r, c in margen
        ],
    }
    return lineas, datos


def trocear(lineas: list[str], limite: int = vb.LIMITE_TELEGRAM) -> list[str]:
    """Parte el informe en mensajes de Telegram sin cortar líneas."""
    mensajes, actual = [], ""
    for linea in lineas:
        if actual and len(actual) + len(linea) + 1 > limite:
            mensajes.append(actual)
            actual = ""
        actual += ("\n" if actual else "") + linea
    if actual:
        mensajes.append(actual)
    return mensajes


# --------------------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fase", choices=["ida", "vuelta", "informe"])
    p.add_argument("--parte", type=int, default=0)
    p.add_argument("--de", type=int, default=1)
    p.add_argument("--config", type=Path, default=AQUI / "config.json")
    p.add_argument("--dir", type=Path, default=Path("."), help="carpeta de ida_*.json / vuelta_*.json")
    a = p.parse_args()

    cfg = vb.Config.desde_archivo(a.config)
    aeropuertos = cargar_aeropuertos()

    if a.fase == "ida":
        codigos = parte_de(list(aeropuertos), a.parte, a.de)
        print(f"Parte {a.parte + 1}/{a.de}: {len(codigos)} aeropuertos")
        out = consultar_lote(cfg, [(cfg.origen, c) for c in codigos], cfg.fechas_ida)
        (a.dir / f"ida_{a.parte}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return 0

    idas = leer_partes(str(a.dir / "ida_*.json"))
    if a.fase == "vuelta":
        codigos = parte_de(candidatos(cfg, idas), a.parte, a.de)
        print(f"Parte {a.parte + 1}/{a.de}: {len(codigos)} vueltas")
        out = consultar_lote(cfg, [(c, cfg.origen) for c in codigos], cfg.fechas_vuelta)
        (a.dir / f"vuelta_{a.parte}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return 0

    vueltas = leer_partes(str(a.dir / "vuelta_*.json"))
    lineas, datos = informe(cfg, idas, vueltas, aeropuertos)
    (a.dir / "exploracion.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.dir / "exploracion.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))
    for m in trocear(lineas):
        vb.notificar(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
