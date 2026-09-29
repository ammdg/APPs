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
    monkeypatch.setattr(publica, "CLAVE", tmp_path / "web" / "clave.json")
    monkeypatch.setattr(publica, "ITERACIONES_CLAVE", 1000)  # que las pruebas vayan rápido
    for var in ("NOTAS_USUARIO", "NOTAS_PASSWORD", "MAPA_USUARIO", "MAPA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

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


def test_cifra_y_descifra():
    clave = publica.deriva_clave("ana", "x", b"sal", 1000)
    c = publica.cifra(b"hola", clave)
    assert c != b"hola" and publica.descifra(c, clave) == b"hola"


def test_id_de():
    assert publica.id_de("Mi Canción (en vivo)") == "mi-cancion-en-vivo"
    assert publica.id_de("¿?") == "cancion"


def test_credenciales_usa_las_del_mapa_si_no_hay_propias(monkeypatch):
    monkeypatch.delenv("NOTAS_USUARIO", raising=False)
    monkeypatch.setenv("MAPA_USUARIO", "u")
    monkeypatch.setenv("MAPA_PASSWORD", "p")
    assert publica.credenciales() == ("u", "p")
    monkeypatch.setenv("NOTAS_USUARIO", "n")
    monkeypatch.setenv("NOTAS_PASSWORD", "q")
    assert publica.credenciales() == ("n", "q")


def test_publica_sin_cifrar(web):
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


def test_poner_contrasena_cifra_lo_ya_publicado(web, monkeypatch):
    (web / "canciones" / "a.mp3").write_bytes(b"ID3 a")
    publica.main([])
    monkeypatch.setenv("NOTAS_USUARIO", "ana")
    monkeypatch.setenv("NOTAS_PASSWORD", "secreta")
    assert publica.main(["--solo-indice"]) == 0

    info = json.loads((web / "web" / "clave.json").read_text())
    import base64
    clave = publica.deriva_clave("ana", "secreta", base64.b64decode(info["sal"]), info["iteraciones"])
    mezcla = web / "web" / "canciones" / "a" / "mezcla.mp3"
    assert mezcla.read_bytes() != b"ID3 a"
    assert publica.descifra(mezcla.read_bytes(), clave) == b"ID3 a"
    indice = json.loads(publica.descifra((web / "web" / "canciones.json").read_bytes(), clave))
    assert indice["canciones"][0]["id"] == "a"

    # Una canción nueva sale cifrada con la misma clave.
    (web / "canciones" / "b.mp3").write_bytes(b"ID3 b")
    assert publica.main([]) == 0
    datos = publica.descifra((web / "web" / "canciones" / "b" / "datos.json").read_bytes(), clave)
    assert json.loads(datos)["titulo"] == "b"


def test_contrasena_cambiada_o_quitada_da_error_claro(web, monkeypatch, capsys):
    monkeypatch.setenv("NOTAS_USUARIO", "ana")
    monkeypatch.setenv("NOTAS_PASSWORD", "secreta")
    assert publica.main(["--solo-indice"]) == 0
    monkeypatch.setenv("NOTAS_PASSWORD", "otra")
    assert publica.main(["--solo-indice"]) == 1
    assert "no coinciden" in capsys.readouterr().err
    monkeypatch.delenv("NOTAS_USUARIO")
    monkeypatch.delenv("NOTAS_PASSWORD")
    assert publica.main(["--solo-indice"]) == 1
    assert "está cifrada" in capsys.readouterr().err


def test_una_cancion_rota_no_impide_las_demas(web, monkeypatch):
    (web / "canciones" / "buena.mp3").write_bytes(b"ID3")
    (web / "canciones" / "rota.mp3").write_bytes(b"ID3")
    original = publica.procesa

    def procesa(audio, titulo, clave):
        if titulo == "rota":
            raise RuntimeError("audio ilegible")
        return original(audio, titulo, clave)

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


def _con_clave(web, monkeypatch):
    import base64
    monkeypatch.setenv("NOTAS_USUARIO", "ana")
    monkeypatch.setenv("NOTAS_PASSWORD", "secreta")
    publica.main(["--solo-indice"])
    info = json.loads((web / "web" / "clave.json").read_text())
    return publica.deriva_clave("ana", "secreta", base64.b64decode(info["sal"]), info["iteraciones"])


def _grabacion(clave, titulo, ext, audio):
    cabecera = json.dumps({"titulo": titulo, "ext": ext}).encode()
    return publica.cifra(cabecera + b"\n" + audio, clave)


def _wav(segundos=1.0):
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.zeros((int(SR * segundos), 2), dtype="float32"), SR, format="WAV")
    return buf.getvalue()


def test_grabacion_cifrada_del_movil(web, monkeypatch):
    clave = _con_clave(web, monkeypatch)
    entrada = web / "canciones" / "grabacion-20260929-120000.cif"
    entrada.write_bytes(_grabacion(clave, "Concierto  en el salón", ".wav", _wav()))
    assert publica.main([]) == 0
    assert not entrada.exists()
    datos = json.loads(publica.descifra(
        (web / "web" / "canciones" / "concierto-en-el-salon" / "datos.json").read_bytes(), clave))
    assert datos["titulo"] == "Concierto en el salón"


def test_grabacion_con_otra_clave_no_se_pierde(web, monkeypatch, capsys):
    _con_clave(web, monkeypatch)
    otra = publica.deriva_clave("ana", "otra", b"x" * 16, 1000)
    entrada = web / "canciones" / "g.cif"
    entrada.write_bytes(_grabacion(otra, "t", ".wav", _wav()))
    assert publica.main([]) == 1
    assert entrada.exists()
    assert "No se pudo descifrar" in capsys.readouterr().err


def test_grabacion_sin_cifrado_en_la_web_da_error(web, capsys):
    (web / "canciones" / "g.cif").write_bytes(b"cualquier cosa")
    assert publica.main([]) == 1
    assert "no tiene usuario y contraseña" in capsys.readouterr().err


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
