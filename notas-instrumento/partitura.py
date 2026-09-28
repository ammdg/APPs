"""Convierte notas sueltas (en segundos) en una partitura en notación ABC, que en el navegador
dibuja abcjs.

- El ritmo se cuantiza a semicorcheas sobre los pulsos que detecta librosa, así que sigue los
  cambios de tempo de la canción.
- Se supone compás de 4/4 empezando en el primer pulso (no se detecta el compás real).
- Las notas que empiezan a la vez se escriben como acorde; una voz por pentagrama.
- Junto al ABC se devuelve, para cada nota escrita, su posición en el texto y su tiempo en
  segundos, para iluminarla mientras suena.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from notas_instrumento import NOMBRES_INGLESES, NOMBRES_LATINOS, Nota

DIVISIONES = 4  # semicorcheas por pulso
POR_COMPAS = 4 * DIVISIONES
COMPASES_POR_LINEA = 4
DURACIONES = [16, 12, 8, 6, 4, 3, 2, 1]  # en semicorcheas, las que se escriben sin ligadura


# --------------------------------------------------------------------------- tiempo <-> pulsos


class Rejilla:
    """Traduce segundos a pulsos (y al revés) interpolando entre los pulsos detectados."""

    def __init__(self, pulsos: list[float], duracion: float, tempo_defecto: float = 120.0):
        p = sorted(float(x) for x in pulsos)
        if len(p) < 2:
            p = [0.0, 60.0 / tempo_defecto]
        paso = float(np.median(np.diff(p)))
        # Alarga hacia atrás hasta cubrir el segundo 0 (con margen: si el primer pulso cae casi en
        # el 0, ese es el primer pulso; si no, una nota en el 0 acabaría un pulso tarde).
        while p[0] > paso * 0.25:
            p.insert(0, p[0] - paso)
        while p[-1] < duracion + paso:
            p.append(p[-1] + paso)
        self.pulsos = np.array(p)
        self.tempo = 60.0 / paso

    def a_pulso(self, t: float) -> float:
        return float(np.interp(t, self.pulsos, np.arange(len(self.pulsos))))

    def a_segundos(self, pulso: float) -> float:
        return float(np.interp(pulso, np.arange(len(self.pulsos)), self.pulsos))

    def hueco(self, t: float) -> int:
        """Semicorchea (entera) más cercana al instante t."""
        return round(self.a_pulso(t) * DIVISIONES)

    def segundos_de_hueco(self, h: int) -> float:
        return self.a_segundos(h / DIVISIONES)


# --------------------------------------------------------------------------- cuantización


@dataclass
class Evento:
    inicio: int  # en semicorcheas
    duracion: int
    notas: list[int] = field(default_factory=list)  # MIDI; vacío = silencio


def cuantiza(notas: list[Nota], rejilla: Rejilla) -> list[Evento]:
    """Una sola voz: acordes y silencios seguidos, sin solapes."""
    grupos: dict[int, list[tuple[int, int]]] = {}
    for n in notas:
        ini = rejilla.hueco(n.inicio)
        fin = max(ini + 1, rejilla.hueco(n.fin))
        grupos.setdefault(ini, []).append((n.midi, fin))

    eventos: list[Evento] = []
    cursor = 0
    inicios = sorted(grupos)
    for i, ini in enumerate(inicios):
        if ini > cursor:
            eventos.append(Evento(cursor, ini - cursor))
        siguiente = inicios[i + 1] if i + 1 < len(inicios) else None
        fin = max(f for _, f in grupos[ini])
        if siguiente is not None:
            fin = min(fin, siguiente)
            if siguiente - fin == 1 and fin - ini >= 2:  # nota soltada un pelín antes: sin silencio
                fin = siguiente
        eventos.append(Evento(ini, fin - ini, sorted({m for m, _ in grupos[ini]})))
        cursor = fin
    resto = -cursor % POR_COMPAS
    if resto or not eventos:
        eventos.append(Evento(cursor, resto or POR_COMPAS))
    return eventos


def trocea(inicio: int, duracion: int) -> list[tuple[int, int]]:
    """Parte un evento en trozos escribibles que no crucen la barra de compás."""
    trozos = []
    while duracion > 0:
        hasta_barra = POR_COMPAS - inicio % POR_COMPAS
        cabe = min(duracion, hasta_barra)
        d = next(x for x in DURACIONES if x <= cabe)
        trozos.append((inicio, d))
        inicio += d
        duracion -= d
    return trozos


# --------------------------------------------------------------------------- ABC


def altura_abc(midi: int, alteraciones: dict[tuple[str, int], str]) -> str:
    """Nota en ABC (Do central = C). `alteraciones` guarda lo que va sonando en el compás, porque
    en ABC un sostenido dura hasta la barra y hay que poner becuadro para quitarlo."""
    nombre = NOMBRES_INGLESES[midi % 12]
    letra, sostenido = nombre[0], len(nombre) > 1
    octava = midi // 12 - 1
    actual = alteraciones.get((letra, octava), "")
    signo = ""
    if sostenido and actual != "^":
        signo = "^"
        alteraciones[(letra, octava)] = "^"
    elif not sostenido and actual == "^":
        signo = "="
        alteraciones[(letra, octava)] = "="
    if octava >= 5:
        texto = letra.lower() + "'" * (octava - 5)
    else:
        texto = letra + "," * (4 - octava)
    return signo + texto


def _duracion_abc(d: int) -> str:
    return "" if d == 1 else str(d)  # L:1/16


@dataclass
class Voz:
    id: str
    nombre: str
    clave: str
    eventos: list[Evento]


def _compases(voz: Voz, rejilla: Rejilla, notacion: str, total: int):
    """Genera, por compás, trozos (texto, sílaba, info_de_evento_o_None)."""
    nombres = NOMBRES_LATINOS if notacion == "latina" else NOMBRES_INGLESES
    compases: list[list[tuple[str, str | None, tuple[float, float] | None]]] = [
        [] for _ in range(total)]
    for ev in voz.eventos:
        piezas = trocea(ev.inicio, ev.duracion)
        for k, (ini, d) in enumerate(piezas):
            compas = ini // POR_COMPAS
            if compas >= total:
                break
            if not ev.notas:
                compases[compas].append(("z" + _duracion_abc(d), None, None))
                continue
            ligada = k < len(piezas) - 1
            primera = k == 0
            silaba = nombres[ev.notas[0] % 12] if primera and len(ev.notas) == 1 else "*"
            tiempos = (rejilla.segundos_de_hueco(ini), rejilla.segundos_de_hueco(ini + d))
            compases[compas].append(((ev.notas, d, ligada), silaba, tiempos))
    return compases


def genera_abc(voces: list[Voz], rejilla: Rejilla, titulo: str, notacion: str = "latina",
               con_nombres: bool = False) -> tuple[str, list[dict]]:
    """Devuelve el texto ABC y la lista de eventos {char, inicio, fin} de las notas escritas."""
    total = max((ev.inicio + ev.duracion for v in voces for ev in v.eventos), default=0)
    total = max(1, -(-total // POR_COMPAS))
    cabecera = [
        "X:1",
        f"T:{titulo}",
        "M:4/4",
        "L:1/16",
        f"Q:1/4={round(rejilla.tempo)}",
    ]
    if len(voces) > 1:
        cabecera.append("%%score {" + " ".join(v.id for v in voces) + "}")
    for v in voces:
        cabecera.append(f'V:{v.id} clef={v.clave} name="{v.nombre}"')
    cabecera.append("K:C")
    partes = ["\n".join(cabecera) + "\n"]
    largo = len(partes[0])
    eventos: list[dict] = []

    def escribe(texto: str) -> int:
        nonlocal largo
        pos = largo
        partes.append(texto)
        largo += len(texto)
        return pos

    por_voz = [_compases(v, rejilla, notacion, total) for v in voces]
    for linea in range(0, total, COMPASES_POR_LINEA):
        for v, compases in zip(voces, por_voz):
            escribe(f"[V:{v.id}] ")
            silabas: list[str] = []
            for c in range(linea, min(linea + COMPASES_POR_LINEA, total)):
                alteraciones: dict[tuple[str, int], str] = {}
                for trozo, silaba, tiempos in compases[c]:
                    if isinstance(trozo, str):  # silencio
                        escribe(trozo + " ")
                        continue
                    notas, d, ligada = trozo
                    alturas = [altura_abc(m, alteraciones) for m in notas]
                    texto = alturas[0] if len(alturas) == 1 else "[" + "".join(alturas) + "]"
                    pos = escribe(texto + _duracion_abc(d) + ("-" if ligada else "") + " ")
                    eventos.append({"char": pos, "inicio": round(tiempos[0], 3),
                                    "fin": round(tiempos[1], 3)})
                    silabas.append(silaba)
                escribe("| ")
            escribe("\n")
            if con_nombres and silabas:
                escribe("w: " + " ".join(silabas) + "\n")
    return "".join(partes), eventos


# --------------------------------------------------------------------------- todo junto


def voces_para(notas: list[Nota], instrumento: str, rejilla: Rejilla) -> list[Voz]:
    """Reparte las notas en pentagramas según el instrumento."""
    if instrumento == "piano":
        derecha = [n for n in notas if n.midi >= 60]
        izquierda = [n for n in notas if n.midi < 60]
        return [Voz("MD", "Mano der.", "treble", cuantiza(derecha, rejilla)),
                Voz("MI", "Mano izq.", "bass", cuantiza(izquierda, rejilla))]
    if instrumento == "bajo":
        clave = "bass"
    elif notas and float(np.median([n.midi for n in notas])) < 57:
        clave = "bass"
    else:
        clave = "treble"
    return [Voz("V1", "", clave, cuantiza(notas, rejilla))]


def partitura(notas: list[Nota], pulsos: list[float], duracion: float, instrumento: str,
              titulo: str, notacion: str = "latina") -> dict:
    rejilla = Rejilla(pulsos, duracion)
    voces = voces_para(notas, instrumento, rejilla)
    abc, eventos = genera_abc(voces, rejilla, titulo, notacion)
    abc_nombres, eventos_nombres = genera_abc(voces, rejilla, titulo, notacion, con_nombres=True)
    return {"tempo": round(rejilla.tempo), "abc": abc, "eventos": eventos,
            "abc_nombres": abc_nombres, "eventos_nombres": eventos_nombres}
