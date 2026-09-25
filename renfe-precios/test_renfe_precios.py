"""Pruebas sin red. La respuesta DWR es sintética: imita el formato que parsea renfe-bot,
no es una captura real de Renfe."""

import json
from datetime import date

import pytest

import renfe_precios as rp

RESPUESTA_DWR = """//#DWR-INSERT
//#DWR-REPLY
r.handleCallback("1","0",{listadoTrenes:[{listviajeViewEnlaceBean:[
 {horaSalida:"07:00",horaLlegada:"09:30",duracionViajeTotalEnMinutos:150,tarifaMinima:"35,50",
  completo:false,razonNoDisponible:"",soloPlazaH:false,tipoTrenUno:"AVLO"},
 {horaSalida:"08:00",horaLlegada:"10:30",duracionViajeTotalEnMinutos:150,tarifaMinima:"62,10",
  completo:false,razonNoDisponible:"",soloPlazaH:false,tipoTrenUno:"AVE"},
 {horaSalida:"09:00",horaLlegada:"11:30",duracionViajeTotalEnMinutos:150,tarifaMinima:null,
  completo:true,razonNoDisponible:"1",soloPlazaH:false,tipoTrenUno:"AVE"},
 {horaSalida:"23:00",horaLlegada:"01:30",duracionViajeTotalEnMinutos:150,tarifaMinima:"20,00",
  completo:false,razonNoDisponible:"",soloPlazaH:false,tipoTrenUno:"AVE"}
]}]});
"""
FECHA = date(2030, 1, 10)


def cfg(**kw):
    base = dict(origen="A", destino="B", fechas=[FECHA], precio_min=0, precio_max=40,
                hora_desde="06:00", hora_hasta="22:00")
    return rp.Viaje(**{**base, **kw})


def test_parsea_respuesta():
    trenes = rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), FECHA)
    assert [t.precio for t in trenes] == [35.5, 62.1, None, 20.0]
    assert [t.disponible for t in trenes] == [True, True, False, True]


def test_token_dwr():
    assert rp.extraer_token_dwr('r.handleCallback("0","0","ABC123xyz");') == "ABC123xyz"
    with pytest.raises(rp.ErrorRenfe):
        rp.extraer_token_dwr("nada")


def test_filtro_precio_hora_y_tipo():
    trenes = rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), FECHA)
    assert [t.salida for t in rp.trenes_en_rango(trenes, cfg())] == ["07:00"]  # 23:00 fuera de hora
    assert rp.trenes_en_rango(trenes, cfg(tipos_tren=["AVE"])) == []
    assert [t.salida for t in rp.trenes_en_rango(trenes, cfg(precio_min=36, precio_max=70))] == ["08:00"]


def test_no_repite_avisos_y_avisa_si_baja():
    t = rp.Tren(FECHA, "07:00", "09:30", 150, 35.5, True, "AVLO")
    estado = rp.actualizar_estado({}, [t], date(2030, 1, 1))
    assert rp.nuevos_para_avisar([t], estado) == []
    mas_barato = rp.Tren(FECHA, "07:00", "09:30", 150, 30.0, True, "AVLO")
    assert rp.nuevos_para_avisar([mas_barato], estado) == [mas_barato]
    # si sale del rango se olvida, y si vuelve a entrar se avisa otra vez
    assert rp.actualizar_estado(estado, [], date(2030, 1, 1)) == {}


def test_estaciones():
    assert rp.obtener_estacion("madrid (todas)").codigo == "0071,MADRI,null"
    assert "BARCELONA (TODAS)" in rp.buscar_estaciones("barcelona")


def test_comprobar_notifica(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(rp, "consultar_renfe",
                        lambda o, d, f: rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), f))
    monkeypatch.setattr(rp, "notificar", enviados.append)
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    c = cfg(origen="MADRID (TODAS)", destino="BARCELONA (TODAS)")
    estado = tmp_path / "estado.json"
    assert rp.comprobar([c], estado) == 0
    assert len(enviados) == 1 and "35.50 €" in enviados[0]
    assert rp.comprobar([c], estado) == 0
    assert len(enviados) == 1  # la segunda hora no repite el aviso
    assert json.loads(estado.read_text())


def test_ida_y_vuelta_en_un_solo_aviso(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(rp, "consultar_renfe",
                        lambda o, d, f: rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), f,
                                                          f"{o.codigo}>{d.codigo}"))
    monkeypatch.setattr(rp, "notificar", enviados.append)
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    ida = cfg(origen="MADRID (TODAS)", destino="PAMPLONA/IRUÑA")
    vuelta = cfg(origen="PAMPLONA/IRUÑA", destino="MADRID (TODAS)")
    estado = tmp_path / "estado.json"
    assert rp.comprobar([ida, vuelta], estado) == 0
    assert len(enviados) == 1
    assert "MADRID (TODAS) → PAMPLONA/IRUÑA" in enviados[0]
    assert "PAMPLONA/IRUÑA → MADRID (TODAS)" in enviados[0]
    assert len(json.loads(estado.read_text())) == 2  # mismo tren/hora, pero sentidos distintos


def test_config_con_viajes_y_valores_comunes(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({
        "precio_max": 50, "hora_desde": "16:00",
        "viajes": [
            {"origen": "MADRID (TODAS)", "destino": "PAMPLONA/IRUÑA", "fecha": "2030-10-02"},
            {"origen": "PAMPLONA/IRUÑA", "destino": "MADRID (TODAS)", "fecha": "2030-10-04",
             "precio_max": 30, "duracion_max": "3:30"},
        ],
    }))
    ida, vuelta = rp.cargar_config(f)
    assert ida.fechas == [date(2030, 10, 2)] and ida.hora_desde == "16:00" and ida.precio_max == 50
    assert vuelta.precio_max == 30 and vuelta.hora_desde == "16:00"
    assert ida.duracion_max is None and vuelta.duracion_max == 210


def test_duracion_maxima():
    trenes = rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), FECHA)
    assert [t.salida for t in rp.trenes_en_rango(trenes, cfg(duracion_max=150))] == ["07:00"]
    assert rp.trenes_en_rango(trenes, cfg(duracion_max=149)) == []


def test_formatos_de_duracion():
    assert rp._a_duracion("3:30") == 210
    assert rp._a_duracion(200) == 200
    assert rp._a_duracion(None) is None
    assert rp.formatear_duracion(204) == "3h24"


def test_duracion_se_calcula_si_renfe_no_la_da():
    datos = {"listadoTrenes": [{"listviajeViewEnlaceBean": [
        {"horaSalida": "22:30", "horaLlegada": "01:10", "tarifaMinima": "30,00", "completo": False,
         "razonNoDisponible": "", "soloPlazaH": False, "tipoTrenUno": "ALVIA"}]}]}
    assert rp.parsear_trenes(datos, FECHA)[0].duracion_min == 160


def test_aviso_muestra_duracion():
    t = rp.Tren(FECHA, "07:32", "10:56", 204, 39.6, True, "ALVIA")
    assert "07:32-10:56 (3h24) ALVIA: 39.60 €" in rp.formatear_aviso("A", "B", [t])


def test_resumen_cada_hora_aunque_no_haya_cambios(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(rp, "consultar_renfe",
                        lambda o, d, f: rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), f,
                                                          f"{o.codigo}>{d.codigo}"))
    monkeypatch.setattr(rp, "notificar", enviados.append)
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    c = cfg(origen="MADRID (TODAS)", destino="PAMPLONA/IRUÑA")
    estado = tmp_path / "estado.json"
    assert rp.comprobar([c], estado, resumen=True) == 0
    assert rp.comprobar([c], estado, resumen=True) == 0
    assert len(enviados) == 2
    primero, segundo = enviados
    assert primero.startswith("🔔 ¡Novedades!") and "AVLO 35,50 € 🆕" in primero
    assert segundo.startswith("🕐 Sin cambios")
    assert not [linea for linea in segundo.splitlines() if linea.endswith("🆕")]
    # lista todos los trenes con plazas (también los que no cumplen filtros), con duración
    assert "✅ 07:00-09:30 (2h30) AVLO 35,50 €" in segundo
    assert "▫️ 08:00-10:30 (2h30) AVE 62,10 €" in segundo
    assert "▫️ 23:00-01:30 (2h30) AVE 20,00 €" in segundo
    assert "(+1 sin plazas)" in segundo


def test_resumen_muestra_errores_de_consulta(tmp_path, monkeypatch):
    enviados = []

    def falla(o, d, f):
        raise rp.ErrorRenfe("x")

    monkeypatch.setattr(rp, "consultar_renfe", falla)
    monkeypatch.setattr(rp, "notificar", enviados.append)
    c = cfg(origen="MADRID (TODAS)", destino="PAMPLONA/IRUÑA")
    assert rp.comprobar([c], tmp_path / "estado.json", resumen=True) == 1
    assert "⚠️ No se pudo consultar Renfe" in enviados[0]


# ------------------------------------------------------------------ vuelos (Google Flights simulado)

from fast_flights.exceptions import FlightsNotFound
from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight


def _vuelo(salida, llegada, precio, fecha=(2030, 1, 10), tramos=1, aerolinea="Iberia"):
    def tramo(h1, h2):
        return SingleFlight(Airport("Madrid", "MAD"), Airport("Pamplona", "PNA"),
                            SimpleDatetime(fecha, h1), SimpleDatetime(fecha, h2), 65, "CRJ1000")
    return Flights("IB", precio, [aerolinea], [tramo(salida, llegada)] * tramos, CarbonEmission(0, 0))


def vuelo_cfg(**kw):
    base = dict(origen="MAD", destino="PNA", fechas=[FECHA], precio_min=0, precio_max=None, medio="avion",
                aerolineas=["IB"], solo_directos=True)
    return rp.Viaje(**{**base, **kw})


def test_consultar_vuelos_directos_y_mas_baratos(monkeypatch):
    pedidas = []

    def falso(q):
        pedidas.append(q)
        return [_vuelo((7, 5), (8, 10), 89), _vuelo((7, 5), (8, 10), 64),  # misma hora, 2 tarifas
                _vuelo((15, 40), (16, 45), 120),
                _vuelo((9, 0), (13, 0), 50, tramos=2),                     # con escala: fuera
                _vuelo((22, 0), (23, 5), 70, fecha=(2030, 1, 11))]        # otro día: fuera
    monkeypatch.setattr(rp, "obtener_vuelos", falso)
    vuelos = rp.consultar_vuelos(vuelo_cfg(), FECHA)
    assert sorted((v.salida, v.precio, v.duracion_min) for v in vuelos) == [
        ("07:05", 64.0, 65), ("15:40", 120.0, 65)]
    assert all(v.tipo == "Iberia" and v.sentido == "MAD>PNA avion" for v in vuelos)
    info = pedidas[0].pb().data[0]
    assert info.max_stops == 0 and list(info.airlines) == ["IB"]
    assert pedidas[0].currency == "EUR"


def test_consultar_vuelos_sin_resultados(monkeypatch):
    def falso(q):
        raise FlightsNotFound("nada")

    monkeypatch.setattr(rp, "obtener_vuelos", falso)
    assert rp.consultar_vuelos(vuelo_cfg(), FECHA) == []


def test_sin_limite_de_precio_en_vuelos():
    v = rp.Tren(FECHA, "07:05", "08:10", 65, 350.0, True, "Iberia", "MAD>PNA avion")
    assert rp.trenes_en_rango([v], vuelo_cfg()) == [v]


def test_config_con_vuelos(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({
        "precio_max": 60, "duracion_max": "3:30",
        "viajes": [{"origen": "MADRID (TODAS)", "destino": "PAMPLONA/IRUÑA", "fecha": "2030-10-02"}],
        "vuelos": {"aerolineas": ["ib"], "solo_directos": True, "precio_max": None, "viajes": [
            {"origen": "mad", "destino": "pna", "fecha": "2030-10-02"},
            {"origen": "PNA", "destino": "MAD", "fecha": "2030-10-04", "hora_desde": "16:00"}]},
    }))
    tren, ida, vuelta = rp.cargar_config(f)
    assert tren.medio == "tren" and tren.precio_max == 60
    assert (ida.medio, ida.origen, ida.aerolineas, ida.precio_max) == ("avion", "MAD", ["IB"], None)
    assert ida.duracion_max is None  # los ajustes de trenes no se aplican a los vuelos
    assert vuelta.hora_desde == "16:00"


def test_resumen_con_trenes_y_vuelos_y_fallo_de_google(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(rp, "consultar_renfe",
                        lambda o, d, f: rp.parsear_trenes(rp.extraer_lista_trenes(RESPUESTA_DWR), f,
                                                          f"{o.codigo}>{d.codigo}"))

    def vuelos(viaje, fecha):
        if viaje.origen == "PNA":
            raise RuntimeError("bloqueado")
        return [rp.Tren(fecha, "07:05", "08:10", 65, 64.0, True, "Iberia", "MAD>PNA avion")]

    monkeypatch.setattr(rp, "consultar_vuelos", vuelos)
    monkeypatch.setattr(rp, "notificar", enviados.append)
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    viajes = [cfg(origen="MADRID (TODAS)", destino="PAMPLONA/IRUÑA"), vuelo_cfg(),
              vuelo_cfg(origen="PNA", destino="MAD")]
    assert rp.comprobar(viajes, tmp_path / "estado.json", resumen=True) == 0  # los trenes sí funcionaron
    texto = enviados[0]
    assert "🚆 MADRID (TODAS) → PAMPLONA/IRUÑA" in texto
    assert "✈️ MAD → PNA" in texto and "✅ 07:05-08:10 (1h05) Iberia 64,00 € 🆕" in texto
    assert "✈️ PNA → MAD" in texto and "⚠️ No se pudo consultar Google Flights: RuntimeError" in texto
    assert "Vuelos: https://www.iberia.com" in texto


def _itinerario_google(precio, hora):
    tramo = [None] * 22
    tramo[3], tramo[4], tramo[5], tramo[6] = "MAD", "Madrid", "Pamplona", "PNA"
    tramo[8], tramo[10], tramo[11], tramo[17] = [hora], [hora + 1], 65, "CRJ1000"
    tramo[20], tramo[21] = list(FECHA.timetuple()[:3]), list(FECHA.timetuple()[:3])
    vuelo = [None] * 23
    vuelo[0], vuelo[1], vuelo[2] = "IB", ["Iberia"], [tramo]
    vuelo[22] = [None] * 9
    return [vuelo, [[None, precio]]]


def test_lee_tambien_las_mejores_opciones(monkeypatch):
    # fast-flights 3.1.0 solo lee payload[3]; lo más barato suele estar en payload[2] ("mejores opciones").
    import fast_flights

    payload = [None] * 8
    payload[2] = [[_itinerario_google(64, 7), []]]  # el segundo, sin precio legible, se descarta
    payload[3] = [[_itinerario_google(120, 15)]]
    payload[7] = [None, [[], []]]
    html = (f'<script class="ds:1">AF_initDataCallback({{key: \'ds:1\', data:{json.dumps(payload)}, '
            f'sideChannel: {{}}}});</script>')
    monkeypatch.setattr(fast_flights, "fetch_flights_html", lambda q: html)
    vuelos = rp.consultar_vuelos(vuelo_cfg(), FECHA)
    assert sorted((v.salida, v.precio) for v in vuelos) == [("07:00", 64.0), ("15:00", 120.0)]
