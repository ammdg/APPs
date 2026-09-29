"""Pruebas de la publicación para GitHub Pages, con separación, transcripción y pulso falsos."""

import io
import json

import numpy as np
import pytest

import analisis
import notas_instrumento as ni
import publica

SR = 8000


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(publica, "ENTRADA", tmp_path / "canciones")
    monkeypatch.setattr(publica, "WEB", tmp_path / "web")
    monkeypatch.setattr(publica, "CANCIONES", tmp_path / "web" / "canciones")
    monkeypatch.setattr(publica, "INDICE", tmp_path / "web" / "canciones.json")

    n = SR * 4
    tono = 0.5 * np.sin(2 * np.pi * 220 * np.arange(n) / SR).astype("float32")
    pistas = {k: np.zeros((n, 2), dtype="float32")
              for k in ["drums", "bass", "other", "vocals", "guitar", "piano"]}
    pistas["vocals"] = np.stack([tono, tono], 1)
    monkeypatch.setattr(ni, "separa", lambda audio, progreso=None: (pistas, SR))
    monkeypatch.setattr(analisis, "pulsos_de", lambda audio: ([0.5, 1.0, 1.5, 2.0], 4.0))
    monkeypatch.setattr(ni, "transcribe_varias", lambda audio, ins, sens, dur: {
        s: [ni.Nota(0.0, 0.5, 57, 0.9)] * (1 if s < 0.6 else 2) for s in sens})
    (tmp_path / "canciones").mkdir()
    return tmp_path



def test_id_de():
    assert publica.id_de("Mi Canción (en vivo)") == "mi-cancion-en-vivo"
    assert publica.id_de("¿?") == "cancion"



def test_publica_una_cancion(web):
    (web / "canciones" / "Mi canción.mp3").write_bytes(b"ID3 falso")
    assert publica.main([]) == 0
    assert not (web / "canciones" / "Mi canción.mp3").exists()  # se borra al terminar
    carpeta = web / "web" / "canciones" / "mi-cancion"
    datos = json.loads((carpeta / "datos.json").read_text())
    assert datos["titulo"] == "Mi canción"
    audios = {p["clave"]: p["audio"] for p in datos["pistas"]}
    assert audios["voz"] == "voz.mp3" and audios["bajo"] is None  # solo lo que suena
    assert (carpeta / "mezcla.mp3").read_bytes() == b"ID3 falso"
    partituras = json.loads((carpeta / "partitura-voz.json").read_text())
    assert set(partituras) == {"0.35", "0.50", "0.65"}
    assert partituras["0.50"]["cantidad"] == 1
    assert "La" in partituras["0.50"]["nombres"]["latina"]["abc"]
    assert "A" in partituras["0.50"]["nombres"]["inglesa"]["abc"].split("w:")[1]
    assert (carpeta / "voz-0.50.mid").read_bytes()[:4] == b"MThd"
    assert not (carpeta / "partitura-bateria.json").exists()
    indice = json.loads((web / "web" / "canciones.json").read_text())
    assert [c["id"] for c in indice["canciones"]] == ["mi-cancion"]




def test_una_cancion_rota_no_impide_las_demas(web, monkeypatch):
    (web / "canciones" / "buena.mp3").write_bytes(b"ID3")
    (web / "canciones" / "rota.mp3").write_bytes(b"ID3")
    original = publica.procesa

    def procesa(audio, titulo):
        if titulo == "rota":
            raise RuntimeError("audio ilegible")
        return original(audio, titulo)

    monkeypatch.setattr(publica, "procesa", procesa)
    assert publica.main([]) == 1
    assert (web / "canciones" / "rota.mp3").exists()  # se queda para reintentar
    assert (web / "web" / "canciones" / "buena" / "datos.json").exists()


def test_enlaces_de_dropbox_y_drive(monkeypatch, tmp_path):
    pedidas = []

    class Respuesta(io.BytesIO):
        headers = {"Content-Type": "audio/mpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(peticion, timeout):
        pedidas.append(peticion.full_url)
        return Respuesta(b"ID3")

    monkeypatch.setattr(publica.urllib.request, "urlopen", urlopen)
    publica.descarga("https://www.dropbox.com/s/abc/cancion.mp3?dl=0", tmp_path)
    publica.descarga("https://drive.google.com/file/d/XYZ_1-2/view?usp=sharing", tmp_path)
    assert pedidas == ["https://www.dropbox.com/s/abc/cancion.mp3?dl=1",
                       "https://drive.google.com/uc?export=download&id=XYZ_1-2"]





def _webm(destino):
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "libopus", str(destino)], check=True)


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="sin ffmpeg")
def test_grabacion_del_movil_en_webm(web):
    _webm(web / "canciones" / "Concierto en el salón.webm")
    assert publica.main([]) == 0
    datos = json.loads((web / "web" / "canciones" / "concierto-en-el-salon" / "datos.json").read_text())
    assert datos["titulo"] == "Concierto en el salón"
    assert (web / "web" / "canciones" / "concierto-en-el-salon" / "mezcla.mp3").exists()


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="sin ffmpeg")
def test_a_wav_convierte_webm(tmp_path):
    import subprocess
    import soundfile as sf
    webm = tmp_path / "g.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "libopus", str(webm)], check=True)
    wav = publica.a_wav(webm, tmp_path)
    datos, sr = sf.read(str(wav))
    assert sr == 44100 and datos.shape[1] == 2 and len(datos) > 40000


def test_a_wav_sin_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(publica.shutil, "which", lambda _: None)
    with pytest.raises(publica.ErrorPublica, match="ffmpeg"):
        publica.a_wav(tmp_path / "g.webm", tmp_path)
