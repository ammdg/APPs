"""Pruebas sin red de la exploración mundial."""

import json

import explorar as ex
import vuelos_baratos as vb
from test_vuelos_baratos import I1, V1, V2, cfg, d, e


def test_lista_de_aeropuertos():
    a = ex.cargar_aeropuertos()
    assert len(a) > 3000
    assert "MAD" not in a and "BCN" not in a and "PMI" not in a  # sin España
    assert {"LIS", "JFK", "NRT", "SYD"} <= set(a)
    kms = [v["km"] for v in a.values()]
    assert kms == sorted(kms)


def test_partes_cubren_todo_sin_repetir():
    codigos = list(ex.cargar_aeropuertos())
    partes = [ex.parte_de(codigos, i, 20) for i in range(20)]
    assert sorted(sum(partes, [])) == sorted(codigos)
    assert max(map(len, partes)) - min(map(len, partes)) <= 1


def buscador(precios):
    def b(o, dst, f, c):
        x = precios.get((o, dst, f))
        if x == "error":
            raise RuntimeError("429")
        return x or vb.Opciones()
    return b


def test_lote_candidatos_e_informe(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(vb, "notificar", enviados.append)
    c = cfg(pausa_segundos=0)
    ida_precios = {("MAD", "LIS", I1): vb.Opciones(d(I1, 45)), ("MAD", "OSL", I1): vb.Opciones(None, e(I1, 60)),
                   ("MAD", "JFK", I1): vb.Opciones(d(I1, 420)), ("MAD", "XXX", I1): "error"}
    idas = ex.consultar_lote(c, [("MAD", x) for x in ["LIS", "OSL", "JFK", "XXX"]], [I1], buscador(ida_precios))
    assert idas["sin_consultar"] == ["XXX"] and idas["errores"] == 1
    assert ex.candidatos(c, idas) == ["LIS", "OSL"]  # JFK: ida 420 € > 150 €

    vuelta_precios = {("LIS", "MAD", V2): vb.Opciones(d(V2, 50)), ("OSL", "MAD", V1): vb.Opciones(None, e(V1, 55))}
    vueltas = ex.consultar_lote(c, [(x, "MAD") for x in ["LIS", "OSL"]], [V1, V2], buscador(vuelta_precios))
    aeropuertos = {"LIS": {"nombre": "Lisboa", "pais": "PT", "km": 500},
                   "OSL": {"nombre": "Oslo", "pais": "NO", "km": 2400},
                   "JFK": {"nombre": "Nueva York", "pais": "US", "km": 5800}}
    lineas, datos = ex.informe(c, idas, vueltas, aeropuertos)
    texto = "\n".join(lineas)
    assert "• Lisboa, PT (LIS) 95 €/pers · 380 € total · 500 km · directo" in texto
    assert "• Oslo, NO (OSL) 115 €/pers · 460 € total · 2.400 km · escala FRA/FRA" in texto
    assert "⚠️ 1 aeropuertos sin consultar (Google cortó): XXX" in texto
    assert "Más lejano: Oslo, NO (OSL), 2.400 km" in texto
    assert [x["codigo"] for x in datos["destinos"]] == ["LIS", "OSL"]
    json.dumps(datos)  # serializable


def test_se_interrumpe_si_google_bloquea():
    c = cfg(pausa_segundos=0)
    todo_error = buscador({("MAD", f"X{i:02d}", I1): "error" for i in range(20)})
    out = ex.consultar_lote(c, [("MAD", f"X{i:02d}") for i in range(20)], [I1], todo_error)
    assert len(out["sin_consultar"]) == 20 and out["errores"] == vb.MAX_ERRORES_SEGUIDOS


def test_trocear_respeta_limite():
    lineas = [f"linea {i} " + "x" * 90 for i in range(200)]
    partes = ex.trocear(lineas)
    assert all(len(p) <= vb.LIMITE_TELEGRAM for p in partes)
    assert "\n".join(partes).split("\n") == lineas


def test_fases_con_ficheros(tmp_path, monkeypatch):
    """Simula el workflow: 2 partes de ida, 1 de vuelta y el informe, con archivos reales."""
    monkeypatch.setattr(vb, "notificar", lambda t: [])
    monkeypatch.setattr(vb, "buscar_opciones", buscador({("MAD", "LIS", I1): vb.Opciones(d(I1, 45)),
                                                         ("LIS", "MAD", V1): vb.Opciones(d(V1, 50))}))
    monkeypatch.setattr(ex, "cargar_aeropuertos", lambda: {"LIS": {"nombre": "Lisboa", "pais": "PT", "km": 500},
                                                            "OPO": {"nombre": "Oporto", "pais": "PT", "km": 420}})
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"origen": "MAD", "fechas_ida": ["2027-06-19"], "fechas_vuelta": ["2027-06-26"],
                                "destinos": {}, "pasajeros": 4, "incluir_escalas": True, "pausa_segundos": 0}))
    import sys
    for args in (["ida", "--parte", "0", "--de", "2"], ["ida", "--parte", "1", "--de", "2"],
                 ["vuelta", "--parte", "0", "--de", "1"], ["informe"]):
        monkeypatch.setattr(sys, "argv", ["explorar.py", *args, "--config", str(conf), "--dir", str(tmp_path)])
        assert ex.main() == 0
    datos = json.loads((tmp_path / "exploracion.json").read_text())
    assert [x["codigo"] for x in datos["destinos"]] == ["LIS"] and datos["destinos"][0]["precio_persona"] == 95
