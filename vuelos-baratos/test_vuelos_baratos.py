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


def b(precio, ida=I1, vuelta=V1, escala_ida=None, escala_vuelta=None, aerolinea="Aerolínea"):
    """Billete de ida y vuelta como lo devuelve buscar_billete (de la vuelta solo se sabe la fecha)."""
    v_ida = vb.Vuelo(ida, "08:00", "09:10", float(precio), aerolinea, escala_ida, 95 if escala_ida else None)
    return vb.Combinacion(v_ida, vb.Vuelo(vuelta, "", "", 0.0, aerolinea, escala_vuelta), billete_unico=True)


# (destino, fecha ida, fecha vuelta, con escalas) -> billete de ida y vuelta más barato
BILLETES = {
    # Lisboa: directo 120 o 95 según fechas; con escala 110 no mejora.
    ("LIS", I1, V1, False): b(120), ("LIS", I2, V2, False): b(95, I2, V2), ("LIS", I1, V1, True): b(110, escala_ida="OPO"),
    # Oslo: sin directo; con escala 115.
    ("OSL", I1, V2, True): b(115, I1, V2, escala_ida="CPH"),
    # París: directo 170; con escala en la vuelta 130.
    ("CDG", I1, V1, False): b(170), ("CDG", I1, V1, True): b(130, escala_vuelta="?"),
    # Nueva York: caro.
    ("JFK", I1, V2, False): b(800, I1, V2),
    # Berlín (caso real): Iberia ida y vuelta 137 € (en solo idas salía 78 + 73 = 151 €).
    ("BER", I1, V1, False): b(137, aerolinea="Iberia"),
}


def falso(o, dst, ida, vuelta, escalas, c):
    return BILLETES.get((dst, ida, vuelta, escalas))


def cfg(**kw):
    base = dict(origen="MAD", fechas_ida=[I1, I2], fechas_vuelta=[V1, V2], pasajeros=4,
                precio_max_persona=150, incluir_escalas=True, escala_max_minutos=180,
                mostrar_por_encima=5, pausa_segundos=0,
                destinos={"LIS": "Lisboa", "OSL": "Oslo", "CDG": "París", "JFK": "Nueva York",
                          "BER": "Berlín", "RAK": "Marrakech", "XXX": "Sin vuelos"})
    return vb.Config(**{**base, **kw})


def por_destino(resultados):
    return {r.destino: r for r in resultados}


def test_siempre_billete_de_ida_y_vuelta():
    r = por_destino(vb.buscar(cfg(), falso)[0])
    assert r["LIS"].directo.precio == 95 and r["LIS"].con_escala is None  # la escala no mejora
    assert r["LIS"].directo.ida.fecha == I2 and r["LIS"].directo.vuelta.fecha == V2
    assert r["OSL"].directo is None and r["OSL"].con_escala.precio == 115
    assert r["CDG"].directo.precio == 170 and r["CDG"].con_escala.precio == 130
    assert r["BER"].directo.precio == 137
    assert all(c.billete_unico for x in r.values() for c in (x.directo, x.con_escala) if c)
    assert r["RAK"].mejor is None and r["XXX"].mejor is None


def test_consulta_todas_las_fechas_directo_y_con_escala():
    pedidas = []

    def contar(o, dst, i, v, escalas, c):
        pedidas.append((o, dst, i, v, escalas))
        return falso(o, dst, i, v, escalas, c)

    vb.buscar(cfg(), contar)
    assert len(pedidas) == 7 * 4 * 2 and all(p[0] == "MAD" for p in pedidas)
    assert ("MAD", "BER", I2, V1, True) in pedidas
    pedidas.clear()
    vb.buscar(cfg(incluir_escalas=False), contar)
    assert len(pedidas) == 7 * 4 and not any(p[4] for p in pedidas)


def test_mensaje_con_dos_apartados():
    resultados, avisos = vb.buscar(cfg(), falso)
    texto = vb.formatear_mensaje(cfg(), resultados, {"iv:LIS": 120.0, "iv:CDG+escala": 120.0}, avisos, AHORA)
    assert "5 de 7 destinos con billete de ida y vuelta" in texto
    directos = texto.index("✅ DIRECTOS")
    directos_ref = texto.index("Siguientes directos más baratos (por encima del límite):")
    escalas = texto.index("🔁 CON 1 ESCALA (máx. 3h)")
    assert directos < texto.index("Lisboa (LIS): 95 €/pers · 380 € total 📉 antes 120 €") < directos_ref
    assert directos < texto.index("Berlín (BER): 137 €/pers · 548 € total 🆕") < directos_ref
    assert directos_ref < texto.index("Nueva York (JFK): 800 €") < escalas
    assert escalas < texto.index("Oslo (OSL): 115 €/pers · 460 € total 🆕")
    assert escalas < texto.index("París (CDG): 130 €/pers · 520 € total 📈 antes 120 €")
    assert "ida sáb 19/06 08:00-09:10 Aerolínea (escala CPH 1h35) · vuelta dom 27/06\n" in texto
    assert "ida sáb 19/06 08:00-09:10 Aerolínea · vuelta sáb 26/06 (con escala)\n" in texto  # París
    assert "París (CDG): 170" not in texto  # el directo caro no se repite si hay opción barata con escala
    assert "Marrakech" not in texto
    assert "billete de ida y vuelta más barato, por persona (1 adulto) × 4" in texto


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

    def roto(*a):
        llamadas.append(1)
        raise RuntimeError("429")

    _, avisos = vb.buscar(cfg(), roto)
    assert len(llamadas) == vb.MAX_ERRORES_SEGUIDOS
    assert avisos and "interrumpida" in avisos[0]


def test_ejecutar_guarda_historial_y_notifica(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(vb, "notificar", enviados.append)
    h = tmp_path / "historial.json"
    assert vb.ejecutar(cfg(), h, falso, tmp_path / 'datos.json') == 0
    assert json.loads(h.read_text()) == {"iv:LIS": 95.0, "iv:OSL+escala": 115.0, "iv:CDG": 170.0,
                                         "iv:CDG+escala": 130.0, "iv:JFK": 800.0, "iv:BER": 137.0}
    vb.ejecutar(cfg(), h, falso, tmp_path / 'datos.json')
    assert "🆕" not in enviados[1]


def test_historial_de_solo_idas_no_se_compara(tmp_path, monkeypatch):
    # El historial antiguo (suma de dos solo idas, sin "iv:") no debe dar 📉/📈 falsos.
    enviados = []
    monkeypatch.setattr(vb, "notificar", enviados.append)
    h = tmp_path / "historial.json"
    h.write_text('{"BER": 151.0}')
    vb.ejecutar(cfg(), h, falso, tmp_path / 'datos.json')
    assert "Berlín (BER): 137 €/pers · 548 € total 🆕" in enviados[0]


def test_ejecutar_falla_si_no_hay_ningun_vuelo(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "notificar", lambda t: [])
    h = tmp_path / "historial.json"
    h.write_text('{"iv:LIS": 90}')
    assert vb.ejecutar(cfg(), h, lambda *a: None, tmp_path / 'datos.json') == 1
    assert json.loads(h.read_text()) == {"iv:LIS": 90}


def test_mensaje_largo_se_recorta():
    muchos = {f"X{i:02d}": f"Destino {i}" for i in range(90)}
    c = cfg(destinos=muchos)
    resultados = [vb.Resultado(k, v, billete_directo=b(10 + i)) for i, (k, v) in enumerate(muchos.items())]
    texto = vb.formatear_mensaje(c, resultados, {}, [], AHORA)
    assert len(texto) <= vb.LIMITE_TELEGRAM and texto.endswith("(lista recortada)")


def test_config_real_valida():
    c = vb.Config.desde_archivo(vb.AQUI / "config.json")
    assert c.origen == "MAD" and c.pasajeros == 4 and c.precio_max_persona == 150
    assert c.incluir_escalas and c.escala_max_minutos == 180
    assert c.fechas_ida == [I1, I2] and c.fechas_vuelta == [V1, V2]
    assert "BCN" not in c.destinos and "PMI" not in c.destinos and "BER" in c.destinos


# --------------------------------------------------------------- partes (varios jobs a la vez)


def test_partes_cubren_todos_los_destinos_una_vez():
    destinos = cfg().destinos
    partes = [vb.partes_de(destinos, i, 4) for i in range(4)]
    assert sorted(k for p in partes for k in p) == sorted(destinos)


def test_partes_se_guardan_y_se_juntan(tmp_path):
    c = cfg()
    paths = []
    for i in range(3):
        sub = cfg(destinos=vb.partes_de(c.destinos, i, 3))
        resultados, avisos = vb.buscar(sub, falso)
        paths.append(tmp_path / f"parte_{i}.json")
        vb.guardar_parte(paths[-1], resultados, avisos + ([f"aviso {i}"] if i == 1 else []))
    juntos, avisos = vb.leer_partes(c, paths)
    solos, _ = vb.buscar(c, falso)
    assert [r.destino for r in juntos] == list(c.destinos)
    assert vb.formatear_mensaje(c, juntos, {}, [], AHORA) == vb.formatear_mensaje(c, solos, {}, [], AHORA)
    assert avisos == ["aviso 1"]
    # Si falta una parte, se manda lo demás y se avisa.
    juntos, avisos = vb.leer_partes(c, paths[:2])
    assert len(juntos) < 7 and "Sin datos de" in avisos[-1]


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


# ------------------------------------------------------------------ billete de ida y vuelta


def test_billete_con_escala_solo_si_mejora_al_directo():
    r = vb.Resultado("BER", "Berlín", billete_directo=b(137), billete_escala=b(140, escala_vuelta="?"))
    assert r.directo.precio == 137 and r.con_escala is None
    r.billete_escala = b(120, escala_vuelta="?")
    assert r.con_escala.precio == 120 and r.mejor.precio == 120


def test_buscar_billete_lee_la_pagina_de_ida_y_vuelta(monkeypatch):
    pedidas = []
    html = _pagina_google([_itinerario_google(137, "BER", 20)], [_itinerario_google(160, "BER", 7)])
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: pedidas.append(q) or html)
    c = vb.buscar_billete("MAD", "BER", I1, V1, False, cfg())
    assert (c.precio, c.ida.salida, c.vuelta.fecha, c.billete_unico, c.con_escala) == (137, "20:00", V1, True, False)
    info = pedidas[0].pb()
    assert len(info.data) == 2 and info.data[1].max_stops == 0


# ------------------------------------------------------------------ mapa


def test_datos_del_mapa(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "notificar", lambda t: [])
    mapa = tmp_path / "datos.json"
    vb.ejecutar(cfg(url_mapa="https://ejemplo/mapa/"), tmp_path / "h.json", falso, mapa)
    datos = json.loads(mapa.read_text())
    codigos = [x["codigo"] for x in datos["destinos"]]
    assert codigos[0] == "LIS" and "RAK" not in codigos and len(codigos) == 5  # solo los que tienen billete
    assert datos["origen"]["codigo"] == "MAD" and datos["pasajeros"] == 4 and datos["precio_max_persona"] == 150
    ber = next(x for x in datos["destinos"] if x["codigo"] == "BER")
    assert (ber["lat"], ber["lon"]) == (52.3617, 13.5023)
    assert ber["directo"]["precio"] == 137 and ber["directo"]["total"] == 548 and ber["escala"] is None
    assert ber["directo"]["ida"]["salida"] == "08:00" and ber["directo"]["vuelta"]["fecha"] == "2027-06-26"
    assert "MAD%20to%20BER%20on%202027-06-19%20through%202027-06-26" in ber["directo"]["google_flights"]
    cdg = next(x for x in datos["destinos"] if x["codigo"] == "CDG")
    assert cdg["escala"]["vuelta"]["con_escala"] and cdg["escala"]["precio"] == 130


def test_mensaje_con_enlace_al_mapa():
    resultados, _ = vb.buscar(cfg(), falso)
    assert "🗺️ Mapa: https://ejemplo/" in vb.formatear_mensaje(cfg(url_mapa="https://ejemplo/"), resultados, {}, [], AHORA)
    assert "Mapa" not in vb.formatear_mensaje(cfg(), resultados, {}, [], AHORA)


def test_coordenadas_de_todos_los_destinos():
    coordenadas = json.loads((vb.AQUI / "coordenadas.json").read_text())
    c = vb.Config.desde_archivo(vb.AQUI / "config.json")
    assert all(k in coordenadas for k in [*c.destinos, c.origen])


def test_datos_del_mapa_sin_cifrar(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "notificar", lambda t: [])
    mapa = tmp_path / "datos.json"
    vb.ejecutar(cfg(), tmp_path / "h.json", falso, mapa)
    assert "destinos" in json.loads(mapa.read_text())
