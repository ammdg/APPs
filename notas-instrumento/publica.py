"""Prepara las canciones para la web de GitHub Pages (web/). Lo ejecuta GitHub Actions.

Coge cada audio de canciones/ (o uno descargado de --url), separa los instrumentos, saca las
notas y las partituras, y lo deja todo en web/canciones/<id>/. Después rehace el índice.

Si hay usuario y contraseña (NOTAS_USUARIO y NOTAS_PASSWORD, o si no MAPA_USUARIO y
MAPA_PASSWORD, los del mapa de vuelos), todo lo publicado va cifrado: aunque el repositorio sea
público, sin ellos no se puede escuchar ni leer nada. Mismo cifrado que el mapa de vuelos:
AES-256-GCM con clave PBKDF2-SHA256 de "usuario:contraseña"; la página lo descifra en el navegador.

Uso:
    python publica.py                                  # procesa lo que haya en canciones/
    python publica.py --url https://... --titulo "Mi canción"
    python publica.py --solo-indice                    # solo rehace web/canciones.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
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
CLAVE = WEB / "clave.json"
EXTENSIONES = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
SENSIBILIDADES = [0.35, 0.5, 0.65]  # "menos", "normal", "más" en la página
ITERACIONES_CLAVE = 600_000  # igual que el mapa de vuelos; web/web.js lee el número de clave.json
COMPROBANTE = b"notas-instrumento"
ZONA = ZoneInfo("Europe/Madrid")


class ErrorPublica(Exception):
    """Error que se explica tal cual."""


# --------------------------------------------------------------------------- cifrado


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def deriva_clave(usuario: str, password: str, sal: bytes, iteraciones: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", f"{usuario}:{password}".encode(), sal, iteraciones,
                               dklen=32)


def cifra(datos: bytes, clave: bytes) -> bytes:
    """IV (12 bytes) + texto cifrado con su etiqueta."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = secrets.token_bytes(12)
    return iv + AESGCM(clave).encrypt(iv, datos, None)


def descifra(datos: bytes, clave: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(clave).decrypt(datos[:12], datos[12:], None)


def credenciales() -> tuple[str, str] | None:
    for prefijo in ("NOTAS", "MAPA"):
        usuario, password = os.getenv(f"{prefijo}_USUARIO", ""), os.getenv(f"{prefijo}_PASSWORD", "")
        if usuario and password:
            return usuario, password
    return None


def clave_publicada(cred: tuple[str, str] | None) -> bytes | None:
    """La clave con la que está cifrado lo que ya hay en web/ (None si no está cifrado).
    Falla si está cifrado y las credenciales no valen (se cambió la contraseña)."""
    if not CLAVE.is_file():
        return None
    info = json.loads(CLAVE.read_text())
    if cred is None:
        raise ErrorPublica(
            "La web está cifrada pero no hay usuario y contraseña (NOTAS_USUARIO/NOTAS_PASSWORD). "
            "Vuelve a ponerlos en los secretos del repositorio.")
    clave = deriva_clave(*cred, base64.b64decode(info["sal"]), info["iteraciones"])
    try:
        if descifra(base64.b64decode(info["comprobante"]), clave) != COMPROBANTE:
            raise ValueError
    except Exception:
        raise ErrorPublica(
            "El usuario o la contraseña no coinciden con los que se usaron para cifrar la web. "
            "Si los has cambiado a propósito, borra la carpeta notas-instrumento/web/canciones y "
            "el fichero web/clave.json y vuelve a subir las canciones.") from None
    return clave


def prepara_cifrado(cred: tuple[str, str] | None) -> bytes | None:
    """Deja web/ en el modo que toca (cifrado o no) y devuelve la clave con la que escribir.
    Si se acaban de poner o quitar las credenciales, convierte lo ya publicado."""
    actual = clave_publicada(cred) if CLAVE.is_file() else None
    if cred is None:
        print("AVISO: sin NOTAS_USUARIO/NOTAS_PASSWORD, las canciones se publican sin cifrar: "
              "cualquiera con el enlace podrá escucharlas.", file=sys.stderr)
        return None
    if actual is not None:
        return actual
    sal = secrets.token_bytes(16)
    nueva = deriva_clave(*cred, sal, ITERACIONES_CLAVE)
    for fichero in _publicados():  # estaban sin cifrar
        fichero.write_bytes(cifra(fichero.read_bytes(), nueva))
    WEB.mkdir(parents=True, exist_ok=True)
    CLAVE.write_text(json.dumps({
        "cifrado": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iteraciones": ITERACIONES_CLAVE,
        "sal": _b64(sal), "comprobante": _b64(cifra(COMPROBANTE, nueva)),
    }, indent=1))
    return nueva


def _publicados() -> list[Path]:
    ficheros = [f for f in CANCIONES.rglob("*") if f.is_file()] if CANCIONES.is_dir() else []
    return ficheros + ([INDICE] if INDICE.is_file() else [])


def escribe(destino: Path, datos: bytes, clave: bytes | None) -> None:
    destino.write_bytes(cifra(datos, clave) if clave else datos)


def lee(origen: Path, clave: bytes | None) -> bytes:
    datos = origen.read_bytes()
    return descifra(datos, clave) if clave else datos


def escribe_json(destino: Path, objeto, clave: bytes | None) -> None:
    escribe(destino, json.dumps(objeto, ensure_ascii=False, separators=(",", ":")).encode(), clave)


# --------------------------------------------------------------------------- canciones


def id_de(titulo: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", sin_tildes).strip("-")[:60] or "cancion"


def procesa(audio: Path, titulo: str, clave: bytes | None) -> str:
    """Analiza una canción y la deja en web/canciones/<id>/. Devuelve el id."""
    import analisis
    import notas_instrumento as ni

    id_ = id_de(titulo)
    print(f"== {titulo} ({id_})", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
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
        escribe(destino / "mezcla.mp3", mezcla, clave)

        publicas = []
        for p in lista:
            audio_pista = None
            if p["suena"]:  # las que no suenan no se publican: no hay nada que escuchar
                audio_pista = f"{p['clave']}{Path(p['fichero']).suffix}"
                escribe(destino / audio_pista, (tmp / p["fichero"]).read_bytes(), clave)
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
                    escribe(destino / f"{p['clave']}-{s:.2f}.mid", midi.read_bytes(), clave)
                escribe_json(destino / f"partitura-{p['clave']}.json", partituras, clave)
            publicas.append({k: p[k] for k in ("clave", "nombre", "actividad", "suena", "partitura")}
                            | {"audio": audio_pista})

        escribe_json(destino / "datos.json", {
            "id": id_, "titulo": titulo, "duracion": round(duracion, 2),
            "fecha": datetime.now(ZONA).isoformat(timespec="seconds"),
            "mezcla": "mezcla.mp3", "pistas": publicas,
        }, clave)
    return id_


def rehace_indice(clave: bytes | None) -> list[dict]:
    canciones = []
    for datos in sorted(CANCIONES.glob("*/datos.json")) if CANCIONES.is_dir() else []:
        try:
            d = json.loads(lee(datos, clave))
        except Exception as e:
            print(f"AVISO: no puedo leer {datos}: {e}", file=sys.stderr)
            continue
        canciones.append({k: d[k] for k in ("id", "titulo", "duracion", "fecha")})
    canciones.sort(key=lambda c: c["fecha"], reverse=True)
    WEB.mkdir(parents=True, exist_ok=True)
    escribe_json(INDICE, {"canciones": canciones}, clave)
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
        clave = prepara_cifrado(credenciales())
        fallos = []
        if a.url:
            if not a.titulo:
                raise ErrorPublica("Con --url hace falta --titulo.")
            with tempfile.TemporaryDirectory() as tmp:
                procesa(descarga(a.url, Path(tmp)), a.titulo, clave)
        elif not a.solo_indice:
            entradas = sorted(f for f in ENTRADA.glob("*") if f.suffix.lower() in EXTENSIONES)
            if not entradas:
                print("No hay canciones nuevas en canciones/.", file=sys.stderr)
            for audio in entradas:
                try:
                    procesa(audio, audio.stem, clave)
                    audio.unlink()  # ya está publicada; el original no hace falta
                except Exception as e:  # que una canción rota no impida publicar las demás
                    import traceback
                    traceback.print_exc()
                    fallos.append(f"{audio.name}: {e}")
        canciones = rehace_indice(clave)
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
