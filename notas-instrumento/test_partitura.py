"""Pruebas de la cuantización y del texto ABC (sin audio ni modelos)."""

import partitura as pt
from notas_instrumento import Nota

# 120 negras/min: un pulso cada 0.5 s, una semicorchea cada 0.125 s.
PULSOS = [i * 0.5 for i in range(1, 20)]


def rejilla():
    return pt.Rejilla(PULSOS, duracion=9.0)


def test_rejilla_rellena_el_principio_y_calcula_el_tempo():
    r = rejilla()
    assert r.tempo == 120
    assert r.hueco(0.0) == 0
    assert r.hueco(0.5) == 4
    assert r.segundos_de_hueco(8) == 1.0


def test_primer_pulso_casi_en_cero_no_desplaza_la_rejilla():
    r = pt.Rejilla([0.05 + i * 0.5 for i in range(10)], duracion=5)
    assert r.hueco(0.05) == 0


def test_cuantiza_acordes_silencios_y_relleno_de_compas():
    notas = [Nota(0.0, 0.5, 60, 1), Nota(0.01, 0.5, 64, 1),  # negra Do+Mi
             Nota(1.0, 1.25, 67, 1)]  # tras un silencio de negra, corchea Sol
    ev = pt.cuantiza(notas, rejilla())
    assert [(e.inicio, e.duracion, e.notas) for e in ev] == [
        (0, 4, [60, 64]), (4, 4, []), (8, 2, [67]), (10, 6, [])]


def test_cuantiza_absorbe_huecos_de_una_semicorchea():
    notas = [Nota(0.0, 0.375, 60, 1), Nota(0.5, 1.0, 62, 1)]  # la 1.ª se suelta 1/16 antes
    ev = pt.cuantiza(notas, rejilla())
    assert (ev[0].inicio, ev[0].duracion) == (0, 4)


def test_trocea_por_barra_y_duraciones_escribibles():
    assert pt.trocea(14, 5) == [(14, 2), (16, 3)]
    assert pt.trocea(0, 5) == [(0, 4), (4, 1)]
    assert pt.trocea(0, 16) == [(0, 16)]


def test_altura_abc_y_alteraciones_por_compas():
    alt = {}
    assert pt.altura_abc(60, alt) == "C"
    assert pt.altura_abc(61, alt) == "^C"
    assert pt.altura_abc(61, alt) == "C"  # el sostenido sigue valiendo en el compás
    assert pt.altura_abc(60, alt) == "=C"
    assert pt.altura_abc(72, {}) == "c"
    assert pt.altura_abc(84, {}) == "c'"
    assert pt.altura_abc(48, {}) == "C,"
    assert pt.altura_abc(28, {}) == "E,,,"


def test_genera_abc_con_ligadura_y_eventos():
    notas = [Nota(1.5, 2.5, 60, 1)]  # cruza la barra del compás 1 al 2
    r = rejilla()
    abc, eventos = pt.genera_abc([pt.Voz("V1", "", "treble", pt.cuantiza(notas, r))], r, "t")
    cuerpo = abc.split("K:C\n")[1]
    assert "C4- | C4" in cuerpo
    assert [abc[e["char"]] for e in eventos] == ["C", "C"]
    assert eventos[0]["inicio"] == 1.5 and eventos[1]["fin"] == 2.5


def test_nombres_de_notas_solo_en_notas_sueltas():
    r = rejilla()
    notas = [Nota(0, 0.5, 60, 1), Nota(0.5, 1.0, 62, 1), Nota(0.5, 1.0, 65, 1)]
    abc, _ = pt.genera_abc([pt.Voz("V1", "", "treble", pt.cuantiza(notas, r))], r, "t",
                           con_nombres=True)
    assert "w: Do *" in abc


def test_piano_a_dos_pentagramas():
    datos = pt.partitura([Nota(0, 0.5, 72, 1), Nota(0, 0.5, 48, 1)], PULSOS, 9.0, "piano", "t")
    assert "%%score {MD MI}" in datos["abc"]
    assert "clef=bass" in datos["abc"]
    assert len(datos["eventos"]) == 2


def test_clave_de_fa_para_bajo_y_notas_graves():
    r = rejilla()
    assert pt.voces_para([Nota(0, 1, 70, 1)], "bajo", r)[0].clave == "bass"
    assert pt.voces_para([Nota(0, 1, 45, 1)], "otros", r)[0].clave == "bass"
    assert pt.voces_para([Nota(0, 1, 70, 1)], "voz", r)[0].clave == "treble"


def test_sin_notas_da_un_compas_de_silencio():
    datos = pt.partitura([], PULSOS, 9.0, "voz", "t")
    assert "z16" in datos["abc"] and datos["eventos"] == []
