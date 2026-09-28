"""Pruebas sin red. Las filas imitan el formato del buscador de nasdaq.com; no son datos reales."""

import json
from datetime import date, datetime, timezone

import universo as u

FILAS = [
    {"symbol": "AAA", "name": "Alfa Inc. Common Stock", "marketCap": "3,000,000,000,000.00", "lastsale": "$300.00",
     "country": "United States"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. Class A Common Stock", "marketCap": "1000000000000",
     "lastsale": "$100.00", "country": "United States"},
    {"symbol": "GOOG", "name": "Alphabet Inc. Class C Capital Stock", "marketCap": "900000000000",
     "lastsale": "$100.00", "country": "United States"},
    {"symbol": "BRK/B", "name": "Berkshire Hathaway Inc.", "marketCap": "950000000000", "lastsale": "$500.00",
     "country": "United States"},
    {"symbol": "TSM", "name": "Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares",
     "marketCap": "800000000000", "lastsale": "$200.00", "country": "Taiwan"},
    {"symbol": "BAC^K", "name": "Bank of America Preferred", "marketCap": "999000000000000", "lastsale": "$25",
     "country": "United States"},
    {"symbol": "ZZZ", "name": "Sin capitalización", "marketCap": "", "lastsale": "$1", "country": "United States"},
    {"symbol": "PEQ", "name": "Pequeña Corp. Common Stock", "marketCap": "1000", "lastsale": "$1",
     "country": "United States"},
]


def test_candidatas_ordena_filtra_y_una_por_empresa():
    c = u.candidatas(FILAS)
    assert [x["ticker"] for x in c] == ["AAA", "GOOGL", "BRK-B", "TSM", "PEQ"]
    assert c[0]["acciones"] == 10_000_000_000  # 3 billones / 300 $
    assert "TSM" not in [x["ticker"] for x in u.candidatas(FILAS, solo_eeuu=True)]


def test_empresa_sin_clase():
    assert u.empresa("Alphabet Inc. Class A Common Stock") == u.empresa("Alphabet Inc. Class C Capital Stock")
    assert u.empresa("Fox Corporation Class A Common Stock") != u.empresa("Alphabet Inc. Class A Common Stock")


def test_ranking_con_cierre_de_la_fecha():
    c = u.candidatas(FILAS)
    # El viernes GOOGL cerraba a 400 $: 10.000 M acciones × 400 = 4 billones, por delante de AAA.
    cierres = {"AAA": 300.0, "GOOGL": 400.0, "BRK-B": 500.0, "TSM": 200.0}
    top = u.ranking(c, cierres, 3)
    assert [x["ticker"] for x in top] == ["GOOGL", "AAA", "BRK-B"]
    assert top[0]["capitalizacion"] == 4e12
    assert "PEQ" not in [x["ticker"] for x in u.ranking(c, cierres, 10)]  # sin cierre: fuera


def test_ultima_sesion_cerrada():
    lunes_tarde = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
    assert u.ultima_sesion_cerrada(lunes_tarde) == date(2026, 9, 25)  # viernes
    martes = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
    assert u.ultima_sesion_cerrada(martes) == date(2026, 9, 28)
    # Lunes 01:00 UTC todavía es domingo en Nueva York -> viernes
    assert u.ultima_sesion_cerrada(datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)) == date(2026, 9, 25)


def test_generar_y_cargar(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "descargar_nasdaq", lambda: FILAS)
    monkeypatch.setattr(u, "cierres_del_dia", lambda t, f: {"AAA": 300.0, "GOOGL": 400.0, "BRK-B": 500.0})
    d = u.generar(3, date(2026, 9, 25), solo_eeuu=False)
    assert [a["ticker"] for a in d["acciones"]] == ["GOOGL", "AAA", "BRK-B"]
    assert d["fecha_referencia"] == "2026-09-25" and d["acciones"][0]["posicion"] == 1
    f = tmp_path / "u.json"
    f.write_text(json.dumps(d))
    assert u.cargar(f) == (["GOOGL", "AAA", "BRK-B"], "2026-09-25")


def test_generar_falla_si_faltan_cierres(monkeypatch):
    monkeypatch.setattr(u, "descargar_nasdaq", lambda: FILAS)
    monkeypatch.setattr(u, "cierres_del_dia", lambda t, f: {})  # p. ej. día festivo
    try:
        u.generar(3, date(2026, 9, 25), solo_eeuu=False)
        assert False, "debería fallar"
    except SystemExit as e:
        assert "festivo" in str(e)
