"""Pruebas sin red. Los HTML son sintéticos: imitan la estructura que se espera de FT y Quefondos,
no son capturas reales, así que no garantizan que las webs sigan teniendo ese formato."""

from datetime import date

import pytest

import precios as p

FT = """<html><body>
<ul class="mod-tearsheet-overview__quote__bar">
 <li><span class="mod-ui-data-list__label">Price (EUR)</span>
     <span class="mod-ui-data-list__value">44,769.80</span></li>
 <li><span class="mod-ui-data-list__label">Today's Change</span>
     <span class="mod-ui-data-list__value">0.10 / 0.00%</span></li>
</ul>
<div class="mod-disclaimer">Data delayed at least 15 minutes, as of Nov 20 2026.</div>
</body></html>"""

QF = """<html><body><script>var x="Valor liquidativo: 9,99";</script>
<p><span>Valor liquidativo:</span> <span>12,591 EUR</span></p>
<p><span>Fecha:</span> <span>20/11/2026</span></p></body></html>"""


def test_ft():
    r = p.parsear_ft(FT)
    assert r == {"fecha": "2026-11-20", "nav": 44769.8, "moneda": "EUR", "fuente": "ft"}


def test_ft_sin_datos():
    with pytest.raises(p.SinPrecio):
        p.parsear_ft("<html>nada</html>")


def test_quefondos_ignora_scripts():
    r = p.parsear_quefondos(QF)
    assert r["nav"] == 12.591 and r["fecha"] == "2026-11-20" and r["moneda"] == "EUR"


def test_numeros_y_fechas():
    assert p.numero("1.234,56", ",") == 1234.56
    assert p.numero("1,234.56", ".") == 1234.56
    assert p.fecha_ft("Sep 3, 2026") == date(2026, 9, 3)


def test_historial_corrige_y_ordena():
    h = [["2026-11-18", 10.0]]
    assert p.anadir(h, "2026-11-20", 11.0)
    assert p.anadir(h, "2026-11-19", 10.5)
    assert not p.anadir(h, "2026-11-20", 11.0)
    assert p.anadir(h, "2026-11-20", 11.1)
    assert h == [["2026-11-18", 10.0], ["2026-11-19", 10.5], ["2026-11-20", 11.1]]


def test_actualizar_usa_reserva_y_anota_errores():
    fondos = [{"isin": "A", "nombre": "Uno"}, {"isin": "B", "nombre": "Dos"}]
    respuestas = {"A": ({"fecha": "2026-11-20", "nav": 5.0, "moneda": "EUR", "fuente": "quefondos"}, ["ft: x"]),
                  "B": (None, ["ft: x", "quefondos: y"])}
    datos = {}
    cambiado, avisos = p.actualizar(datos, fondos, lambda f: respuestas[f["isin"]])
    assert cambiado and len(avisos) == 1
    assert datos["fondos"]["A"]["historial"] == [["2026-11-20", 5.0]]
    assert datos["fondos"]["A"]["fuente"] == "quefondos"
    assert "error" in datos["fondos"]["B"]
