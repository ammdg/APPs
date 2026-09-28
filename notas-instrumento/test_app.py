"""Pruebas del servidor con la separación, la transcripción y el pulso sustituidos."""

import io
import time

import numpy as np
import pytest

import app as aplicacion
import notas_instrumento as ni

SR = 8000


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(aplicacion, "TRABAJOS", tmp_path)
    aplicacion._estado.clear()
    n = SR * 4
    tono = 0.5 * np.sin(2 * np.pi * 220 * np.arange(n) / SR).astype("float32")
    silencio = np.zeros(n, dtype="float32")
    pistas = {nombre: np.stack([silencio, silencio], 1)
              for nombre in ["drums", "bass", "other", "vocals", "guitar", "piano"]}
    pistas["vocals"] = np.stack([tono, tono], 1)

    def separa(audio, progreso=None):
        progreso and progreso(1.0)
        return pistas, SR

    monkeypatch.setattr(ni, "separa", separa)
    monkeypatch.setattr(aplicacion, "pulsos_de", lambda audio: ([0.5, 1.0, 1.5, 2.0], 4.0))
    monkeypatch.setattr(ni, "transcribe",
                        lambda *a: ([ni.Nota(0.0, 0.5, 57, 0.9), ni.Nota(0.0, 0.5, 45, 0.3)],
                                    None))
    return aplicacion.app.test_client()


def sube(cliente, nombre="mi canción.mp3"):
    r = cliente.post("/api/canciones", data={"audio": (io.BytesIO(b"x"), nombre)},
                     content_type="multipart/form-data")
    return r


def espera_listo(cliente, id_):
    for _ in range(100):
        t = cliente.get(f"/api/canciones/{id_}").get_json()
        if t["estado"] in ("listo", "error"):
            return t
        time.sleep(0.05)
    raise AssertionError("no terminó")


def test_flujo_completo(cliente):
    r = sube(cliente)
    assert r.status_code == 202
    id_ = r.get_json()["id"]
    t = espera_listo(cliente, id_)
    assert t["estado"] == "listo" and t["titulo"] == "mi canción"
    suenan = {p["clave"]: p["suena"] for p in t["pistas"]}
    assert suenan == {"voz": True, "guitarra": False, "bajo": False, "piano": False,
                      "otros": False, "bateria": False}

    p = cliente.get(f"/api/canciones/{id_}/partitura/voz").get_json()
    assert p["cantidad"] == 1  # la voz es monofónica: se queda la nota más fuerte
    assert "A,4 z12" in p["abc"].split("K:C")[1] and p["tempo"] == 120

    assert cliente.get(f"/api/canciones/{id_}/audio/voz").status_code == 200
    assert cliente.get(f"/api/canciones/{id_}/audio/mezcla").status_code == 200
    assert cliente.get(f"/api/canciones/{id_}/audio/sin-voz").status_code == 200
    assert cliente.get(f"/api/canciones/{id_}/midi/voz").status_code == 200
    assert cliente.get(f"/api/canciones/{id_}/partitura/bateria").status_code == 400


def test_errores(cliente):
    assert sube(cliente, "notas.txt").status_code == 400
    assert cliente.get("/api/canciones/../../etc").status_code == 404
    assert cliente.get("/api/canciones/" + "a" * 32).status_code == 404
    id_ = sube(cliente).get_json()["id"]
    espera_listo(cliente, id_)
    assert cliente.get(f"/api/canciones/{id_}/audio/nada").status_code == 404
    assert cliente.get(f"/api/canciones/{id_}/partitura/voz?sensibilidad=2").status_code == 400
