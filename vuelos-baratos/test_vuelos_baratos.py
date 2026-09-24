"""Pruebas sin red: Google Flights se sustituye por un buscador falso."""

import json
from datetime import date, datetime

import vuelos_baratos as vb

I1, I2, V1, V2 = date(2027, 6, 19), date(2027, 6, 20), date(2027, 6, 26), date(2027, 6, 27)
AHORA = datetime(2026, 9, 24, 8, 17)

# precio de cada vuelo (origen, destino, fecha) -> €
PRECIOS = {
    ("MAD", "LIS", I1): 60, ("MAD", "LIS", I2): 45, ("LIS", "MAD", V1): 70, ("LIS", "MAD", V2): 50,  # 95
    ("MAD", "BCN", I1): 40, ("BCN", "MAD", V1): 130, ("BCN", "MAD", V2): 150,                          # 170
    ("MAD", "JFK", I1): 420, ("JFK", "MAD", V2): 380,                                                  # 800
    ("MAD", "RAK", I1): 30,                                                                            # sin vuelta
}


def falso(o, d, f, directos):
    p = PRECIOS.get((o, d, f))
    return vb.Vuelo(f, "08:00", "09:10", float(p), "Aerolínea") if p else None


def cfg(**kw):
    base = dict(origen="MAD", fechas_ida=[I1, I2], fechas_vuelta=[V1, V2], pasajeros=4,
                precio_max_persona=150, mostrar_por_encima=5, pausa_segundos=0,
                destinos={"LIS": "Lisboa", "BCN": "Barcelona", "JFK": "Nueva York", "RAK": "Marrakech",
                          "OSL": "Oslo"})
    return vb.Config(**{**base, **kw})


def test_combina_ida_y_vuelta_mas_baratas():
    resultados, avisos = vb.buscar(cfg(), falso)
    r = {x.destino: x for x in resultados}
    assert r["LIS"].precio == 95 and r["LIS"].ida.fecha == I2 and r["LIS"].vuelta.fecha == V2
    assert r["BCN"].precio == 170
    assert r["RAK"].ida and r["RAK"].vuelta is None and r["RAK"].precio is None
    assert r["OSL"].ida is None
    assert avisos == []


def test_no_busca_vuelta_si_la_ida_ya_es_cara():
    pedidas = []

    def contar(o, d, f, directos):
        pedidas.append((o, d, f))
        return falso(o, d, f, directos)

    vb.buscar(cfg(mostrar_por_encima=0), contar)
    assert not [p for p in pedidas if p[0] == "JFK"]  # ida 420 € > 150 €: no se consulta la vuelta
    assert ("LIS", "MAD", V1) in pedidas


def test_mensaje_ranking_y_marcas():
    resultados, avisos = vb.buscar(cfg(), falso)
    texto = vb.formatear_mensaje(cfg(), resultados, {"LIS": 120.0, "BCN": 150.0}, avisos, AHORA)
    assert "✅ Por debajo de 150 €/persona" in texto
    assert "• Lisboa (LIS): 95 €/pers · 380 € total 📉 antes 120 €" in texto
    assert "ida dom 20/06 08:00-09:10 Aerolínea · vuelta dom 27/06" in texto
    assert "Barcelona (BCN): 170 €/pers · 680 € total 📈 antes 150 €" in texto  # por encima, como referencia
    assert "Nueva York (JFK): 800 €/pers · 3.200 € total 🆕" in texto
    assert texto.index("Lisboa") < texto.index("Los siguientes") < texto.index("Barcelona")
    assert "Marrakech" not in texto and "Oslo" not in texto  # sin ida y vuelta completas


def test_sin_baratos():
    resultados, _ = vb.buscar(cfg(precio_max_persona=50), falso)
    texto = vb.formatear_mensaje(cfg(precio_max_persona=50), resultados, {}, [], AHORA)
    assert "Ningún destino por debajo de 50 €/persona hoy." in texto


def test_interrumpe_si_google_bloquea():
    llamadas = []

    def roto(o, d, f, directos):
        llamadas.append(1)
        raise RuntimeError("429")

    resultados, avisos = vb.buscar(cfg(), roto)
    assert len(llamadas) == vb.MAX_ERRORES_SEGUIDOS
    assert avisos and "interrumpida" in avisos[0]


def test_ejecutar_guarda_historial_y_notifica(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(vb, "notificar", enviados.append)
    h = tmp_path / "historial.json"
    assert vb.ejecutar(cfg(), h, falso) == 0
    assert json.loads(h.read_text()) == {"LIS": 95.0, "BCN": 170.0, "JFK": 800.0}
    assert "🆕" in enviados[0]
    vb.ejecutar(cfg(), h, falso)
    assert "🆕" not in enviados[1]


def test_ejecutar_falla_si_no_hay_ningun_vuelo(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "notificar", lambda t: [])
    h = tmp_path / "historial.json"
    h.write_text('{"LIS": 90}')
    assert vb.ejecutar(cfg(), h, lambda *a: None) == 1
    assert json.loads(h.read_text()) == {"LIS": 90}  # no se pierde el historial


def test_mensaje_largo_se_recorta():
    muchos = {f"X{i:02d}": f"Destino {i}" for i in range(90)}
    c = cfg(destinos=muchos)
    resultados = [vb.Resultado(k, v, vb.Vuelo(I1, "08:00", "09:00", 10.0 + i, "A"),
                               vb.Vuelo(V1, "08:00", "09:00", 10.0, "A"))
                  for i, (k, v) in enumerate(muchos.items())]
    texto = vb.formatear_mensaje(c, resultados, {}, [], AHORA)
    assert len(texto) <= vb.LIMITE_TELEGRAM and texto.endswith("(lista recortada)")


def test_config_real_valida():
    c = vb.Config.desde_archivo(vb.AQUI / "config.json")
    assert c.origen == "MAD" and c.pasajeros == 4 and c.precio_max_persona == 150 and c.solo_directos
    assert c.fechas_ida == [I1, I2] and c.fechas_vuelta == [V1, V2]
    assert all(len(k) == 3 and k.isalpha() for k in c.destinos)
