"""Lo que comparten la app local (app.py) y la publicación en GitHub Pages (publica.py): qué
pistas hay, cómo se decide si suenan, el pulso y cómo se guardan los audios."""

from __future__ import annotations

from pathlib import Path

import numpy as np

import notas_instrumento as ni
import partitura

FRECUENCIA_ANALISIS = 22050

# (clave, pista de Demucs, nombre visible). El orden es el de la pantalla.
PISTAS = [
    ("voz", "vocals", "Voz"),
    ("guitarra", "guitar", "Guitarra"),
    ("bajo", "bass", "Bajo"),
    ("piano", "piano", "Piano / teclado"),
    ("otros", "other", "Otros instrumentos"),
    ("bateria", "drums", "Batería"),
]
SIN_PARTITURA = {"bateria"}

# Una pista cuenta como "suena" si pasa del umbral en al menos este % del tiempo. Son valores
# puestos a ojo, no calibrados: por eso la pantalla deja elegir también las que no pasan.
UMBRAL_DB = -35.0
ACTIVIDAD_MINIMA = 0.05


def actividad(pista: np.ndarray, pico_mezcla: float, frecuencia: int) -> float:
    """Fracción de ventanas de 0.5 s en que la pista suena por encima de UMBRAL_DB respecto al
    nivel máximo de la mezcla."""
    mono = pista.mean(axis=1) if pista.ndim == 2 else pista
    ventana = frecuencia // 2
    n = len(mono) // ventana
    if n == 0 or pico_mezcla <= 0:
        return 0.0
    rms = np.sqrt((mono[: n * ventana].reshape(n, ventana) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-10) / pico_mezcla)
    return float((db > UMBRAL_DB).mean())


def pico(mezcla: np.ndarray, frecuencia: int) -> float:
    """Nivel RMS máximo de la mezcla en ventanas de 0.5 s."""
    mono = mezcla.mean(axis=1) if mezcla.ndim == 2 else mezcla
    ventana = frecuencia // 2
    n = max(1, len(mono) // ventana)
    return float(np.sqrt((mono[: n * ventana].reshape(n, -1) ** 2).mean(axis=1)).max())


def pulsos_de(audio: Path) -> tuple[list[float], float]:
    """Pulsos detectados (segundos) y duración de la canción."""
    import librosa

    y, sr = librosa.load(str(audio), sr=FRECUENCIA_ANALISIS, mono=True)
    _, pulsos = librosa.beat.beat_track(y=y, sr=sr, units="time")
    return [float(p) for p in pulsos], len(y) / sr


def guarda_audio(destino_sin_ext: Path, datos: np.ndarray, frecuencia: int,
                 compresion: float | None = None) -> Path:
    """MP3 si libsndfile lo soporta (ocupa ~10 veces menos), si no WAV. `compresion` (0-1): más
    alto = fichero más pequeño y peor calidad."""
    import soundfile as sf

    destino = destino_sin_ext.with_suffix(".mp3")
    try:
        if compresion is not None:
            try:
                sf.write(str(destino), datos, frecuencia, format="MP3", compression_level=compresion)
                return destino
            except TypeError:  # soundfile antiguo, sin compression_level
                pass
        sf.write(str(destino), datos, frecuencia, format="MP3")
        return destino
    except Exception:
        destino = destino_sin_ext.with_suffix(".wav")
        sf.write(str(destino), datos, frecuencia)
        return destino


def guarda_pistas(pistas: dict, frecuencia: int, carpeta: Path,
                  compresion: float | None = None) -> list[dict]:
    """Guarda cada pista en `carpeta` y devuelve su resumen para la pantalla."""
    nivel = pico(sum(pistas.values()), frecuencia)
    lista = []
    for clave, nombre_demucs, nombre in PISTAS:
        datos = pistas[nombre_demucs]
        fichero = guarda_audio(carpeta / clave, datos, frecuencia, compresion)
        act = actividad(datos, nivel, frecuencia)
        lista.append({"clave": clave, "nombre": nombre, "fichero": fichero.name,
                      "actividad": round(act, 3), "suena": act >= ACTIVIDAD_MINIMA,
                      "partitura": clave not in SIN_PARTITURA})
    return lista


def partituras(notas: list[ni.Nota], pulsos: list[float], duracion: float, clave: str,
               titulo: str) -> dict:
    """Partitura sin nombres y con nombres en las dos notaciones (lo que necesita la web)."""
    latina = partitura.partitura(notas, pulsos, duracion, clave, titulo, "latina")
    inglesa = partitura.partitura(notas, pulsos, duracion, clave, titulo, "inglesa")
    return {
        "cantidad": len(notas), "tempo": latina["tempo"],
        "abc": latina["abc"], "eventos": latina["eventos"],
        "nombres": {
            "latina": {"abc": latina["abc_nombres"], "eventos": latina["eventos_nombres"]},
            "inglesa": {"abc": inglesa["abc_nombres"], "eventos": inglesa["eventos_nombres"]},
        },
    }
