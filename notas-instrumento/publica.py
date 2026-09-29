"""Prepara las canciones para la web de GitHub Pages (web/). Lo ejecuta GitHub Actions.

Coge cada audio de canciones/ (subido a mano, o grabado con el micrófono desde la página) o
uno descargado de --url, separa los instrumentos, saca las notas y las partituras, y lo deja
todo en web/canciones/<id>/. Después rehace el índice.

Todo lo publicado es público: cualquiera con el enlace de la página (o mirando el repositorio)
puede escucharlo.

Uso:
    python publica.py                                  # procesa lo que haya en canciones/
    python publica.py --url https://... --titulo "Mi canción"
    python publica.py --solo-indice                    # solo rehace web/canciones.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

AQUI = Path(__file__).resolve().parent
ENTRADA = AQUI / "canciones"
WEB = AQUI / "web"
CANCIONES = WEB / "canciones"
INDICE = WEB / "canciones.json"
EXTENSIONES = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".webm", ".mp4"}
LEGIBLES = {".mp3", ".wav", ".flac", ".ogg"}  # los demás se pasan a WAV con ffmpeg
SENSIBILIDADES = [0.35, 0.5, 0.65]  # "menos", "normal", "más" en la página
ZONA = ZoneInfo("Europe/Madrid")


class ErrorPublica(Exception):
    """Error que se explica tal cual."""


def escribe(destino: Path, datos: bytes) -> None:
    destino.write_bytes(datos)


def escribe_json(destino: Path, objeto) -> None:
    escribe(destino, json.dumps(objeto, ensure_ascii=False, separators=(",", ":")).encode())


# --------------------------------------------------------------------------- canciones


def id_de(titulo: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", sin_tildes).strip("-")[:60] or "cancion"


def procesa(audio: Path, titulo: str) -> str:
    """Analiza una canción y la deja en web/canciones/<id>/. Devuelve el id."""
    import analisis
    import notas_instrumento as ni

    id_ = id_de(titulo)
    print(f"== {titulo} ({id_})", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if audio.suffix.lower() not in LEGIBLES:
            audio = a_wav(audio, tmp)
        pistas, frecuencia = ni.separa(audio)
        lista = analisis.guarda_pistas(pistas, frecuencia, tmp, compresion=0.8)
        pulsos, duracion = analisis.pulsos_de(audio)

        destino = CANCIONES / id_
        if destino.exists():
            shutil.rmtree(destino)
        destino.mkdir(parents=True)

        if audio.suffix.lower() == ".mp3":
            mezcla = audio.read_bytes()
        else:  # los navegadores no leen bien todos los formatos: se pasa a MP3
            mezcla = analisis.guarda_audio(tmp / "mezcla", sum(pistas.values()), frecuencia).read_bytes()
        escribe(destino / "mezcla.mp3", mezcla)

        publicas = []
        for p in lista:
            audio_pista = None
            if p["suena"]:  # las que no suenan no se publican: no hay nada que escuchar
                audio_pista = f"{p['clave']}{Path(p['fichero']).suffix}"
                escribe(destino / audio_pista, (tmp / p["fichero"]).read_bytes())
            if p["partitura"]:
                instrumento = ni.INSTRUMENTOS[p["clave"]]
                por_sens = ni.transcribe_varias(tmp / p["fichero"], instrumento, SENSIBILIDADES, 100)
                partituras = {}
                for s, notas in por_sens.items():
                    notas = ni.limpia(notas, instrumento)
                    partituras[f"{s:.2f}"] = analisis.partituras(
                        notas, pulsos, duracion, p["clave"], f"{titulo} - {p['nombre']}")
                    midi = tmp / "notas.mid"
                    ni.guarda_midi(notas, midi, instrumento)
                    escribe(destino / f"{p['clave']}-{s:.2f}.mid", midi.read_bytes())
                escribe_json(destino / f"partitura-{p['clave']}.json", partituras)
            publicas.append({k: p[k] for k in ("clave", "nombre", "actividad", "suena", "partitura")}
                            | {"audio": audio_pista})

        escribe_json(destino / "datos.json", {
            "id": id_, "titulo": titulo, "duracion": round(duracion, 2),
            "fecha": datetime.now(ZONA).isoformat(timespec="seconds"),
            "mezcla": "mezcla.mp3", "pistas": publicas,
        })
    return id_


def a_wav(audio: Path, carpeta: Path) -> Path:
    """Convierte con ffmpeg lo que no se lee directamente (WebM y MP4 de las grabaciones del
    móvil, M4A...)."""
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ErrorPublica(f"Hace falta ffmpeg para leer {audio.suffix} y no está instalado.")
    destino = carpeta / "convertido.wav"
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(audio), "-ac", "2", "-ar", "44100",
                        str(destino)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorPublica(f"ffmpeg no pudo leer {audio.name}: {r.stderr.strip()[-300:]}")
    return destino


def rehace_indice() -> list[dict]:
    canciones = []
    for datos in sorted(CANCIONES.glob("*/datos.json")) if CANCIONES.is_dir() else []:
        try:
            d = json.loads(datos.read_text())
        except Exception as e:
            print(f"AVISO: no puedo leer {datos}: {e}", file=sys.stderr)
            continue
        canciones.append({k: d[k] for k in ("id", "titulo", "duracion", "fecha")})
    canciones.sort(key=lambda c: c["fecha"], reverse=True)
    WEB.mkdir(parents=True, exist_ok=True)
    escribe_json(INDICE, {"canciones": canciones})
    return canciones


def descarga(url: str, carpeta: Path) -> Path:
    """Descarga un audio de un enlace directo. Los enlaces de Dropbox y Google Drive "para
    compartir" se convierten a descarga directa."""
    if "dropbox.com" in url:
        url = re.sub(r"([?&])dl=0", r"\1dl=1", url)
        if "dl=1" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=)([\w-]+)", url)
    if m:
        url = f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    peticion = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(peticion, timeout=120) as r:
        tipo = r.headers.get("Content-Type", "")
        if "text/html" in tipo:
            raise ErrorPublica("El enlace devuelve una página web, no un audio. Usa un enlace de "
                               "descarga directa (o compártelo como «cualquiera con el enlace»).")
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if ext not in EXTENSIONES:
            ext = ".m4a" if "mp4" in tipo else ".ogg" if "ogg" in tipo else ".wav" if "wav" in tipo else ".mp3"
        destino = carpeta / f"descarga{ext}"
        with destino.open("wb") as f:
            shutil.copyfileobj(r, f)
    return destino


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", help="enlace de descarga del audio (en vez de la carpeta canciones/)")
    p.add_argument("--titulo", help="título de la canción descargada con --url")
    p.add_argument("--solo-indice", action="store_true", help="solo rehacer el índice")
    a = p.parse_args(argv)

    try:
        fallos = []
        if a.url:
            if not a.titulo:
                raise ErrorPublica("Con --url hace falta --titulo.")
            with tempfile.TemporaryDirectory() as tmp:
                procesa(descarga(a.url, Path(tmp)), a.titulo)
        elif not a.solo_indice:
            entradas = sorted(f for f in ENTRADA.glob("*") if f.suffix.lower() in EXTENSIONES)
            if not entradas:
                print("No hay canciones nuevas en canciones/.", file=sys.stderr)
            for audio in entradas:
                try:
                    procesa(audio, audio.stem)
                    audio.unlink()  # ya está publicada; el original no hace falta
                except Exception as e:  # que una canción rota no impida publicar las demás
                    import traceback
                    traceback.print_exc()
                    fallos.append(f"{audio.name}: {e}")
        canciones = rehace_indice()
    except ErrorPublica as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Publicadas {len(canciones)} canciones.", file=sys.stderr)
    if fallos:
        print("No se pudieron procesar:\n  " + "\n  ".join(fallos), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
