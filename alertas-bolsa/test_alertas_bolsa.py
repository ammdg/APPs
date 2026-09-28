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
    assert cfg.tickers and cfg.reglas


def test_comprobar_guarda_estado(tmp_path, monkeypatch):
    enviados = []
    monkeypatch.setattr(ab, "descargar", lambda t, h: {"AAA": velas([100, 100, 90])})
    monkeypatch.setattr(ab, "notificar", lambda texto: enviados.append(texto) or ["x"])
    cfg = ab.Config(tickers=["AAA"], reglas=[ab.Regla("Caída", "var1 < -5")])
    estado = tmp_path / "estado.json"
    assert ab.comprobar(cfg, estado) == 0
    assert ab.comprobar(cfg, estado) == 0  # misma vela: no repite
    assert len(enviados) == 1
