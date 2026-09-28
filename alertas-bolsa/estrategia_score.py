# Estrategia de señales WATCH / BUY por puntuación (score) y máquina de estados.
# Código aportado por el usuario, sin cambios de lógica. alertas_bolsa.py lo llama para cada acción.

import pandas as pd
import numpy as np


# ============================================================
# CONFIGURACIÓN
# ============================================================

SMA_FAST = 50
SMA_SLOW = 200
SMA_SHORT = 20

RSI_PERIOD = 14
ATR_PERIOD = 14

CORRECTION_MIN = -0.15     # -15%
CORRECTION_MAX = -0.05     # -5%

RSI_CORRECTION = 45
RSI_CONFIRMATION = 50

VOLUME_RATIO_MIN = 1.20

SCORE_WATCH = 60
SCORE_BUY = 70


# ============================================================
# INDICADORES
# ============================================================

def calculate_indicators(df):

    df = df.copy()

    # --------------------------------------------------------
    # Medias móviles
    # --------------------------------------------------------

    df["SMA20"] = df["Close"].rolling(SMA_SHORT).mean()
    df["SMA50"] = df["Close"].rolling(SMA_FAST).mean()
    df["SMA200"] = df["Close"].rolling(SMA_SLOW).mean()

    # SMA200 de hace 20 sesiones
    df["SMA200_20"] = df["SMA200"].shift(20)

    # --------------------------------------------------------
    # RSI(14)
    # --------------------------------------------------------

    delta = df["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / RSI_PERIOD,
        min_periods=RSI_PERIOD,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / RSI_PERIOD,
        min_periods=RSI_PERIOD,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss

    df["RSI"] = 100 - (100 / (1 + rs))

    # --------------------------------------------------------
    # Máximo 60 sesiones
    # --------------------------------------------------------

    df["MAX60"] = df["Close"].rolling(60).max()

    # Drawdown respecto al máximo
    df["DRAWDOWN"] = (
        df["Close"] / df["MAX60"] - 1
    )

    # --------------------------------------------------------
    # Máximo de los 3 días anteriores
    # --------------------------------------------------------

    df["MAX_PREVIOUS_3"] = (
        df["Close"]
        .shift(1)
        .rolling(3)
        .max()
    )

    # --------------------------------------------------------
    # Volumen medio 20 sesiones
    # --------------------------------------------------------

    df["AVG_VOLUME20"] = (
        df["Volume"]
        .rolling(20)
        .mean()
    )

    df["VOLUME_RATIO"] = (
        df["Volume"] /
        df["AVG_VOLUME20"]
    )

    return df


# ============================================================
# CÁLCULO DEL SCORE
# ============================================================

def calculate_score(row, rsi_min_last_10):

    score = 0

    # --------------------------------------------------------
    # TENDENCIA — 30 puntos
    # --------------------------------------------------------

    if row["Close"] > row["SMA200"]:
        score += 10

    if row["SMA50"] > row["SMA200"]:
        score += 10

    if row["SMA200"] > row["SMA200_20"]:
        score += 10

    # --------------------------------------------------------
    # CORRECCIÓN — 20 puntos
    # --------------------------------------------------------

    if (
        CORRECTION_MIN
        <= row["DRAWDOWN"]
        <= CORRECTION_MAX
    ):
        score += 20

    # --------------------------------------------------------
    # RSI — 20 puntos
    # --------------------------------------------------------

    if rsi_min_last_10 < RSI_CORRECTION:
        score += 10

    rsi_recovery = (
        row["RSI"] > RSI_CONFIRMATION
        and row["RSI_prev"] <= RSI_CONFIRMATION
    )

    if rsi_recovery:
        score += 10

    # --------------------------------------------------------
    # PRECIO — 15 puntos
    # --------------------------------------------------------

    if row["Close"] > row["SMA20"]:
        score += 7.5

    if row["Close"] > row["MAX_PREVIOUS_3"]:
        score += 7.5

    # --------------------------------------------------------
    # VOLUMEN — 15 puntos
    # --------------------------------------------------------

    if row["VOLUME_RATIO"] > VOLUME_RATIO_MIN:
        score += 15

    return score


# ============================================================
# ANALIZADOR DE SEÑALES
# ============================================================

def generate_signals(df):

    df = calculate_indicators(df)

    # RSI del día anterior
    df["RSI_prev"] = df["RSI"].shift(1)

    # Resultado
    df["Score"] = np.nan
    df["Signal"] = "NO SIGNAL"

    # Para almacenar el estado interno
    state = "NEUTRAL"

    # Historial de RSI de las últimas 10 sesiones
    for i in range(len(df)):

        # ----------------------------------------------------
        # Necesitamos suficientes datos
        # ----------------------------------------------------

        if i < 200:
            continue

        row = df.iloc[i]

        # ----------------------------------------------------
        # RSI mínimo de las últimas 10 sesiones
        # ----------------------------------------------------

        rsi_window = df["RSI"].iloc[
            max(0, i - 9):i + 1
        ]

        rsi_min_last_10 = rsi_window.min()

        # ----------------------------------------------------
        # FILTRO DE TENDENCIA
        # ----------------------------------------------------

        trend_ok = (
            row["Close"] > row["SMA200"]
            and row["SMA50"] > row["SMA200"]
            and row["SMA200"] > row["SMA200_20"]
        )

        # Si no existe tendencia alcista:
        # reset completo de la configuración.

        if not trend_ok:

            df.loc[df.index[i], "Signal"] = "NO SIGNAL"

            state = "NEUTRAL"

            continue

        # ----------------------------------------------------
        # CORRECCIÓN
        # ----------------------------------------------------

        correction = (
            CORRECTION_MIN
            <= row["DRAWDOWN"]
            <= CORRECTION_MAX
        )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        rsi_correction = (
            rsi_min_last_10 < RSI_CORRECTION
        )

        rsi_recovery = (
            row["RSI"] > RSI_CONFIRMATION
            and row["RSI_prev"] <= RSI_CONFIRMATION
        )

        # ----------------------------------------------------
        # PRECIO
        # ----------------------------------------------------

        price_above_sma20 = (
            row["Close"] > row["SMA20"]
        )

        price_breakout = (
            row["Close"] > row["MAX_PREVIOUS_3"]
        )

        price_confirmation = (
            price_above_sma20
            and price_breakout
        )

        # ----------------------------------------------------
        # VOLUMEN
        # ----------------------------------------------------

        volume_confirmation = (
            row["VOLUME_RATIO"] > VOLUME_RATIO_MIN
        )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            row,
            rsi_min_last_10
        )

        df.loc[df.index[i], "Score"] = score

        # ----------------------------------------------------
        # CONDICIÓN WATCH
        # ----------------------------------------------------

        watch_conditions = (
            score >= SCORE_WATCH
        )

        # ----------------------------------------------------
        # CONDICIÓN BUY
        # ----------------------------------------------------

        buy_conditions = (
            score >= SCORE_BUY
            and rsi_recovery
            and price_confirmation
        )

        # ====================================================
        # MÁQUINA DE ESTADOS
        # ====================================================

        # ----------------------------------------------------
        # ESTADO NEUTRAL
        # ----------------------------------------------------

        if state == "NEUTRAL":

            if buy_conditions:

                # Primera aparición de BUY
                df.loc[
                    df.index[i],
                    "Signal"
                ] = "BUY"

                state = "BUY_ACTIVE"

            elif watch_conditions:

                # Primera aparición de WATCH
                df.loc[
                    df.index[i],
                    "Signal"
                ] = "WATCH"

                state = "WATCH_ACTIVE"

            else:

                df.loc[
                    df.index[i],
                    "Signal"
                ] = "NO SIGNAL"

        # ----------------------------------------------------
        # ESTADO WATCH_ACTIVE
        # ----------------------------------------------------

        elif state == "WATCH_ACTIVE":

            if buy_conditions:

                # WATCH se convierte en BUY
                df.loc[
                    df.index[i],
                    "Signal"
                ] = "BUY"

                state = "BUY_ACTIVE"

            elif not watch_conditions:

                # La configuración ha desaparecido
                df.loc[
                    df.index[i],
                    "Signal"
                ] = "NO SIGNAL"

                state = "NEUTRAL"

            else:

                # Sigue siendo WATCH,
                # pero NO repetimos la alerta.
                df.loc[
                    df.index[i],
                    "Signal"
                ] = "NO SIGNAL"

        # ----------------------------------------------------
        # ESTADO BUY_ACTIVE
        # ----------------------------------------------------

        elif state == "BUY_ACTIVE":

            # No volvemos a generar BUY
            df.loc[
                df.index[i],
                "Signal"
            ] = "NO SIGNAL"

            # Cuando desaparece completamente
            # la configuración, volvemos a neutral.

            if not watch_conditions:

                state = "NEUTRAL"

    return df


# ============================================================
# OBTENER ÚNICAMENTE LAS ALERTAS
# ============================================================

def get_alerts(df):

    result = generate_signals(df)

    alerts = result[
        result["Signal"].isin(
            ["WATCH", "BUY"]
        )
    ].copy()

    return alerts[
        [
            "Date",
            "Close",
            "RSI",
            "SMA20",
            "SMA50",
            "SMA200",
            "DRAWDOWN",
            "VOLUME_RATIO",
            "Score",
            "Signal"
        ]
    ]
