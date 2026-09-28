"""Backtest de las señales BUY de estrategia_score.py sobre la lista fija de acciones.

Para cada BUY mide cuánto había subido o bajado la acción N sesiones después, comparado con el
S&P 500 (fondo SPY) en las mismas fechas. Las señales solo usan datos anteriores a cada día, así que
calcularlas con todo el histórico da lo mismo que calcularlas en su momento.

Entrada: apertura de la sesión siguiente a la señal (el aviso llega tras el cierre).
Salida: cierre N sesiones después de la señal.

    python backtest.py --desde 2026-09-01 --hasta 2026-09-12 --fin 2026-09-25

Limitación: la lista de acciones es la de las 500 mayores al 25/09/2026. Las empresas que cayeron
mucho y salieron de la lista no están (sesgo de supervivencia), lo que favorece el resultado.
"""

from __future__ import annotations

import argparse
from datetime import date

import numpy as np
import pandas as pd

import alertas_bolsa as ab
import universo

HORIZONTES = (1, 5, 10, 20)


def operaciones(datos: dict[str, pd.DataFrame], ref: pd.DataFrame, fin: date) -> pd.DataFrame:
    """Una fila por cada BUY, con la rentabilidad a cada horizonte (NaN si aún no ha pasado)."""
    ref = ref[ref.index.date <= fin]
    filas = []
    for t, df in datos.items():
        df = df[df.index.date <= fin]
        if len(df) < 220:
            continue
        res = ab.senales(df)
        for f in res.index[res["Signal"] == "BUY"]:
            i = df.index.get_loc(f)
            if i + 1 >= len(df):
                continue  # señal del último día: aún no se puede entrar
            entrada = df["Open"].iloc[i + 1]
            fila = {"ticker": t, "fecha": f.date(), "score": res["Score"].iloc[i], "entrada": entrada}
            r_ini = ref.index.searchsorted(df.index[i + 1])
            for h in HORIZONTES + ("fin",):
                j = len(df) - 1 if h == "fin" else i + h
                if j >= len(df) or j <= i:
                    fila[f"r{h}"] = fila[f"spy{h}"] = np.nan
                    continue
                fila[f"r{h}"] = (df["Close"].iloc[j] / entrada - 1) * 100
                r_fin = ref.index.searchsorted(df.index[j])
                if r_ini < len(ref) and r_fin < len(ref):
                    fila[f"spy{h}"] = (ref["Close"].iloc[r_fin] / ref["Open"].iloc[r_ini] - 1) * 100
                else:
                    fila[f"spy{h}"] = np.nan
            filas.append(fila)
    return pd.DataFrame(filas)


def resumen(ops: pd.DataFrame, h: str) -> str:
    r, s = ops[f"r{h}"].dropna(), ops[f"spy{h}"]
    if r.empty:
        return f"{h:>4}: sin datos"
    s = s[r.index]
    return (f"{h:>4}: n={len(r):>4} · suben {100 * (r > 0).mean():5.1f}% · media {r.mean():+6.2f}% · "
            f"mediana {r.median():+6.2f}% · SPY media {s.mean():+6.2f}% · baten al SPY {100 * (r > s).mean():5.1f}%")


def base(datos: dict[str, pd.DataFrame], fin: date, desde: date, hasta: date, h: int) -> str:
    """Referencia: todas las acciones, todos los días de la ventana, sin ninguna señal."""
    rs = []
    for df in datos.values():
        df = df[df.index.date <= fin]
        c = df["Close"]
        fut = (c.shift(-h) / df["Open"].shift(-1) - 1) * 100
        m = (df.index.date >= desde) & (df.index.date <= hasta)
        rs.append(fut[m].dropna())
    r = pd.concat(rs) if rs else pd.Series(dtype=float)
    return (f"{h:>4}: n={len(r):>6} · suben {100 * (r > 0).mean():5.1f}% · media {r.mean():+6.2f}% · "
            f"mediana {r.median():+6.2f}%")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", type=date.fromisoformat, required=True, help="primer día de señales a analizar")
    p.add_argument("--hasta", type=date.fromisoformat, required=True, help="último día de señales a analizar")
    p.add_argument("--fin", type=date.fromisoformat, required=True, help="último cierre con datos (p. ej. 2026-09-25)")
    p.add_argument("--anual-desde", type=date.fromisoformat, help="además, resumen de todos los BUY desde esta fecha")
    p.add_argument("--historico", default="3y")
    a = p.parse_args()

    tickers, fecha_lista = universo.cargar()
    datos = ab.descargar(tickers + ["SPY"], a.historico)
    ref = datos.pop("SPY")
    print(f"Lista de {len(tickers)} acciones ({fecha_lista}); con datos: {len(datos)}. Referencia: SPY.")
    todas = operaciones(datos, ref, a.fin)
    pd.set_option("display.width", 200)

    ops = todas[(todas["fecha"] >= a.desde) & (todas["fecha"] <= a.hasta)].sort_values(["fecha", "ticker"])
    print(f"\n=== BUY entre {a.desde} y {a.hasta}: {len(ops)} señales ===")
    print("Rentabilidad desde la apertura del día siguiente hasta el cierre N sesiones después (%):")
    for f, g in ops.groupby("fecha"):
        print(f"\n{f} · {len(g)} BUY")
        for _, x in g.iterrows():
            vals = "  ".join(f"{h}s {x[f'r{h}']:+6.2f}" if not np.isnan(x[f"r{h}"]) else f"{h}s    -  "
                             for h in HORIZONTES)
            print(f"   {x['ticker']:<6} score {x['score']:>5g}  {vals}  hasta {a.fin}: {x['rfin']:+6.2f} "
                  f"(SPY {x['spyfin']:+5.2f})")
        print("   " + resumen(g, "5").strip())
        print("   " + resumen(g, "fin").strip())

    print(f"\n--- Resumen de la ventana ({len(ops)} BUY) ---")
    for h in HORIZONTES + ("fin",):
        print(resumen(ops, str(h)))
    print(f"\nReferencia sin señal (todas las acciones, todos los días de la ventana):")
    for h in (1, 5, 10):
        print(base(datos, a.fin, a.desde, a.hasta, h))

    if a.anual_desde:
        anual = todas[todas["fecha"] >= a.anual_desde]
        print(f"\n=== Todos los BUY desde {a.anual_desde}: {len(anual)} señales ===")
        for h in HORIZONTES:
            print(resumen(anual, str(h)))
        print(f"\nReferencia sin señal en el mismo periodo:")
        for h in (5, 10, 20):
            print(base(datos, a.fin, a.anual_desde, a.fin, h))
        print("\nPor mes (a 10 sesiones):")
        anual = anual.assign(mes=pd.to_datetime(anual["fecha"]).dt.strftime("%Y-%m"))
        for m, g in anual.groupby("mes"):
            r = g["r10"].dropna()
            if len(r):
                print(f"  {m}: n={len(r):>3} · suben {100 * (r > 0).mean():5.1f}% · media {r.mean():+6.2f}% · "
                      f"SPY {g.loc[r.index, 'spy10'].mean():+6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
