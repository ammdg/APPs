"""Vigila acciones del mercado americano y avisa cuando un indicador cumple una regla.

Datos: velas diarias de Yahoo Finance (librería yfinance, no oficial). En config.json:

- "estrategia": señales WATCH / BUY de estrategia_score.py (puntuación + máquina de estados).
- "reglas": alertas sueltas, p. ej. "rsi14 < 30", que avisan cuando la condición pasa de falsa a
  verdadera.

Las condiciones son expresiones sobre indicadores. Nunca se avisa dos veces de lo mismo con la misma vela.
"""

from __future__ import annotations

import argparse
import ast
import json
import operator
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import estrategia_score
import universo

AQUI = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- indicadores


def sma(serie: pd.Series, n: int) -> pd.Series:
    return serie.rolling(n, min_periods=n).mean()


def ema(serie: pd.Series, n: int) -> pd.Series:
    return serie.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(serie: pd.Series, n: int = 14) -> pd.Series:
    """RSI con el suavizado de Wilder."""
    delta = serie.diff()
    subida = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    bajada = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    valor = 100 - 100 / (1 + subida / bajada)
    return valor.where(bajada != 0, 100.0).where(subida.notna())


FIJAS = {
    "precio": "precio de cierre (o último, si la sesión está abierta)",
    "apertura": "precio de apertura",
    "maximo": "máximo del día",
    "minimo": "mínimo del día",
    "volumen": "volumen del día",
    "vol_rel": "volumen / media de volumen de 20 días",
    "macd": "MACD (12, 26)",
    "macd_senal": "señal del MACD (9)",
    "macd_hist": "histograma del MACD",
    "bb_sup": "banda de Bollinger superior (20, 2)",
    "bb_media": "media de Bollinger (20)",
    "bb_inf": "banda de Bollinger inferior (20, 2)",
    "max52": "máximo de 52 semanas (252 sesiones)",
    "min52": "mínimo de 52 semanas (252 sesiones)",
}
PARAMETRICAS = {
    "sma": "media móvil simple de N sesiones (sma50, sma200…)",
    "ema": "media móvil exponencial de N sesiones (ema20…)",
    "rsi": "RSI de N sesiones (rsi14…)",
    "var": "variación % del precio en N sesiones (var1 = hoy, var5 = semana…)",
}
_PARAM = re.compile(r"^(sma|ema|rsi|var)(\d+)$")


def variable(datos: pd.DataFrame, nombre: str) -> pd.Series:
    """Calcula la serie de una variable a partir de las velas (columnas Open/High/Low/Close/Volume)."""
    c = datos["Close"]
    if m := _PARAM.match(nombre):
        tipo, n = m.group(1), int(m.group(2))
        if n < 1:
            raise ValueError(f"Periodo no válido en {nombre!r}")
        return {"sma": sma, "ema": ema, "rsi": rsi}[tipo](c, n) if tipo != "var" else c.pct_change(n) * 100
    if nombre == "precio":
        return c
    if nombre in ("apertura", "maximo", "minimo", "volumen"):
        return datos[{"apertura": "Open", "maximo": "High", "minimo": "Low", "volumen": "Volume"}[nombre]]
    if nombre == "vol_rel":
        return datos["Volume"] / sma(datos["Volume"], 20)
    if nombre in ("macd", "macd_senal", "macd_hist"):
        linea = ema(c, 12) - ema(c, 26)
        senal = linea.ewm(span=9, adjust=False, min_periods=9).mean()
        return {"macd": linea, "macd_senal": senal, "macd_hist": linea - senal}[nombre]
    if nombre in ("bb_sup", "bb_media", "bb_inf"):
        media, desv = sma(c, 20), c.rolling(20, min_periods=20).std(ddof=0)
        return {"bb_sup": media + 2 * desv, "bb_media": media, "bb_inf": media - 2 * desv}[nombre]
    if nombre == "max52":
        return datos["High"].rolling(252, min_periods=252).max()
    if nombre == "min52":
        return datos["Low"].rolling(252, min_periods=252).min()
    raise ValueError(f"Variable desconocida: {nombre!r}. Usa --ayuda-variables para ver la lista.")


# --------------------------------------------------------------------------- expresiones

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def compilar(expresion: str) -> ast.Expression:
    """Valida la expresión: solo números, variables, + - * /, comparaciones y and/or/not."""
    arbol = ast.parse(expresion, mode="eval")
    for nodo in ast.walk(arbol):
        permitido = isinstance(nodo, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
                                      ast.USub, ast.Compare, ast.BinOp, ast.Name, ast.Load, ast.Constant,
                                      *_BIN, *_CMP))
        if not permitido or (isinstance(nodo, ast.Constant) and not isinstance(nodo.value, (int, float))):
            raise ValueError(f"No se admite {type(nodo).__name__} en la regla {expresion!r}")
    return arbol


def nombres(arbol: ast.AST) -> list[str]:
    return list(dict.fromkeys(n.id for n in ast.walk(arbol) if isinstance(n, ast.Name)))


def evaluar(arbol: ast.AST, datos: pd.DataFrame, cache: dict[str, pd.Series] | None = None) -> Any:
    """Evalúa la expresión sobre toda la serie. Una comparación con NaN (faltan datos) es falsa."""
    cache = {} if cache is None else cache

    def ev(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in cache:
                cache[n.id] = variable(datos, n.id)
            return cache[n.id]
        if isinstance(n, ast.BinOp):
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else ~_bool(v, datos)
        if isinstance(n, ast.Compare):
            izq, total = ev(n.left), None
            for op, der_n in zip(n.ops, n.comparators):
                der = ev(der_n)
                parte = _bool(_CMP[type(op)](izq, der), datos)
                total = parte if total is None else total & parte
                izq = der
            return total
        if isinstance(n, ast.BoolOp):
            partes = [_bool(ev(v), datos) for v in n.values]
            total = partes[0]
            for p in partes[1:]:
                total = total & p if isinstance(n.op, ast.And) else total | p
            return total
        raise ValueError(f"Nodo no soportado: {type(n).__name__}")

    return ev(arbol)


def _bool(valor: Any, datos: pd.DataFrame) -> pd.Series:
    if isinstance(valor, pd.Series):
        return valor.fillna(False).astype(bool)
    return pd.Series(bool(valor), index=datos.index)


# --------------------------------------------------------------------------- configuración


@dataclass
class Regla:
    nombre: str
    si: str
    tickers: list[str] | None = None  # None = todas
    modo: str = "cambio"  # "cambio": al pasar de falso a verdadero; "siempre": cada vela en que se cumpla
    arbol: ast.Expression = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.modo not in ("cambio", "siempre"):
            raise ValueError(f"Regla {self.nombre!r}: modo debe ser 'cambio' o 'siempre'")
        self.arbol = compilar(self.si)
        for v in nombres(self.arbol):
            if v not in FIJAS and not _PARAM.match(v):
                raise ValueError(f"Regla {self.nombre!r}: variable desconocida {v!r}")

    def aplica_a(self, ticker: str) -> bool:
        return self.tickers is None or ticker in self.tickers


@dataclass
class Config:
    tickers: list[str]  # se ignora si hay "universo"
    reglas: list[Regla]
    historico: str = "2y"
    estrategia: bool = True  # señales WATCH/BUY de estrategia_score.py
    universo: str | None = None  # fichero con la lista fija (universo_fijo.json, generado con universo.py)


def cargar_config(path: Path) -> Config:
    d = json.loads(path.read_text(encoding="utf-8"))
    estrategia = d.get("estrategia", {}).get("activa", True)
    reglas = [
        Regla(nombre=r["nombre"], si=r["si"], tickers=[t.upper() for t in r["tickers"]] if r.get("tickers") else None,
              modo=r.get("modo", "cambio"))
        for r in d.get("reglas", []) if r.get("activa", True)
    ]
    tickers = list(dict.fromkeys(t.upper() for t in d.get("tickers", [])))
    return Config(tickers=tickers, reglas=reglas, historico=d.get("historico", "2y"), estrategia=estrategia,
                  universo=(d.get("universo") or {}).get("fichero"))


# --------------------------------------------------------------------------- datos


def descargar(tickers: list[str], historico: str = "2y", tanda: int = 100) -> dict[str, pd.DataFrame]:
    """Velas diarias ajustadas por dividendos y splits. Los tickers sin datos no aparecen.

    Se piden en tandas con una pausa entre ellas para no saturar a Yahoo.
    """
    salida: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), tanda):
        if i:
            time.sleep(2)
        salida.update(_descargar_tanda(tickers[i:i + tanda], historico))
    return salida


def _descargar_tanda(tickers: list[str], historico: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf  # import aquí para que las pruebas no lo necesiten

    bruto = yf.download(tickers, period=historico, interval="1d", auto_adjust=True, progress=False,
                        group_by="ticker", threads=True)
    salida = {}
    for t in tickers:
        try:
            df = bruto[t] if isinstance(bruto.columns, pd.MultiIndex) else bruto
        except KeyError:
            continue
        df = df.dropna(subset=["Close"])
        if not df.empty:
            salida[t] = df
    return salida


# --------------------------------------------------------------------------- reglas y estado


@dataclass
class Alerta:
    ticker: str
    nombre: str  # nombre de la regla, o BUY / WATCH
    condicion: str
    fecha: str  # fecha de la vela, AAAA-MM-DD
    precio: float
    var1: float | None
    valores: dict[str, float]

    @property
    def clave(self) -> str:
        return f"{self.ticker}|{self.nombre}"


def revisar(ticker: str, datos: pd.DataFrame, reglas: list[Regla]) -> list[Alerta]:
    """Reglas que se cumplen en la última vela (y, en modo cambio, no se cumplían en la anterior)."""
    if len(datos) < 2:
        return []
    cache: dict[str, pd.Series] = {}
    alertas = []
    for r in reglas:
        if not r.aplica_a(ticker):
            continue
        cond = _bool(evaluar(r.arbol, datos, cache), datos)
        if not cond.iloc[-1] or (r.modo == "cambio" and cond.iloc[-2]):
            continue
        valores = {v: float(cache[v].iloc[-1]) for v in nombres(r.arbol) if v in cache}
        var1 = variable(datos, "var1").iloc[-1]
        alertas.append(Alerta(ticker, r.nombre, r.si, datos.index[-1].strftime("%Y-%m-%d"), float(datos["Close"].iloc[-1]),
                              None if pd.isna(var1) else float(var1), valores))
    return alertas


def senales(datos: pd.DataFrame) -> pd.DataFrame:
    """Ejecuta estrategia_score.generate_signals sobre las velas (índice de fechas)."""
    df = datos.rename_axis("Date").reset_index()
    return estrategia_score.generate_signals(df).set_index("Date")


def revisar_estrategia(ticker: str, datos: pd.DataFrame) -> list[Alerta]:
    """Alerta BUY o WATCH si la estrategia la emite en la última vela.

    La estrategia recorre todo el histórico desde NEUTRAL, así que el estado se reconstruye en cada
    ejecución y no hace falta guardarlo.
    """
    if datos.empty:
        return []
    res = senales(datos)
    ult = res.iloc[-1]
    if ult["Signal"] not in ("BUY", "WATCH"):
        return []
    valores = {"RSI": ult["RSI"], "caída desde máx. 60 (%)": ult["DRAWDOWN"] * 100,
               "vol_rel": ult["VOLUME_RATIO"], "SMA20": ult["SMA20"], "SMA50": ult["SMA50"], "SMA200": ult["SMA200"]}
    var1 = datos["Close"].pct_change().iloc[-1] * 100
    return [Alerta(ticker, ult["Signal"], f"score {ult['Score']:g}", res.index[-1].strftime("%Y-%m-%d"),
                   float(ult["Close"]), None if pd.isna(var1) else float(var1),
                   {k: float(v) for k, v in valores.items()})]


def cargar_estado(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def filtrar_ya_avisadas(alertas: list[Alerta], estado: dict[str, str]) -> list[Alerta]:
    """Quita las alertas ya enviadas para esa misma vela (p. ej. festivos o varias ejecuciones al día)."""
    return [a for a in alertas if estado.get(a.clave) != a.fecha]


# --------------------------------------------------------------------------- avisos


def _num(v: float) -> str:
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.1f} M"
    return f"{v:,.2f}"


def formatear(alertas: list[Alerta]) -> str:
    lineas = [f"📈 Alertas de bolsa ({len(alertas)})"]
    por_ticker: dict[str, list[Alerta]] = {}
    for a in alertas:
        por_ticker.setdefault(a.ticker, []).append(a)
    for t, lista in por_ticker.items():
        a0 = lista[0]
        var = f" ({a0.var1:+.2f}%)" if a0.var1 is not None else ""
        lineas.append(f"\n{t} · {_num(a0.precio)} ${var} · vela {a0.fecha}")
        for a in lista:
            detalle = ", ".join(f"{k} {_num(v)}" for k, v in a.valores.items())
            icono = {"BUY": "🟢", "WATCH": "👀"}.get(a.nombre, "🔔")
            lineas.append(f"  {icono} {a.nombre}: {a.condicion}  [{detalle}]")
        lineas.append(f"  https://finance.yahoo.com/quote/{t}")
    lineas.append("\nInformativo, no es consejo de inversión.")
    return "\n".join(lineas)


def trocear(texto: str, maximo: int = 3900) -> list[str]:
    """Parte el texto por bloques (líneas en blanco) para no pasar del límite de Telegram (4096)."""
    trozos, actual = [], ""
    for bloque in texto.split("\n\n"):
        while len(bloque) > maximo:  # bloque suelto demasiado largo: se corta a lo bruto
            trozos.append(bloque[:maximo])
            bloque = bloque[maximo:]
        if actual and len(actual) + 2 + len(bloque) > maximo:
            trozos.append(actual)
            actual = bloque
        else:
            actual = f"{actual}\n\n{bloque}" if actual else bloque
    if actual:
        trozos.append(actual)
    return trozos


def notificar(texto: str) -> list[str]:
    """Envía el aviso por los canales configurados en variables de entorno. Devuelve los usados."""
    usados = []
    trozos = trocear(texto)
    if len(trozos) > 1:
        for texto_parcial in trozos:
            usados = notificar(texto_parcial)
            time.sleep(1)
        return usados
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
            headers={"Title": "Alertas de bolsa", "Tags": "chart_with_upwards_trend"},
            timeout=20,
        )
        r.raise_for_status()
        usados.append("ntfy")
    if not usados:
        print("(Sin canal de aviso configurado: define TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID o NTFY_TOPIC)")
    return usados


# --------------------------------------------------------------------------- orquestación


def elegir_tickers(cfg: Config) -> list[str]:
    """Tickers de la lista fija (si hay) más los de "tickers" en config.json."""
    if not cfg.universo:
        return cfg.tickers
    fijos, fecha = universo.cargar(AQUI / cfg.universo)
    print(f"Lista fija: {len(fijos)} acciones de mayor capitalización al cierre del {fecha}")
    return fijos + [t for t in cfg.tickers if t not in fijos]


def comprobar(cfg: Config, estado_path: Path, hasta: date | None = None, enviar: bool = True) -> int:
    """Descarga, evalúa y avisa. `hasta`: analizar como si fuera el cierre de ese día (pruebas).
    `enviar=False`: solo imprime el mensaje, sin enviarlo ni guardar el estado."""
    if not cfg.reglas and not cfg.estrategia:
        print("No hay estrategia ni reglas activas en config.json: no se comprueba nada.")
        return 0
    tickers = elegir_tickers(cfg)
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {len(tickers)} tickers · {len(cfg.reglas)} reglas")
    datos = descargar(tickers, cfg.historico)
    if hasta:
        datos = {t: df[df.index.date <= hasta] for t, df in datos.items()}
        datos = {t: df for t, df in datos.items() if not df.empty}
    sin_datos = [t for t in tickers if t not in datos]
    if sin_datos:
        print(f"⚠️ Sin datos: {', '.join(sin_datos)}")
    if not datos:
        print("No se ha podido descargar ningún ticker.")
        return 1

    alertas = [a for t, df in datos.items() for a in revisar(t, df, cfg.reglas)]
    if cfg.estrategia:
        alertas += [a for t, df in datos.items() for a in revisar_estrategia(t, df)]
    estado = cargar_estado(estado_path)
    nuevas = filtrar_ya_avisadas(alertas, estado)
    print(f"Reglas cumplidas: {len(alertas)} · nuevas: {len(nuevas)}")
    if not nuevas:
        return 0

    texto = formatear(nuevas)
    print(texto)
    if not enviar:
        print("(--sin-avisos: no se envía ni se guarda el estado)")
        return 0
    try:
        notificar(texto)
    except requests.RequestException as e:
        # Sin guardar estado: la próxima ejecución reintentará el aviso.
        print(f"Error enviando el aviso: {e}")
        return 1
    estado.update({a.clave: a.fecha for a in nuevas})
    estado_path.write_text(json.dumps(estado, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return 0


def mostrar_valores(ticker: str, cfg: Config) -> int:
    """Imprime los valores actuales de las variables que usan las reglas (para ajustar umbrales)."""
    datos = descargar([ticker.upper()], cfg.historico).get(ticker.upper())
    if datos is None:
        print(f"Sin datos para {ticker}")
        return 1
    usadas = list(dict.fromkeys(v for r in cfg.reglas for v in nombres(r.arbol)))
    print(f"{ticker.upper()} · vela {datos.index[-1]:%Y-%m-%d} · {len(datos)} sesiones")
    for v in usadas:
        print(f"  {v:>12} = {_num(float(variable(datos, v).iloc[-1]))}")
    for r in cfg.reglas:
        if r.aplica_a(ticker.upper()):
            print(f"  {'✅' if bool(_bool(evaluar(r.arbol, datos), datos).iloc[-1]) else '▫️'} {r.nombre}: {r.si}")
    if cfg.estrategia:
        res = senales(datos)
        ult = res.iloc[-1]
        print(f"  Estrategia: score {ult['Score']:g} · RSI {ult['RSI']:.1f} · caída desde máx. 60 "
              f"{ult['DRAWDOWN'] * 100:.1f}% · vol_rel {ult['VOLUME_RATIO']:.2f}")
        hist = res[res["Signal"].isin(["BUY", "WATCH"])].tail(15)
        print(f"  Últimas señales ({len(hist)}):" if len(hist) else "  Sin señales en el histórico")
        for fecha, fila in hist.iterrows():
            print(f"    {fecha:%Y-%m-%d}  {fila['Signal']:<5}  score {fila['Score']:>4g}  precio {_num(float(fila['Close']))}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=AQUI / "config.json")
    p.add_argument("--estado", type=Path, default=AQUI / "estado.json")
    p.add_argument("--cada", type=int, metavar="MIN", help="repetir cada MIN minutos (en tu ordenador)")
    p.add_argument("--ver", metavar="TICKER", help="muestra los valores actuales de un ticker y sale")
    p.add_argument("--hasta", type=date.fromisoformat, metavar="AAAA-MM-DD",
                   help="analizar como si fuera el cierre de ese día (para pruebas)")
    p.add_argument("--sin-avisos", action="store_true", help="solo mostrar el mensaje, sin enviarlo")
    p.add_argument("--probar-aviso", action="store_true", help="manda un mensaje de prueba y sale")
    p.add_argument("--ayuda-variables", action="store_true", help="lista las variables para las reglas")
    a = p.parse_args()

    if a.ayuda_variables:
        for k, v in {**FIJAS, **{k + "N": v for k, v in PARAMETRICAS.items()}}.items():
            print(f"  {k:>12}  {v}")
        return 0
    if a.probar_aviso:
        return 0 if notificar("✅ Prueba de alertas de bolsa") else 1
    cfg = cargar_config(a.config)
    if a.ver:
        return mostrar_valores(a.ver, cfg)
    if not a.cada:
        return comprobar(cfg, a.estado, a.hasta, enviar=not a.sin_avisos)
    while True:
        comprobar(cfg, a.estado)
        time.sleep(a.cada * 60)


if __name__ == "__main__":
    sys.exit(main())
