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


def _p(fuente, fecha, nav):
    return {"fuente": fuente, "fecha": fecha, "nav": nav, "moneda": "EUR"}


def test_elegir_precio_exacto_si_cuadra():
    precio, aviso = p.elegir(_p("ft", "2026-11-19", 11.99), _p("quefondos", "2026-11-19", 11.989))
    assert precio["nav"] == 11.989 and aviso is None


def test_elegir_ft_si_no_cuadra():
    precio, aviso = p.elegir(_p("ft", "2026-11-19", 11.99), _p("quefondos", "2026-11-19", 1.199))
    assert precio["fuente"] == "ft" and aviso


def test_elegir_el_mas_reciente():
    assert p.elegir(_p("ft", "2026-11-20", 10), _p("quefondos", "2026-11-19", 10.0001))[0]["fuente"] == "ft"
    assert p.elegir(_p("ft", "2026-11-19", 10), _p("quefondos", "2026-11-20", 10.2))[0]["fuente"] == "quefondos"
    assert p.elegir(_p("ft", "2026-11-19", 10), _p("quefondos", "2026-11-20", 20))[0]["fuente"] == "ft"


def test_elegir_con_una_sola_fuente():
    assert p.elegir(None, _p("quefondos", "2026-11-19", 5))[0]["fuente"] == "quefondos"
    assert p.elegir(None, None)[0] is None


def test_elegir_entre_varias_precisas():
    ft = _p("ft", "2026-11-19", 12.59)
    qf = _p("quefondos", "2026-11-18", 12.4)
    yh = _p("yahoo", "2026-11-19", 12.591)
    assert p.elegir(ft, [qf, yh])[0]["fuente"] == "yahoo"
    assert p.elegir(ft, [_p("quefondos", "2026-11-19", 12.5912), yh])[0]["fuente"] == "quefondos"
    assert p.elegir(ft, [_p("yahoo", "2026-11-19", 99)])[0]["fuente"] == "ft"


def test_chart_yahoo():
    datos = {"chart": {"result": [{"meta": {"currency": "EUR"}, "timestamp": [1763337600, 1763510400, 1763596800],
                                   "indicators": {"quote": [{"close": [12.5, 12.590999603271484, None]}]}}]}}
    r = p.parsear_chart_yahoo(datos, "EUR")
    assert r["nav"] == 12.591 and r["fecha"] == "2025-11-19"
    with pytest.raises(p.SinPrecio):
        p.parsear_chart_yahoo(datos, "USD")


def test_historico_ft():
    tabla = ("<tr><td><span>Friday, November 20, 2026</span><span>Fri, Nov 20, 2026</span></td>"
             "<td>1</td><td>2</td><td>0.5</td><td>1,352.48</td><td>--</td></tr>"
             "<tr><td>Thursday, November 19, 2026</td><td>1</td><td>2</td><td>0.5</td><td>350.10</td></tr>")
    assert p.parsear_historico_ft(tabla) == [["2026-11-19", 350.1], ["2026-11-20", 1352.48]]


def test_cargar_historico_no_pisa_y_respeta_fecha():
    datos = {"fondos": {"A": {"nombre": "Uno", "historial": [["2026-11-19", 10.0]]}}}
    serie = [["2026-09-17", 8.0], ["2026-09-18", 9.0], ["2026-11-19", 10.3], ["2026-11-20", 10.5]]
    cambiado = p.cargar_historico(datos, [{"isin": "A", "nombre": "Uno"}], date(2026, 9, 18),
                                  lambda f: (serie, "yahoo"))
    assert cambiado
    assert datos["fondos"]["A"]["historial"] == [["2026-09-18", 9.0], ["2026-11-19", 10.0], ["2026-11-20", 10.5]]


FINECT = (
    '<script type="application/ld+json">{"@type":["Product"],"offers":{"@type":"Offer","price":"105.1118",'
    '"priceCurrency":"EUR"}}</script><script>window.x="{\\"lastQuote\\":{\\"datetime\\":\\"2026-11-19T00:00:00.000Z\\",'
    '\\"price\\":99.5},\\"totalNetAsset\\":1,\\"lastQuote\\":{\\"datetime\\":\\"2026-11-20T00:00:00.000Z\\",'
    '\\"price\\":105.1118,\\"change\\":0.26}}";</script>'
)


def test_finect():
    assert p.parsear_finect(FINECT) == {"fecha": "2026-11-20", "nav": 105.1118, "moneda": "EUR", "fuente": "finect"}
    with pytest.raises(p.SinPrecio):
        p.parsear_finect(FINECT.replace("105.1118,", "1,"))


def test_combinar_series_rellena_huecos():
    yahoo = [["2026-09-21", 100.0], ["2026-09-23", 101.0]]
    ft = [["2026-09-04", 99.0], ["2026-09-21", 100.1], ["2026-09-22", 100.5], ["2026-09-24", 300.0]]
    assert p.combinar_series(yahoo, ft) == [["2026-09-04", 99.0], ["2026-09-21", 100.0],
                                            ["2026-09-22", 100.5], ["2026-09-23", 101.0]]
