"""Aplicación web local: subes una canción, separa los instrumentos, eliges uno y escuchas la
canción mientras se ilumina su partitura.

Uso:
    python app.py                 # abre http://127.0.0.1:5000
    python app.py --puerto 8000

Todo se ejecuta en tu ordenador; las canciones y las pistas separadas se guardan en ./trabajos/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import traceback
import uuid
import webbrowser
from pathlib import Path

import numpy as np
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import notas_instrumento as ni
import partitura

AQUI = Path(__file__).resolve().parent
ESTATICOS = AQUI / "static"
TRABAJOS = AQUI / "trabajos"
EXTENSIONES = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
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

ID_VALIDO = re.compile(r"^[0-9a-f]{32}$")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

_estado: dict[str, dict] = {}
_cerrojo = threading.Lock()


# --------------------------------------------------------------------------- análisis


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


def pulsos_de(audio: Path) -> tuple[list[float], float]:
    import librosa

    y, sr = librosa.load(str(audio), sr=FRECUENCIA_ANALISIS, mono=True)
    _, pulsos = librosa.beat.beat_track(y=y, sr=sr, units="time")
    return [float(p) for p in pulsos], len(y) / sr


def _guarda_audio(destino_sin_ext: Path, datos: np.ndarray, frecuencia: int) -> Path:
    """MP3 si libsndfile lo soporta (ocupa ~10 veces menos), si no WAV."""
    import soundfile as sf

    try:
        destino = destino_sin_ext.with_suffix(".mp3")
        sf.write(str(destino), datos, frecuencia, format="MP3")
        return destino
    except Exception:
        destino = destino_sin_ext.with_suffix(".wav")
        sf.write(str(destino), datos, frecuencia)
        return destino


def _actualiza(id_: str, **cambios) -> None:
    with _cerrojo:
        _estado[id_].update(cambios)
        copia = dict(_estado[id_])
    (TRABAJOS / id_ / "estado.json").write_text(json.dumps(copia, ensure_ascii=False))


def analiza(id_: str) -> None:
    carpeta = TRABAJOS / id_
    try:
        original = next(carpeta.glob("original.*"))
        _actualiza(id_, estado="separando", mensaje="Separando instrumentos...", progreso=0.0)
        pistas, frecuencia = ni.separa(original, lambda f: _actualiza(id_, progreso=round(f, 3)))

        _actualiza(id_, mensaje="Detectando qué instrumentos suenan...", progreso=1.0)
        mezcla = sum(pistas.values())
        mono = mezcla.mean(axis=1) if mezcla.ndim == 2 else mezcla
        ventana = frecuencia // 2
        n = max(1, len(mono) // ventana)
        pico = float(np.sqrt((mono[: n * ventana].reshape(n, -1) ** 2).mean(axis=1)).max())

        lista = []
        for clave, nombre_demucs, nombre in PISTAS:
            datos = pistas[nombre_demucs]
            fichero = _guarda_audio(carpeta / clave, datos, frecuencia)
            act = actividad(datos, pico, frecuencia)
            lista.append({"clave": clave, "nombre": nombre, "fichero": fichero.name,
                          "actividad": round(act, 3), "suena": act >= ACTIVIDAD_MINIMA,
                          "partitura": clave not in SIN_PARTITURA})

        _actualiza(id_, mensaje="Buscando el pulso...")
        pulsos, duracion = pulsos_de(original)
        _actualiza(id_, estado="listo", mensaje="", pistas=lista, pulsos=pulsos,
                   duracion=duracion)
    except Exception as e:  # se muestra en la pantalla
        traceback.print_exc()
        _actualiza(id_, estado="error", mensaje=f"No se pudo analizar la canción: {e}")


def _trabajo(id_: str) -> dict:
    if not ID_VALIDO.match(id_):
        abort(404)
    with _cerrojo:
        if id_ in _estado:
            return dict(_estado[id_])
    fichero = TRABAJOS / id_ / "estado.json"
    if not fichero.is_file():
        abort(404)
    datos = json.loads(fichero.read_text())
    if datos.get("estado") != "listo":  # se cortó a medias (se cerró el servidor)
        datos.update(estado="error", mensaje="El análisis se interrumpió. Sube la canción otra vez.")
    with _cerrojo:
        _estado[id_] = datos
    return dict(datos)


def _pista(trabajo: dict, clave: str) -> dict:
    for p in trabajo.get("pistas", []):
        if p["clave"] == clave:
            return p
    abort(404)


# --------------------------------------------------------------------------- rutas


@app.get("/")
def portada():
    return send_from_directory(ESTATICOS, "index.html")


@app.get("/static/<path:ruta>")
def estaticos(ruta: str):
    return send_from_directory(ESTATICOS, ruta)


@app.post("/api/canciones")
def sube():
    fichero = request.files.get("audio")
    if fichero is None or not fichero.filename:
        return jsonify(error="Falta el fichero de audio."), 400
    ext = Path(fichero.filename).suffix.lower()
    if ext not in EXTENSIONES:
        return jsonify(error=f"Formato no admitido ({ext or 'sin extensión'})."), 400

    id_ = uuid.uuid4().hex
    carpeta = TRABAJOS / id_
    carpeta.mkdir(parents=True)
    fichero.save(carpeta / f"original{ext}")
    titulo = Path(fichero.filename).stem
    with _cerrojo:
        _estado[id_] = {"id": id_, "titulo": titulo, "original": f"original{ext}",
                        "estado": "en cola", "mensaje": "En cola...", "progreso": 0.0}
    _actualiza(id_)
    threading.Thread(target=analiza, args=(id_,), daemon=True).start()
    return jsonify(id=id_), 202


@app.get("/api/canciones/<id_>")
def consulta(id_: str):
    t = _trabajo(id_)
    t.pop("pulsos", None)
    return jsonify(t)


@app.get("/api/canciones/<id_>/audio/<clave>")
def audio(id_: str, clave: str):
    t = _trabajo(id_)
    carpeta = TRABAJOS / id_
    if clave == "mezcla":
        return send_file(carpeta / t["original"], conditional=True)
    if clave.startswith("sin-"):
        return send_file(_sin(t, clave[4:]), conditional=True)
    return send_file(carpeta / _pista(t, clave)["fichero"], conditional=True)


def _sin(t: dict, clave: str) -> Path:
    """La canción sin un instrumento: suma de las demás pistas (se calcula una vez)."""
    import soundfile as sf

    _pista(t, clave)
    carpeta = TRABAJOS / t["id"]
    hecho = list(carpeta.glob(f"sin-{clave}.*"))
    if hecho:
        return hecho[0]
    suma, frecuencia = None, None
    for p in t["pistas"]:
        if p["clave"] != clave:
            datos, frecuencia = sf.read(str(carpeta / p["fichero"]), dtype="float32")
            if suma is None:
                suma = datos
            else:
                largo = min(len(suma), len(datos))
                suma = suma[:largo] + datos[:largo]
    return _guarda_audio(carpeta / f"sin-{clave}", suma, frecuencia)


def _notas(t: dict, clave: str, sensibilidad: float) -> list[ni.Nota]:
    carpeta = TRABAJOS / t["id"]
    cache = carpeta / f"notas-{clave}-{sensibilidad:.2f}.json"
    if cache.is_file():
        return [ni.Nota(**n) for n in json.loads(cache.read_text())]
    instrumento = ni.INSTRUMENTOS[clave]
    notas, _ = ni.transcribe(carpeta / _pista(t, clave)["fichero"], instrumento, sensibilidad, 100)
    notas = ni.filtra_registro(notas, instrumento)
    if instrumento.monofonico:
        notas = ni.monofoniza(notas)
    notas.sort(key=lambda n: (n.inicio, n.midi))
    cache.write_text(json.dumps([n.__dict__ for n in notas]))
    return notas


def _sensibilidad() -> float:
    try:
        s = float(request.args.get("sensibilidad", 0.5))
    except ValueError:
        abort(400)
    if not 0 < s < 1:
        abort(400)
    return s


@app.get("/api/canciones/<id_>/partitura/<clave>")
def partitura_de(id_: str, clave: str):
    t = _trabajo(id_)
    pista = _pista(t, clave)
    if not pista["partitura"]:
        return jsonify(error="La batería no tiene notas con altura: no hay partitura."), 400
    notacion = "inglesa" if request.args.get("notacion") == "inglesa" else "latina"
    notas = _notas(t, clave, _sensibilidad())
    datos = partitura.partitura(notas, t["pulsos"], t["duracion"], clave,
                                f"{t['titulo']} - {pista['nombre']}", notacion)
    datos["cantidad"] = len(notas)
    return jsonify(datos)


@app.get("/api/canciones/<id_>/midi/<clave>")
def midi(id_: str, clave: str):
    t = _trabajo(id_)
    if not _pista(t, clave)["partitura"]:
        abort(404)
    notas = _notas(t, clave, _sensibilidad())
    destino = TRABAJOS / id_ / f"{clave}.mid"
    ni.guarda_midi(notas, destino, ni.INSTRUMENTOS[clave])
    return send_file(destino, as_attachment=True,
                     download_name=f"{t['titulo']} - {clave}.mid")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--puerto", type=int, default=5000)
    p.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    a = p.parse_args(argv)
    TRABAJOS.mkdir(exist_ok=True)
    url = f"http://127.0.0.1:{a.puerto}"
    print(f"Abre {url} en el navegador (Ctrl+C para salir).", file=sys.stderr)
    if not a.no_abrir:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=a.puerto, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
