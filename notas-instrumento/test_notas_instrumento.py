"""Pruebas sin modelos: la separación y la transcripción se sustituyen por funciones falsas."""

import csv
from pathlib import Path

import pytest

import notas_instrumento as ni
from notas_instrumento import Nota


def test_nombre_nota():
    assert ni.nombre_nota(60) == "Do4"
    assert ni.nombre_nota(61, "inglesa") == "C#4"
    assert ni.nombre_nota(21) == "La0"
    assert ni.nombre_nota(108) == "Do8"


def test_formatea_tiempo():
    assert ni.formatea_tiempo(75.5) == "1:15.500"


@pytest.mark.parametrize("texto,esperado", [
    ("Guitarra", "guitarra"), ("VOZ", "voz"), ("violín", "violín"), ("saxo", "saxofón"),
    ("teclado", "piano"), ("bass", "bajo"), ("sintetizador", "otros"),
])
def test_busca_instrumento(texto, esperado):
    assert ni.busca_instrumento(texto).nombre == esperado


def test_bateria_y_desconocido_dan_error():
    with pytest.raises(ni.ErrorNotas, match="batería"):
        ni.busca_instrumento("batería")
    with pytest.raises(ni.ErrorNotas, match="No conozco"):
        ni.busca_instrumento("gaita")


def test_filtra_registro():
    bajo = ni.INSTRUMENTOS["bajo"]
    notas = [Nota(0, 1, 20, 0.5), Nota(0, 1, 40, 0.5), Nota(0, 1, 80, 0.5)]
    assert [n.midi for n in ni.filtra_registro(notas, bajo)] == [40]


def test_monofoniza_gana_la_mas_fuerte():
    notas = [Nota(0.0, 1.0, 60, 0.9), Nota(0.0, 0.8, 72, 0.3),  # armónico: fuera
             Nota(0.9, 2.0, 62, 0.8)]  # solapa 0.1 s con la anterior, más débil: se recorta
    res = ni.monofoniza(notas)
    assert [(n.midi, n.inicio, n.fin) for n in res] == [(60, 0.0, 1.0), (62, 1.0, 2.0)]


def test_monofoniza_recorta_la_anterior_si_la_nueva_es_mas_fuerte():
    res = ni.monofoniza([Nota(0.0, 1.0, 60, 0.3), Nota(0.5, 1.5, 64, 0.9)])
    assert [(n.midi, n.inicio, n.fin) for n in res] == [(60, 0.0, 0.5), (64, 0.5, 1.5)]


def test_procesa_separa_la_pista_del_instrumento(tmp_path, monkeypatch):
    audio = tmp_path / "cancion.mp3"
    audio.write_bytes(b"")
    llamadas = {}

    def separa_falso(entrada, pista, destino):
        llamadas["pista"] = pista
        destino.write_bytes(b"wav")
        return destino

    def transcribe_falso(entrada, instrumento, sensibilidad, duracion_min_ms):
        llamadas["transcrito"] = entrada.name
        return [Nota(1.0, 1.5, 64, 0.7), Nota(0.0, 0.5, 60, 0.8), Nota(0.0, 0.5, 100, 0.8)], None

    monkeypatch.setattr(ni, "separa_pista", separa_falso)
    monkeypatch.setattr(ni, "transcribe", transcribe_falso)
    salida = tmp_path / "salida"
    notas = ni.procesa(audio, ni.INSTRUMENTOS["guitarra"], separar=True, salida=salida,
                       notacion="latina", sensibilidad=0.5, duracion_min_ms=100,
                       guardar_pista=True)

    assert llamadas == {"pista": "guitar", "transcrito": "cancion_guitarra.wav"}
    assert [n.midi for n in notas] == [60, 64]  # ordenadas y sin la nota fuera de registro
    with (salida / "cancion_guitarra.csv").open() as f:
        filas = list(csv.DictReader(f))
    assert [f["nota"] for f in filas] == ["Do4", "Mi4"]
    assert (salida / "cancion_guitarra.mid").stat().st_size > 0
    assert (salida / "cancion_guitarra.wav").exists()


def test_main_sin_separar(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "solo.wav"
    audio.write_bytes(b"")
    monkeypatch.setattr(ni, "separa_pista", lambda *a: pytest.fail("no debía separar"))
    monkeypatch.setattr(ni, "transcribe", lambda *a: ([Nota(0.0, 0.5, 57, 0.8)], None))
    assert ni.main([str(audio), "-i", "piano", "--sin-separar", "-o", str(tmp_path / "s"),
                    "--notacion", "inglesa"]) == 0
    assert "A3" in capsys.readouterr().out


def test_main_errores(tmp_path, capsys):
    assert ni.main([str(tmp_path / "no.mp3"), "-i", "piano"]) == 1
    assert "No encuentro" in capsys.readouterr().err
