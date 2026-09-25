"""Pruebas sin red: Google Flights se sustituye por un buscador falso."""

import json
from datetime import date, datetime

import pytest

import vuelos_baratos as vb

I1, I2, V1, V2 = date(2027, 6, 19), date(2027, 6, 20), date(2027, 6, 26), date(2027, 6, 27)
AHORA = datetime(2026, 9, 24, 7, 15)


def d(f, precio):
    return vb.Vuelo(f, "08:00", "09:10", float(precio), "Aerolínea")


def e(f, precio, escala="FRA", espera=80):
    return vb.Vuelo(f, "07:00", "12:30", float(precio), "Otra", escala, espera)


# (origen, destino, fecha) -> Opciones(directo, escala)
OPCIONES = {
    # Lisboa: directo 45+50=95; con escala no mejora.
    ("MAD", "LIS", I1): vb.Opciones(d(I1, 60), e(I1, 70)), ("MAD", "LIS", I2): vb.Opciones(d(I2, 45)),
    ("LIS", "MAD", V1): vb.Opciones(d(V1, 70)), ("LIS", "MAD", V2): vb.Opciones(d(V2, 50)),
    # Oslo: sin directo; con escala 60+55=115.
    ("MAD", "OSL", I1): vb.Opciones(None, e(I1, 60, "CPH", 95)), ("OSL", "MAD", V2): vb.Opciones(None, e(V2, 55)),
    # París: directo 80+90=170; ida con escala 40 -> 40+90=130 (más barato y bajo el límite).
    ("MAD", "CDG", I1): vb.Opciones(d(I1, 80), e(I1, 40, "BCN", 60)), ("CDG", "MAD", V1): vb.Opciones(d(V1, 90)),
    # Nueva York: caro.
    ("MAD", "JFK", I1): vb.Opciones(d(I1, 420)), ("JFK", "MAD", V2): vb.Opciones(d(V2, 380)),
    # Marrakech: sin vuelta.
    ("MAD", "RAK", I1): vb.Opciones(d(I1, 30)),
}


def falso(o, dst, f, cfg):
    return OPCIONES.get((o, dst, f), vb.Opciones())


def cfg(**kw):
    base = dict(origen="MAD", fechas_ida=[I1, I2], fechas_vuelta=[V1, V2], pasajeros=4,
                precio_max_persona=150, incluir_escalas=True, escala_max_minutos=180,
                mostrar_por_encima=5, pausa_segundos=0,
                destinos={"LIS": "Lisboa", "OSL": "Oslo", "CDG": "París", "JFK": "Nueva York",
                          "RAK": "Marrakech", "XXX": "Sin vuelos"})
    return vb.Config(**{**base, **kw})


def por_destino(resultados):
    return {r.destino: r for r in resultados}


def test_combinaciones_directas_y_con_escala():
    r = por_destino(vb.buscar(cfg(), falso)[0])
    assert r["LIS"].directo.precio == 95 and r["LIS"].con_escala is None  # la escala no mejora
    assert r["LIS"].directo.ida.fecha == I2 and r["LIS"].directo.vuelta.fecha == V2
    assert r["OSL"].directo is None and r["OSL"].con_escala.precio == 115
    assert r["CDG"].directo.precio == 170 and r["CDG"].con_escala.precio == 130
    assert r["CDG"].con_escala.ida.escala == "BCN" and r["CDG"].con_escala.vuelta.escala is None
    assert r["RAK"].mejor is None and r["XXX"].ida is None


def test_no_busca_vuelta_si_la_ida_ya_es_cara():
    pedidas = []

    def contar(o, dst, f, c):
        pedidas.append((o, dst, f))
        return falso(o, dst, f, c)

    vb.buscar(cfg(mostrar_por_encima=0), contar)
    assert not [p for p in pedidas if p[0] == "JFK"]
    assert ("OSL", "MAD", V1) in pedidas  # la ida con escala cuenta para seguir buscando


def test_mensaje_con_dos_apartados():
    resultados, avisos = vb.buscar(cfg(), falso)
    texto = vb.formatear_mensaje(cfg(), resultados, {"LIS": 120.0, "CDG+escala": 120.0}, avisos, AHORA)
    directos = texto.index("✅ DIRECTOS")
    directos_ref = texto.index("Siguientes directos más baratos (por encima del límite):")
    escalas = texto.index("🔁 CON 1 ESCALA (máx. 3h)")
    assert directos < texto.index("Lisboa (LIS): 95 €/pers · 380 € total 📉 antes 120 €") < directos_ref
    assert directos_ref < texto.index("Nueva York (JFK): 800 €") < escalas
    assert escalas < texto.index("Oslo (OSL): 115 €/pers · 460 € total 🆕")
    assert escalas < texto.index("París (CDG): 130 €/pers · 520 € total 📈 antes 120 €")
    assert "ida sáb 19/06 07:00-12:30 Otra (escala CPH 1h35)" in texto
    assert "vuelta sáb 26/06 08:00-09:10 Aerolínea" in texto  # tramo directo dentro de una combinación
    assert "París (CDG): 170" not in texto  # el directo caro no se repite si hay opción barata con escala
    assert "Marrakech" not in texto


def test_escalas_por_encima_del_limite_se_muestran_como_referencia():
    # Con límite 100 no hay ninguna opción con escala barata, pero se enseñan las siguientes.
    c = cfg(precio_max_persona=100)
    resultados, _ = vb.buscar(c, falso)
    texto = vb.formatear_mensaje(c, resultados, {}, [], AHORA)
    escalas = texto.index("🔁 CON 1 ESCALA")
    ref = texto.index("Siguientes con escala más baratos (por encima del límite):")
    assert escalas < texto.index("Ninguno hoy.", escalas) < ref
    assert ref < texto.index("Oslo (OSL): 115 €") < texto.index("París (CDG): 130 €")
    assert "Lisboa (LIS): 95" in texto and texto.count("Lisboa") == 1  # barato directo: no se repite


def test_referencia_limitada_a_mostrar_por_encima():
    resultados, _ = vb.buscar(cfg(precio_max_persona=10), falso)
    texto = vb.formatear_mensaje(cfg(precio_max_persona=10, mostrar_por_encima=1), resultados, {}, [], AHORA)
    assert "Oslo (OSL)" in texto and "París (CDG): 130" not in texto


def test_sin_escalas_no_hay_apartado():
    c = cfg(incluir_escalas=False)
    resultados, _ = vb.buscar(c, falso)
    texto = vb.formatear_mensaje(c, resultados, {}, [], AHORA)
    assert "CON 1 ESCALA" not in texto


def test_sin_baratos():
    c = cfg(precio_max_persona=50)
    resultados, _ = vb.buscar(c, falso)
    texto = vb.formatear_mensaje(c, resultados, {}, [], AHORA)
    assert texto.count("Ninguno hoy.") == 2


def test_interrumpe_si_google_bloquea():
    llamadas = []

    def roto(o, dst, f, c):
        llamadas.append(1)
        raise RuntimeError("429")

    _, avisos = vb.buscar(cfg(), roto)
    assert len(llamadas) == vb.MAX_ERRORES_SEGUIDOS
    assert avisos and "interrumpida" in avisos[0]


def test_ejecutar_guarda_historial_y_notifica(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(vb, "notificar", enviados.append)
    h = tmp_path / "historial.json"
    assert vb.ejecutar(cfg(), h, falso) == 0
    assert json.loads(h.read_text()) == {"LIS": 95.0, "OSL+escala": 115.0, "CDG": 170.0,
                                         "CDG+escala": 130.0, "JFK": 800.0}
    vb.ejecutar(cfg(), h, falso)
    assert "🆕" not in enviados[1]


def test_ejecutar_falla_si_no_hay_ningun_vuelo(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "notificar", lambda t: [])
    h = tmp_path / "historial.json"
    h.write_text('{"LIS": 90}')
    assert vb.ejecutar(cfg(), h, lambda *a: vb.Opciones()) == 1
    assert json.loads(h.read_text()) == {"LIS": 90}


def test_mensaje_largo_se_recorta():
    muchos = {f"X{i:02d}": f"Destino {i}" for i in range(90)}
    c = cfg(destinos=muchos)
    resultados = [vb.Resultado(k, v, ida_directo=d(I1, 10 + i), vuelta_directo=d(V1, 10))
                  for i, (k, v) in enumerate(muchos.items())]
    texto = vb.formatear_mensaje(c, resultados, {}, [], AHORA)
    assert len(texto) <= vb.LIMITE_TELEGRAM and texto.endswith("(lista recortada)")


def test_config_real_valida():
    c = vb.Config.desde_archivo(vb.AQUI / "config.json")
    assert c.origen == "MAD" and c.pasajeros == 4 and c.precio_max_persona == 150
    assert c.incluir_escalas and c.escala_max_minutos == 180
    assert c.fechas_ida == [I1, I2] and c.fechas_vuelta == [V1, V2]
    assert "BCN" not in c.destinos and "PMI" not in c.destinos


# --------------------------------------------------------------- lectura de Google Flights (simulada)

import fast_flights
import fast_flights.parser
from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight


def _google(monkeypatch, html, parse):
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: html)
    monkeypatch.setattr(fast_flights.parser, "parse", parse)


def _tramo(o, dst, h1, h2, fecha=(2027, 6, 19)):
    return SingleFlight(Airport(o, o), Airport(dst, dst), SimpleDatetime(fecha, h1), SimpleDatetime(fecha, h2),
                        60, "A320")


def _vuelo(precio, *tramos):
    return Flights("x", precio, ["Aerolínea"], list(tramos), CarbonEmission(0, 0))


def test_separa_directos_y_escalas_y_filtra_la_espera(monkeypatch):
    pedidas = []

    def fetch(q):
        pedidas.append(q)
        return '<script class="ds:1"></script>'

    monkeypatch.setattr(fast_flights, "fetch_flights_html", fetch)
    monkeypatch.setattr(fast_flights.parser, "parse", lambda h: [
        _vuelo(90, _tramo("MAD", "OSL", (8, 0), (11, 30))),                                  # directo
        _vuelo(70, _tramo("MAD", "CPH", (7, 0), (9, 50)), _tramo("CPH", "OSL", (11, 25), (12, 35))),  # 1h35
        _vuelo(40, _tramo("MAD", "FRA", (6, 0), (8, 30)), _tramo("FRA", "OSL", (12, 0), (14, 0))),    # 3h30: fuera
        _vuelo(30, _tramo("MAD", "A", (6, 0), (7, 0)), _tramo("A", "B", (7, 30), (8, 0)),
               _tramo("B", "OSL", (8, 30), (9, 0))),                                                 # 2 escalas
    ])
    op = vb.buscar_opciones("MAD", "OSL", I1, cfg())
    assert op.directo.precio == 90 and op.directo.escala is None
    assert (op.escala.precio, op.escala.escala, op.escala.espera_min) == (70, "CPH", 95)
    info = pedidas[0].pb()
    assert info.data[0].max_stops == 1 and info.data[0].max_layover_minutes == 180
    assert info.hide_separate_and_self_transfer


def test_sin_escalas_pide_solo_directos(monkeypatch):
    pedidas = []
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: pedidas.append(q) or "ds:1")
    monkeypatch.setattr(fast_flights.parser, "parse", lambda h: [
        _vuelo(70, _tramo("MAD", "CPH", (7, 0), (9, 50)), _tramo("CPH", "OSL", (11, 25), (12, 35)))])
    op = vb.buscar_opciones("MAD", "OSL", I1, cfg(incluir_escalas=False))
    assert op == vb.Opciones()
    assert pedidas[0].pb().data[0].max_stops == 0


def test_dia_sin_vuelos_no_es_error(monkeypatch):
    # Así falló en la primera ejecución real: TypeError("'NoneType' object is not subscriptable").
    def sin_lista(html):
        raise TypeError("'NoneType' object is not subscriptable")

    _google(monkeypatch, '<script class="ds:1">...</script>', sin_lista)
    assert vb.buscar_opciones("MAD", "VGO", I1, cfg()) == vb.Opciones()


def test_pagina_sin_datos_si_es_error(monkeypatch):
    _google(monkeypatch, "<html>captcha</html>", lambda h: [])
    with pytest.raises(RuntimeError):
        vb.buscar_opciones("MAD", "LIS", I1, cfg())


def test_muchos_destinos_sin_vuelos_no_interrumpen():
    destinos = {f"X{i:02d}": "Sin vuelos" for i in range(20)} | {"LIS": "Lisboa"}
    resultados, avisos = vb.buscar(cfg(destinos=destinos), falso)
    assert avisos == []
    assert por_destino(resultados)["LIS"].directo.precio == 95


def _itinerario_google(precio, destino, hora):
    tramo = [None] * 22
    tramo[3], tramo[4], tramo[5], tramo[6] = "MAD", "Madrid", destino, destino
    tramo[8], tramo[10], tramo[11], tramo[17] = [hora], [hora + 2], 120, "A320"
    tramo[20], tramo[21] = [2027, 6, 19], [2027, 6, 19]
    vuelo = [None] * 23
    vuelo[0], vuelo[1], vuelo[2] = "x", ["Aerolínea"], [tramo]
    vuelo[22] = [None] * 9
    return [vuelo, [[None, precio]]]


def _pagina_google(mejores, otros):
    payload = [None] * 8
    payload[2] = [mejores] if mejores is not None else None
    payload[3] = [otros] if otros is not None else None
    payload[7] = [None, [[], []]]
    datos = json.dumps(payload)
    return f'<html><script class="ds:1" nonce="n">AF_initDataCallback({{key: \'ds:1\', data:{datos}, sideChannel: {{}}}});</script></html>'


def test_lee_tambien_las_mejores_opciones(monkeypatch):
    # fast-flights 3.1.0 solo lee payload[3]; lo más barato suele estar en payload[2] ("mejores opciones").
    html = _pagina_google(
        [_itinerario_google(32, "OPO", 6), [["roto"], None]],  # el segundo, sin precio, se descarta
        [_itinerario_google(44, "OPO", 11)],
    )
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: html)
    op = vb.buscar_opciones("MAD", "OPO", I1, cfg())
    assert (op.directo.precio, op.directo.salida) == (32, "06:00")


def test_solo_mejores_opciones(monkeypatch):
    html = _pagina_google([_itinerario_google(50, "OPO", 9)], None)
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: html)
    assert vb.buscar_opciones("MAD", "OPO", I1, cfg()).directo.precio == 50


def test_pagina_sin_ninguna_lista_es_sin_vuelos(monkeypatch):
    html = _pagina_google(None, None)
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: html)
    assert vb.buscar_opciones("MAD", "HAJ", I1, cfg()) == vb.Opciones()
