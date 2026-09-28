"""Pruebas sin red. Las filas imitan el formato del buscador de nasdaq.com; no son datos reales."""

import json

import requests

import universo as u

FILAS = [
    {"symbol": "AAA", "name": "Alfa Inc. Common Stock", "marketCap": "3,000,000,000,000.00", "country": "United States"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. Class A Common Stock", "marketCap": "1000000000000", "country": "United States"},
    {"symbol": "GOOG", "name": "Alphabet Inc. Class C Capital Stock", "marketCap": "900000000000", "country": "United States"},
    {"symbol": "BRK/B", "name": "Berkshire Hathaway Inc.", "marketCap": "950000000000", "country": "United States"},
    {"symbol": "TSM", "name": "Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares",
     "marketCap": "800000000000", "country": "Taiwan"},
    {"symbol": "BAC^K", "name": "Bank of America Preferred", "marketCap": "999000000000000", "country": "United States"},
    {"symbol": "ZZZ", "name": "Sin capitalización", "marketCap": "", "country": "United States"},
    {"symbol": "PEQ", "name": "Pequeña Corp. Common Stock", "marketCap": "1000", "country": "United States"},
]


def test_seleccionar_ordena_y_filtra():
    assert u.seleccionar(FILAS, 10) == ["AAA", "GOOGL", "BRK-B", "TSM", "PEQ"]
    assert u.seleccionar(FILAS, 2) == ["AAA", "GOOGL"]
    assert "TSM" not in u.seleccionar(FILAS, 10, solo_eeuu=True)


def test_empresa_sin_clase():
    assert u.empresa("Alphabet Inc. Class A Common Stock") == u.empresa("Alphabet Inc. Class C Capital Stock")
    assert u.empresa("Fox Corporation Class A Common Stock") != u.empresa("Alphabet Inc. Class A Common Stock")


def test_obtener_usa_nasdaq_y_guarda(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "descargar_nasdaq", lambda: FILAS)
    cache = tmp_path / "universo.json"
    tickers, fuente = u.obtener(3, False, cache, tmp_path / "no_existe.json")
    assert tickers == ["AAA", "GOOGL", "BRK-B"] and fuente.startswith("nasdaq.com")
    assert json.loads(cache.read_text())["tickers"] == tickers


def test_obtener_respaldos(tmp_path, monkeypatch):
    def falla():
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(u, "descargar_nasdaq", falla)
    respaldo = tmp_path / "sp500.json"
    respaldo.write_text(json.dumps({"tickers": ["X", "Y"]}))
    cache = tmp_path / "universo.json"
    assert u.obtener(500, False, cache, respaldo)[0] == ["X", "Y"]
    assert "S&P 500" in u.obtener(500, False, cache, respaldo)[1]
    cache.write_text(json.dumps({"fecha": "2030-01-01", "tickers": ["A", "B", "C"]}))
    tickers, fuente = u.obtener(2, False, cache, respaldo)
    assert tickers == ["A", "B"] and "2030-01-01" in fuente


def test_obtener_rechaza_lista_corta(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "descargar_nasdaq", lambda: FILAS)  # solo 5 acciones válidas
    respaldo = tmp_path / "sp500.json"
    respaldo.write_text(json.dumps({"tickers": ["X"]}))
    assert u.obtener(500, False, tmp_path / "c.json", respaldo)[0] == ["X"]


def test_respaldo_incluido_valido():
    d = json.loads((u.AQUI / "sp500_respaldo.json").read_text())
    assert len(d["tickers"]) > 490 and "BRK-B" in d["tickers"]
