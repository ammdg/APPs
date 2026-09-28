"""Pruebas sin red: las velas son sintéticas, no datos reales de mercado."""

import pandas as pd
import pytest

import alertas_bolsa as ab


def velas(cierres, volumen=None):
    idx = pd.bdate_range("2030-01-01", periods=len(cierres))
    c = pd.Series(cierres, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                         "Volume": volumen if volumen is not None else [1000.0] * len(c)}, index=idx)


def test_rsi_extremos():
    assert ab.rsi(pd.Series(range(1, 40), dtype=float)).iloc[-1] == 100
    assert ab.rsi(pd.Series(range(40, 1, -1), dtype=float)).iloc[-1] == pytest.approx(0)
    assert pd.isna(ab.rsi(pd.Series([1.0, 2, 3])).iloc[-1])  # faltan datos


def test_rsi_valor_conocido():
    # Alterna +1/-1: subidas y bajadas medias iguales -> RSI cercano a 50
    serie = pd.Series([10 + (i % 2) for i in range(200)], dtype=float)
    assert ab.rsi(serie).iloc[-1] == pytest.approx(50, abs=5)


def test_variables():
    d = velas([100.0] * 30 + [110.0])
    assert ab.variable(d, "var1").iloc[-1] == pytest.approx(10)
    assert ab.variable(d, "sma5").iloc[-1] == pytest.approx(102)
    assert ab.variable(d, "bb_media").iloc[-1] == pytest.approx(100.5)
    with pytest.raises(ValueError):
        ab.variable(d, "inventada")


def test_expresiones_no_permitidas():
    for mala in ["__import__('os')", "precio.real", "precio[0]", "'a' < 'b'", "precio ** 2"]:
        with pytest.raises((ValueError, SyntaxError)):
            ab.compilar(mala)
    with pytest.raises(ValueError):
        ab.Regla("x", "foo < 3")


def test_evaluar_combinada():
    d = velas([100.0] * 30 + [90.0])
    r = ab.Regla("x", "var1 < -5 and not precio > 95 or precio > 1000")
    assert list(ab.evaluar(r.arbol, d).iloc[-2:]) == [False, True]
    r2 = ab.Regla("y", "80 < precio < 95")
    assert bool(ab.evaluar(r2.arbol, d).iloc[-1])


def test_solo_avisa_al_cambiar():
    regla = ab.Regla("Caída", "var1 < -5")
    # Cae hoy -> avisa
    assert len(ab.revisar("AAA", velas([100, 100, 90]), [regla])) == 1
    # Cayó ayer y hoy también -> ya se cumplía, no avisa en modo cambio
    assert ab.revisar("AAA", velas([100, 90, 80]), [regla]) == []
    siempre = ab.Regla("Caída", "var1 < -5", modo="siempre")
    assert len(ab.revisar("AAA", velas([100, 90, 80]), [siempre])) == 1


def test_cruce_de_medias():
    regla = ab.Regla("Cruce", "sma2 > sma4")
    assert len(ab.revisar("AAA", velas([10, 9, 8, 7, 6, 12]), [regla])) == 1
    assert ab.revisar("AAA", velas([10, 9, 8, 7, 6, 5]), [regla]) == []


def test_regla_por_ticker():
    regla = ab.Regla("Solo B", "precio > 0", tickers=["BBB"], modo="siempre")
    assert ab.revisar("AAA", velas([1, 2]), [regla]) == []
    assert len(ab.revisar("BBB", velas([1, 2]), [regla])) == 1


def test_estado_evita_repetir_misma_vela():
    a = ab.revisar("AAA", velas([100, 100, 90]), [ab.Regla("Caída", "var1 < -5")])
    assert ab.filtrar_ya_avisadas(a, {}) == a
    assert ab.filtrar_ya_avisadas(a, {a[0].clave: a[0].fecha}) == []
    assert ab.filtrar_ya_avisadas(a, {a[0].clave: "2000-01-01"}) == a


def test_formatear():
    a = ab.revisar("AAA", velas([100, 100, 90]), [ab.Regla("Caída", "var1 < -5")])
    texto = ab.formatear(a)
    assert "AAA" in texto and "Caída" in texto and "-10.00%" in texto


def test_config_por_defecto_valida():
    cfg = ab.cargar_config(ab.AQUI / "config.json")
    assert cfg.universo == "universo_fijo.json" and cfg.estrategia


# --------------------------------------------------------------------------- estrategia_score


def escenario(dias_tras_buy=3):
    """Tendencia alcista larga, corrección de ~9% en 6 sesiones y rebote con volumen doble.

    Con estrategia_score da WATCH en la 4.ª sesión de caída y BUY en la 2.ª de rebote.
    """
    p = [100.0]
    for i in range(259):
        p.append(p[-1] * 1.004 + (0.3 if i % 2 else -0.3))
    for _ in range(6):
        p.append(p[-1] * 0.985)
    for _ in range(2 + dias_tras_buy):
        p.append(p[-1] * 1.03)
    vol = [1000.0] * (len(p) - 2 - dias_tras_buy) + [2000.0] * (2 + dias_tras_buy)
    return velas(p, vol)


def test_estrategia_watch_y_buy():
    res = ab.senales(escenario())
    emitidas = res[res["Signal"] != "NO SIGNAL"]
    assert list(emitidas["Signal"]) == ["WATCH", "BUY"]
    assert emitidas["Score"].iloc[1] >= 70


def test_estrategia_solo_avisa_en_la_ultima_vela():
    d = escenario()
    buy = ab.senales(d).query("Signal == 'BUY'").index[0]
    watch = ab.senales(d).query("Signal == 'WATCH'").index[0]
    assert [a.nombre for a in ab.revisar_estrategia("AAA", d.loc[:buy])] == ["BUY"]
    assert [a.nombre for a in ab.revisar_estrategia("AAA", d.loc[:watch])] == ["WATCH"]
    assert ab.revisar_estrategia("AAA", d) == []  # el BUY fue hace 3 sesiones


def test_estrategia_sin_datos_suficientes():
    assert ab.revisar_estrategia("AAA", velas([100.0] * 150)) == []


def test_mensaje_estrategia():
    d = escenario()
    buy = ab.senales(d).query("Signal == 'BUY'").index[0]
    texto = ab.formatear(ab.revisar_estrategia("AAA", d.loc[:buy]))
    assert "🟢 BUY: score" in texto and "RSI" in texto


def test_comprobar_con_estrategia(tmp_path, monkeypatch):
    d = escenario()
    buy = ab.senales(d).query("Signal == 'BUY'").index[0]
    enviados = []
    monkeypatch.setattr(ab, "descargar", lambda t, h: {"AAA": d.loc[:buy]})
    monkeypatch.setattr(ab, "notificar", lambda texto: enviados.append(texto) or ["x"])
    cfg = ab.Config(tickers=["AAA"], reglas=[], estrategia=True)
    estado = tmp_path / "estado.json"
    assert ab.comprobar(cfg, estado) == 0
    assert ab.comprobar(cfg, estado) == 0  # misma vela: no repite
    assert len(enviados) == 1 and "BUY" in enviados[0]


def test_trocear_mensajes_largos():
    bloques = [f"TICKER{i}\n  🟢 BUY: score 80  [RSI 55]" + " x" * 40 for i in range(200)]
    texto = "\n\n".join(bloques)
    trozos = ab.trocear(texto)
    assert len(trozos) > 1 and all(len(t) <= 3900 for t in trozos)
    assert "\n\n".join(trozos) == texto


def test_elegir_tickers_con_lista_fija(tmp_path, monkeypatch):
    monkeypatch.setattr(ab.universo, "cargar", lambda path: (["A", "B"], "2026-09-25"))
    cfg = ab.Config(tickers=["B", "C"], reglas=[], universo="universo_fijo.json")
    assert ab.elegir_tickers(cfg) == ["A", "B", "C"]
    assert ab.elegir_tickers(ab.Config(tickers=["Z"], reglas=[])) == ["Z"]
