"""Qué hace la acción en las 30 sesiones siguientes a cada BUY de estrategia_score.py.

Entrada: apertura de la sesión siguiente a la señal. Para cada señal con 30 sesiones de datos después:
- Rentabilidad al cierre de 5, 10, 15, 20, 25 y 30 sesiones.
- Si llegó a subir alguna vez (algún cierre, o algún máximo del día, por encima de la entrada).
- Máxima subida y máxima bajada en esas 30 sesiones, y en qué sesión se alcanzó la máxima subida.

Se compara con la referencia "cualquier acción de la lista, cualquier día" (una de cada 5 sesiones).
Limitación: la lista es la de las 500 mayores al 25/09/2026 (sesgo de supervivencia).

    python trayectoria.py --fin 2026-09-25 --corte 2025-01-01
"""

from __future__ import annotations

import argparse
from datetime import date

import numpy as np
import pandas as pd

import alertas_bolsa as ab
import estrategia_score as es
import universo
import variantes

PLAZOS = (5, 10, 15, 20, 25, 30)
N = max(PLAZOS)
UMBRALES = (2, 5, 10)


def medir(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, i: int) -> dict[str, float]:
    """Métricas de una entrada en la apertura de i+1, siguiendo hasta el cierre de i+N."""
    entrada = o[i + 1]
    cierres = c[i + 1:i + N + 1] / entrada - 1
    maximos = h[i + 1:i + N + 1] / entrada - 1
    minimos = lo[i + 1:i + N + 1] / entrada - 1
    fila = {f"r{p}": cierres[p - 1] * 100 for p in PLAZOS}
    fila["max_cierre"] = cierres.max() * 100
    fila["max_intradia"] = maximos.max() * 100
    fila["min_intradia"] = minimos.min() * 100
    fila["sesion_max"] = int(cierres.argmax()) + 1
    for u in UMBRALES:
        llega = np.flatnonzero(cierres * 100 >= u)
        fila[f"llega{u}"] = float(len(llega) > 0)
        fila[f"sesion{u}"] = float(llega[0] + 1) if len(llega) else np.nan
    return fila


def informe(df: pd.DataFrame, titulo: str) -> None:
    n = len(df)
    print(f"\n### {titulo}: {n} casos")
    if not n:
        return
    print(f"  Nunca cerró por encima de la entrada en {N} sesiones: {100 * (df['max_cierre'] <= 0).mean():5.1f}%")
    print(f"  Nunca superó la entrada ni siquiera intradía:        {100 * (df['max_intradia'] <= 0).mean():5.1f}%")
    print(f"  {'sesiones':>9} {'suben':>7} {'media':>8} {'mediana':>8} {'media de las que suben':>23} "
          f"{'media de las que bajan':>23}")
    for p in PLAZOS:
        r = df[f"r{p}"]
        print(f"  {p:>9} {100 * (r > 0).mean():6.1f}% {r.mean():+7.2f}% {r.median():+7.2f}% "
              f"{r[r > 0].mean():+22.2f}% {r[r <= 0].mean():+22.2f}%")
    print(f"  Mejor cierre en las {N} sesiones: media {df['max_cierre'].mean():+.2f}% · mediana "
          f"{df['max_cierre'].median():+.2f}% · sesión media del mejor cierre {df['sesion_max'].mean():.1f}")
    print(f"  Peor mínimo intradía en las {N} sesiones: media {df['min_intradia'].mean():+.2f}% · mediana "
          f"{df['min_intradia'].median():+.2f}%")
    for u in UMBRALES:
        s = df[f"sesion{u}"].dropna()
        print(f"  Llegó a cerrar al menos +{u}% alguna vez: {100 * df[f'llega{u}'].mean():5.1f}%"
              + (f" · en la sesión {s.median():.0f} (mediana)" if len(s) else ""))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fin", type=date.fromisoformat, required=True, help="último cierre con datos")
    p.add_argument("--corte", type=date.fromisoformat, help="separar además el periodo antes/después de esta fecha")
    p.add_argument("--desde", type=date.fromisoformat, help="solo señales desde esta fecha")
    p.add_argument("--historico", default="5y")
    a = p.parse_args()

    tickers, _ = universo.cargar()
    datos = ab.descargar(tickers + ["SPY"], a.historico)
    spy = datos.pop("SPY")
    spy_ok = spy["Close"] > spy["Close"].rolling(200).mean()
    senales, base = [], []
    for t, df in datos.items():
        df = df[df.index.date <= a.fin]
        if len(df) < 220 + N:
            continue
        r = variantes.rasgos(df)
        buy = variantes.condicion_buy(r, variantes.VARIANTES[0], spy_ok.reindex(df.index).fillna(False))
        sig = variantes.maquina(r["tendencia"].to_numpy(), r["score"].to_numpy(), buy, es.SCORE_WATCH)
        o, h, lo, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
        fechas = df.index.date
        ultimo = len(df) - N - 1  # hace falta llegar al cierre de i+N
        for i in np.flatnonzero(sig == 2):
            if i <= ultimo:
                senales.append({"ticker": t, "fecha": fechas[i], **medir(o, h, lo, c, i)})
        for i in range(220, ultimo + 1, 5):
            base.append({"fecha": fechas[i], **medir(o, h, lo, c, i)})
    s, b = pd.DataFrame(senales), pd.DataFrame(base)
    if a.desde:
        s, b = s[s["fecha"] >= a.desde], b[b["fecha"] >= a.desde]
    print(f"Señales BUY con {N} sesiones de datos posteriores: {len(s)} "
          f"({s['fecha'].min()} a {s['fecha'].max()})")
    informe(s, "BUY (todas)")
    informe(b, "Referencia: cualquier acción, cualquier día")
    if a.corte:
        informe(s[s["fecha"] < a.corte], f"BUY antes de {a.corte}")
        informe(s[s["fecha"] >= a.corte], f"BUY desde {a.corte}")
        informe(b[b["fecha"] >= a.corte], f"Referencia desde {a.corte}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
