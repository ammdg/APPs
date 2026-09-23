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
             "precio_max": 30},
        ],
    }))
    ida, vuelta = rp.cargar_config(f)
    assert ida.fechas == [date(2030, 10, 2)] and ida.hora_desde == "16:00" and ida.precio_max == 50
    assert vuelta.precio_max == 30 and vuelta.hora_desde == "16:00"
