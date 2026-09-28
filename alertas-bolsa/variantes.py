"""Compara variantes de la estrategia para tener menos señales BUY pero más fiables.

Replica de forma vectorizada los indicadores y la máquina de estados de estrategia_score.py (sin
modificar ese fichero) y añade condiciones opcionales al BUY. Antes de comparar, comprueba que la
réplica con los parámetros originales da exactamente las mismas señales que generate_signals.

Para evitar el sobreajuste, cada variante se mide en dos tramos: uno de ajuste y otro de validación.
Una variante solo es buena si mejora en los dos.

Entrada: apertura de la sesión siguiente a la señal. Salida: cierre N sesiones después.

    python variantes.py --fin 2026-09-25 --corte 2025-01-01
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

import alertas_bolsa as ab
import estrategia_score as es
import universo

HORIZONTES = (10, 20)


@dataclass(frozen=True)
class Variante:
    nombre: str
    score_buy: float = es.SCORE_BUY
    exigir_correccion: bool = False  # caída entre -5 % y -15 % desde el máximo de 60 sesiones
    exigir_volumen: bool = False  # volumen > 1,2 × media de 20 sesiones
    rsi_min10_max: float | None = None  # RSI mínimo de 10 sesiones por debajo de este valor
    filtro_mercado: bool = False  # el S&P 500 (SPY) por encima de su media de 200 sesiones


VARIANTES = [
    Variante("0 Original"),
    Variante("1 Score BUY >= 80", score_buy=80),
    Variante("2 Score BUY >= 85", score_buy=85),
    Variante("3 Exige corrección", exigir_correccion=True),
    Variante("4 Exige volumen", exigir_volumen=True),
    Variante("5 Mercado alcista (SPY > SMA200)", filtro_mercado=True),
    Variante("6 Corrección + volumen", exigir_correccion=True, exigir_volumen=True),
    Variante("7 Corrección + mercado", exigir_correccion=True, filtro_mercado=True),
    Variante("8 Corrección + RSI10 < 40", exigir_correccion=True, rsi_min10_max=40),
    Variante("9 Corrección + RSI10 < 35", exigir_correccion=True, rsi_min10_max=35),
    Variante("10 Corrección + volumen + mercado", exigir_correccion=True, exigir_volumen=True, filtro_mercado=True),
    Variante("11 Corr. + RSI10<40 + mercado", exigir_correccion=True, rsi_min10_max=40, filtro_mercado=True),
    Variante("12 Corr. + vol. + RSI10<40 + mercado", exigir_correccion=True, exigir_volumen=True,
             rsi_min10_max=40, filtro_mercado=True),
]


def rasgos(df: pd.DataFrame) -> pd.DataFrame:
    """Los mismos cálculos que calculate_indicators + calculate_score, pero para todas las velas a la vez."""
    d = es.calculate_indicators(df)
    c = d["Close"]
    rsi_prev = d["RSI"].shift(1)
    rsi_min10 = d["RSI"].rolling(10, min_periods=1).min()
    corr = (d["DRAWDOWN"] >= es.CORRECTION_MIN) & (d["DRAWDOWN"] <= es.CORRECTION_MAX)
    rec = (d["RSI"] > es.RSI_CONFIRMATION) & (rsi_prev <= es.RSI_CONFIRMATION)
    sobre20 = c > d["SMA20"]
    ruptura = c > d["MAX_PREVIOUS_3"]
    vol = d["VOLUME_RATIO"] > es.VOLUME_RATIO_MIN
    t1, t2, t3 = c > d["SMA200"], d["SMA50"] > d["SMA200"], d["SMA200"] > d["SMA200_20"]
    score = (10.0 * t1 + 10 * t2 + 10 * t3 + 20 * corr + 10 * (rsi_min10 < es.RSI_CORRECTION) + 10 * rec
             + 7.5 * sobre20 + 7.5 * ruptura + 15 * vol)
    return pd.DataFrame({"tendencia": t1 & t2 & t3, "score": score, "corr": corr, "rec": rec,
                         "precio": sobre20 & ruptura, "vol": vol, "rsi_min10": rsi_min10}, index=df.index)


def maquina(tendencia: np.ndarray, score: np.ndarray, buy: np.ndarray, score_watch: float) -> np.ndarray:
    """Máquina de estados de generate_signals. Devuelve 2 en los BUY, 1 en los WATCH, 0 en el resto."""
    out = np.zeros(len(score), dtype=np.int8)
    estado = 0  # 0 NEUTRAL, 1 WATCH_ACTIVE, 2 BUY_ACTIVE
    for i in range(len(score)):
        if i < 200:
            continue
        if not tendencia[i]:
            estado = 0
            continue
        watch = score[i] >= score_watch
        if estado == 0:
            if buy[i]:
                out[i], estado = 2, 2
            elif watch:
                out[i], estado = 1, 1
        elif estado == 1:
            if buy[i]:
                out[i], estado = 2, 2
            elif not watch:
                estado = 0
        elif not watch:
            estado = 0
    return out


def condicion_buy(r: pd.DataFrame, v: Variante, mercado_ok: pd.Series) -> np.ndarray:
    b = (r["score"] >= v.score_buy) & r["rec"] & r["precio"]
    if v.exigir_correccion:
        b &= r["corr"]
    if v.exigir_volumen:
        b &= r["vol"]
    if v.rsi_min10_max is not None:
        b &= r["rsi_min10"] < v.rsi_min10_max
    if v.filtro_mercado:
        b &= mercado_ok
    return b.to_numpy()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fin", type=date.fromisoformat, required=True, help="último cierre con datos")
    p.add_argument("--corte", type=date.fromisoformat, required=True, help="inicio del tramo de validación")
    p.add_argument("--historico", default="5y")
    a = p.parse_args()

    tickers, _ = universo.cargar()
    datos = ab.descargar(tickers + ["SPY"], a.historico)
    spy = datos.pop("SPY")
    spy = spy[spy.index.date <= a.fin]
    spy_ok = spy["Close"] > spy["Close"].rolling(200).mean()
    print(f"Acciones con datos: {len(datos)} de {len(tickers)}. SPY desde {spy.index[0].date()}.")

    # 1) Comprobar que la réplica coincide con estrategia_score.generate_signals
    distintas = 0
    for t, df in datos.items():
        df = df[df.index.date <= a.fin]
        if len(df) < 220:
            continue
        r = rasgos(df)
        rep = maquina(r["tendencia"].to_numpy(), r["score"].to_numpy(),
                      condicion_buy(r, VARIANTES[0], spy_ok.reindex(df.index).fillna(False)), es.SCORE_WATCH)
        orig = ab.senales(df)["Signal"].map({"BUY": 2, "WATCH": 1}).fillna(0).astype(int).to_numpy()
        if not np.array_equal(rep, orig):
            distintas += 1
            print(f"  ⚠️ {t}: {int((rep != orig).sum())} velas distintas")
    print(f"Réplica vs estrategia original: {distintas} acciones con diferencias (debe ser 0)\n")

    # 2) Operaciones de cada variante
    filas = []
    base = []
    for t, df in datos.items():
        df = df[df.index.date <= a.fin]
        if len(df) < 220:
            continue
        r = rasgos(df)
        ok = spy_ok.reindex(df.index).fillna(False)
        o, c = df["Open"].to_numpy(), df["Close"].to_numpy()
        so = spy["Open"].reindex(df.index).ffill().to_numpy()
        sc = spy["Close"].reindex(df.index).ffill().to_numpy()
        fechas = df.index.date
        n = len(df)
        # Referencia: cualquier acción, cualquier día (desde la vela 220)
        for i in range(220, n - 1):
            fila = {"fecha": fechas[i]}
            for h in HORIZONTES:
                fila[f"r{h}"] = (c[i + h] / o[i + 1] - 1) * 100 if i + h < n else np.nan
            base.append(fila)
        for v in VARIANTES:
            senal = maquina(r["tendencia"].to_numpy(), r["score"].to_numpy(), condicion_buy(r, v, ok), es.SCORE_WATCH)
            for i in np.flatnonzero(senal == 2):
                if i + 1 >= n:
                    continue
                fila = {"variante": v.nombre, "ticker": t, "fecha": fechas[i]}
                for h in HORIZONTES:
                    if i + h < n:
                        fila[f"r{h}"] = (c[i + h] / o[i + 1] - 1) * 100
                        fila[f"s{h}"] = (sc[i + h] / so[i + 1] - 1) * 100
                    else:
                        fila[f"r{h}"] = fila[f"s{h}"] = np.nan
                filas.append(fila)
    ops = pd.DataFrame(filas)
    base = pd.DataFrame(base)

    tramos = {"AJUSTE": (date(1900, 1, 1), a.corte), "VALIDACIÓN": (a.corte, date(2100, 1, 1))}
    for nombre, (ini, fin) in tramos.items():
        dias = int(((spy.index.date >= ini) & (spy.index.date < fin) & spy_ok.notna().to_numpy()).sum())
        b = base[(base["fecha"] >= ini) & (base["fecha"] < fin)]
        f0 = ops[(ops["fecha"] >= ini) & (ops["fecha"] < fin)]
        desde = f0["fecha"].min() if len(f0) else ini
        print(f"=== Tramo de {nombre}: señales desde {desde} hasta {min(fin, a.fin)} ===")
        print(f"Referencia sin señal: suben a 10s {100 * (b['r10'].dropna() > 0).mean():.1f}% · a 20s "
              f"{100 * (b['r20'].dropna() > 0).mean():.1f}% · media a 20s {b['r20'].mean():+.2f}%")
        print(f"{'variante':<38}{'BUY':>6}{'/día':>6}{'suben10':>9}{'suben20':>9}{'media20':>9}"
              f"{'mediana20':>10}{'SPY20':>8}{'>SPY20':>8}")
        for v in VARIANTES:
            g = f0[f0["variante"] == v.nombre]
            r10, r20 = g["r10"].dropna(), g["r20"].dropna()
            if r20.empty:
                print(f"{v.nombre:<38}{len(g):>6}   sin datos")
                continue
            s20 = g.loc[r20.index, "s20"]
            print(f"{v.nombre:<38}{len(g):>6}{len(g) / max(dias, 1):>6.2f}{100 * (r10 > 0).mean():>8.1f}%"
                  f"{100 * (r20 > 0).mean():>8.1f}%{r20.mean():>+8.2f}%{r20.median():>+9.2f}%{s20.mean():>+7.2f}%"
                  f"{100 * (r20 > s20).mean():>7.1f}%")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
