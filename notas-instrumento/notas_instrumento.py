"""Saca las notas que toca un instrumento en una canción (MP3, WAV, FLAC...).

Dos pasos:
  1. Separación: Demucs (modelo htdemucs_6s, de Meta, licencia MIT) divide la mezcla en pistas:
     voz, bajo, guitarra, piano, batería y "otros".
  2. Transcripción: Basic Pitch (de Spotify, licencia Apache 2.0) convierte la pista elegida en
     notas (inicio, fin, altura, intensidad).

El resultado es aproximado: la separación deja restos de otros instrumentos y la transcripción
automática se equivoca (sobre todo con octavas y notas muy rápidas o muy suaves).

Uso:
    python notas_instrumento.py cancion.mp3 --instrumento guitarra
    python notas_instrumento.py cancion.mp3 -i bajo --notacion inglesa
    python notas_instrumento.py solo_de_piano.mp3 -i piano --sin-separar
    python notas_instrumento.py --instrumentos           # lista de instrumentos que entiende
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MODELO_DEMUCS = "htdemucs_6s"
FRECUENCIA_DEMUCS = 44100

NOMBRES_LATINOS = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
NOMBRES_INGLESES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


class ErrorNotas(Exception):
    """Error que se le explica al usuario tal cual (sin traza)."""


# --------------------------------------------------------------------------- instrumentos


@dataclass(frozen=True)
class Instrumento:
    nombre: str
    pista: str  # pista de Demucs de la que sale
    nota_min: int  # MIDI; se descartan las notas fuera del registro del instrumento
    nota_max: int
    monofonico: bool  # solo puede tocar una nota a la vez


# Registros aproximados (MIDI 60 = Do4 = do central). Algo de margen por si hay afinaciones bajas.
INSTRUMENTOS = {
    "voz": Instrumento("voz", "vocals", 36, 88, True),  # Do2 - Mi6
    "bajo": Instrumento("bajo", "bass", 23, 67, False),  # Si0 - Sol4 (bajo de 5 cuerdas)
    "guitarra": Instrumento("guitarra", "guitar", 38, 88, False),  # Re2 (drop D) - Mi6
    "piano": Instrumento("piano", "piano", 21, 108, False),  # La0 - Do8
    "violin": Instrumento("violín", "other", 55, 103, False),  # Sol3 - Sol7
    "flauta": Instrumento("flauta", "other", 59, 98, True),  # Si3 - Re7
    "saxofon": Instrumento("saxofón", "other", 44, 89, True),  # Sol#2 - Fa6 (tenor + alto)
    "trompeta": Instrumento("trompeta", "other", 52, 86, True),  # Mi3 - Re6
    "otros": Instrumento("otros", "other", 21, 108, False),
}

ALIAS = {
    "voces": "voz", "vocal": "voz", "cantante": "voz", "vocals": "voz", "voice": "voz",
    "bass": "bajo", "contrabajo": "bajo",
    "guitar": "guitarra", "guitarras": "guitarra",
    "teclado": "piano", "teclados": "piano", "keys": "piano",
    "viola": "violin", "violonchelo": "otros", "chelo": "otros",
    "flute": "flauta",
    "saxo": "saxofon", "sax": "saxofon", "saxophone": "saxofon",
    "trumpet": "trompeta",
    "sintetizador": "otros", "synth": "otros", "cuerdas": "otros", "other": "otros",
}
SIN_ALTURA = {"bateria", "drums", "percusion", "caja", "bombo"}


def _normaliza(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def busca_instrumento(texto: str) -> Instrumento:
    clave = _normaliza(texto)
    if clave in SIN_ALTURA:
        raise ErrorNotas(
            "La batería no toca notas con altura, así que no se puede sacar su melodía. "
            "Elige un instrumento con notas (voz, bajo, guitarra, piano...)."
        )
    clave = ALIAS.get(clave, clave)
    if clave not in INSTRUMENTOS:
        raise ErrorNotas(
            f"No conozco el instrumento «{texto}». Usa uno de estos: {', '.join(INSTRUMENTOS)} "
            "(o 'otros' para cualquier instrumento que no sea voz, bajo, guitarra, piano ni batería)."
        )
    return INSTRUMENTOS[clave]


# --------------------------------------------------------------------------- notas


@dataclass
class Nota:
    inicio: float  # segundos
    fin: float
    midi: int
    intensidad: float  # 0-1

    @property
    def duracion(self) -> float:
        return self.fin - self.inicio


def nombre_nota(midi: int, notacion: str = "latina") -> str:
    """Nombre con octava científica: MIDI 60 -> Do4 (latina) o C4 (inglesa)."""
    nombres = NOMBRES_LATINOS if notacion == "latina" else NOMBRES_INGLESES
    return f"{nombres[midi % 12]}{midi // 12 - 1}"


def formatea_tiempo(segundos: float) -> str:
    minutos, resto = divmod(segundos, 60)
    return f"{int(minutos)}:{resto:06.3f}"


def filtra_registro(notas: list[Nota], instrumento: Instrumento) -> list[Nota]:
    return [n for n in notas if instrumento.nota_min <= n.midi <= instrumento.nota_max]


def monofoniza(notas: list[Nota]) -> list[Nota]:
    """Para instrumentos de una sola nota a la vez: si dos notas se solapan, gana la más fuerte y
    a la otra se le recorta el trozo solapado (o se descarta si queda en nada)."""
    resultado: list[Nota] = []
    for nota in sorted(notas, key=lambda n: (n.inicio, -n.intensidad)):
        nota = Nota(nota.inicio, nota.fin, nota.midi, nota.intensidad)
        if resultado and nota.inicio < resultado[-1].fin:
            anterior = resultado[-1]
            if nota.intensidad > anterior.intensidad:
                anterior.fin = nota.inicio
                if anterior.duracion <= 0:
                    resultado.pop()
            else:
                nota.inicio = anterior.fin
                if nota.duracion <= 0:
                    continue
        resultado.append(nota)
    return resultado


# --------------------------------------------------------------------------- audio


def separa(audio: Path, progreso=None) -> tuple[dict, int]:
    """Separa la mezcla con Demucs. Devuelve ({pista: array (muestras, canales)}, frecuencia).
    `progreso`, si se da, recibe la fracción completada (0-1) según avanza."""
    import librosa
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    print(f"Cargando el modelo de separación ({MODELO_DEMUCS})...", file=sys.stderr)
    modelo = get_model(MODELO_DEMUCS)
    modelo.eval()

    ondas, _ = librosa.load(str(audio), sr=modelo.samplerate, mono=False)
    if ondas.ndim == 1:
        ondas = ondas[None].repeat(modelo.audio_channels, axis=0)
    mezcla = torch.from_numpy(ondas[: modelo.audio_channels]).float()
    muestras = mezcla.shape[-1]

    def avisa(d: dict) -> None:
        if progreso and d.get("state") == "end":
            # Aproximado: segment_offset es donde empieza el trozo recién terminado.
            hecho = d["model_idx_in_bag"] + d["segment_offset"] / muestras
            progreso(min(1.0, hecho / d["models"]))

    # Misma normalización que el comando `demucs`.
    referencia = mezcla.mean(0)
    media, desviacion = referencia.mean(), referencia.std() + 1e-8
    dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Separando pistas en {dispositivo} (en CPU tarda unos minutos)...", file=sys.stderr)
    with torch.no_grad():
        fuentes = apply_model(modelo, ((mezcla - media) / desviacion)[None], device=dispositivo,
                              progress=progreso is None, callback=avisa)[0]
    if progreso:
        progreso(1.0)
    fuentes = fuentes * desviacion + media
    return ({nombre: fuentes[i].cpu().numpy().T for i, nombre in enumerate(modelo.sources)},
            modelo.samplerate)


def separa_pista(audio: Path, pista: str, destino: Path) -> Path:
    """Separa `pista` de la mezcla con Demucs y la guarda como WAV en `destino`."""
    import soundfile as sf

    pistas, frecuencia = separa(audio)
    if pista not in pistas:
        raise ErrorNotas(f"El modelo no tiene la pista '{pista}' (tiene: {list(pistas)}).")
    sf.write(str(destino), pistas[pista], frecuencia)
    return destino


def transcribe_varias(audio: Path, instrumento: Instrumento, sensibilidades: list[float],
                      duracion_min_ms: float) -> dict[float, list[Nota]]:
    """Pasa Basic Pitch una sola vez y saca las notas con cada sensibilidad (0-1)."""
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.constants import AUDIO_SAMPLE_RATE, FFT_HOP
    from basic_pitch.inference import run_inference
    from basic_pitch.note_creation import model_output_to_notes
    import librosa

    print("Transcribiendo notas (Basic Pitch)...", file=sys.stderr)
    salida = run_inference(str(audio), ICASSP_2022_MODEL_PATH)
    resultado = {}
    for s in sensibilidades:
        _, eventos = model_output_to_notes(
            salida,
            onset_thresh=1 - s,
            frame_thresh=0.3,
            min_note_len=int(round(duracion_min_ms / 1000 * AUDIO_SAMPLE_RATE / FFT_HOP)),
            min_freq=float(librosa.midi_to_hz(instrumento.nota_min)),
            max_freq=float(librosa.midi_to_hz(instrumento.nota_max)),
        )
        resultado[s] = [Nota(float(ini), float(fin), int(tono), float(amp))
                        for ini, fin, tono, amp, *_ in eventos]
    return resultado


def transcribe(audio: Path, instrumento: Instrumento, sensibilidad: float,
               duracion_min_ms: float) -> tuple[list[Nota], None]:
    """Notas detectadas por Basic Pitch con una sensibilidad."""
    return transcribe_varias(audio, instrumento, [sensibilidad], duracion_min_ms)[sensibilidad], None


def limpia(notas: list[Nota], instrumento: Instrumento) -> list[Nota]:
    """Quita lo que está fuera del registro, deja una nota a la vez si toca, y ordena."""
    notas = filtra_registro(notas, instrumento)
    if instrumento.monofonico:
        notas = monofoniza(notas)
    return sorted(notas, key=lambda n: (n.inicio, n.midi))


def guarda_midi(notas: list[Nota], destino: Path, instrumento: Instrumento) -> None:
    import pretty_midi

    programas = {"voz": 53, "bajo": 33, "guitarra": 25, "piano": 0, "violín": 40,
                 "flauta": 73, "saxofón": 65, "trompeta": 56}  # General MIDI, base 0
    pm = pretty_midi.PrettyMIDI()
    pista = pretty_midi.Instrument(program=programas.get(instrumento.nombre, 0),
                                   name=instrumento.nombre)
    for n in notas:
        pista.notes.append(pretty_midi.Note(velocity=max(1, min(127, round(n.intensidad * 127))),
                                            pitch=n.midi, start=n.inicio, end=n.fin))
    pm.instruments.append(pista)
    pm.write(str(destino))


def guarda_csv(notas: list[Nota], destino: Path, notacion: str) -> None:
    with destino.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["inicio_s", "fin_s", "duracion_s", "nota", "midi", "intensidad"])
        for n in notas:
            w.writerow([f"{n.inicio:.3f}", f"{n.fin:.3f}", f"{n.duracion:.3f}",
                        nombre_nota(n.midi, notacion), n.midi, f"{n.intensidad:.2f}"])


def tabla(notas: list[Nota], notacion: str) -> str:
    lineas = [f"{'inicio':>10}  {'duración':>8}  nota"]
    for n in notas:
        lineas.append(f"{formatea_tiempo(n.inicio):>10}  {n.duracion:7.2f}s  "
                      f"{nombre_nota(n.midi, notacion)}")
    return "\n".join(lineas)


# --------------------------------------------------------------------------- main


def procesa(audio: Path, instrumento: Instrumento, *, separar: bool, salida: Path,
            notacion: str, sensibilidad: float, duracion_min_ms: float,
            guardar_pista: bool) -> list[Nota]:
    salida.mkdir(parents=True, exist_ok=True)
    base = salida / f"{audio.stem}_{_normaliza(instrumento.nombre).replace(' ', '_')}"

    with tempfile.TemporaryDirectory() as tmp:
        pista = audio
        if separar:
            destino = base.with_suffix(".wav") if guardar_pista else Path(tmp) / "pista.wav"
            pista = separa_pista(audio, instrumento.pista, destino)
        notas, _ = transcribe(pista, instrumento, sensibilidad, duracion_min_ms)

    notas = limpia(notas, instrumento)

    guarda_csv(notas, base.with_suffix(".csv"), notacion)
    guarda_midi(notas, base.with_suffix(".mid"), instrumento)
    return notas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Saca las notas que toca un instrumento en una canción.")
    p.add_argument("audio", nargs="?", type=Path, help="fichero de audio (mp3, wav, flac, ogg...)")
    p.add_argument("-i", "--instrumento", help="voz, bajo, guitarra, piano, violín, flauta, ...")
    p.add_argument("-o", "--salida", type=Path, default=Path("salida"),
                   help="carpeta donde dejar el CSV y el MIDI (por defecto: ./salida)")
    p.add_argument("--notacion", choices=["latina", "inglesa"], default="latina",
                   help="Do Re Mi... o C D E... (por defecto: latina)")
    p.add_argument("--sin-separar", action="store_true",
                   help="no separar pistas: el audio ya es el instrumento solo")
    p.add_argument("--sensibilidad", type=float, default=0.5,
                   help="0-1; más alto = detecta más notas (y más falsas). Por defecto 0.5")
    p.add_argument("--duracion-min", type=float, default=100,
                   help="descarta notas más cortas que esto, en ms (por defecto 100)")
    p.add_argument("--guardar-pista", action="store_true",
                   help="guarda también el WAV del instrumento separado")
    p.add_argument("--instrumentos", action="store_true", help="lista los instrumentos y sale")
    a = p.parse_args(argv)

    if a.instrumentos:
        for clave, ins in INSTRUMENTOS.items():
            print(f"{clave:10} {nombre_nota(ins.nota_min)}-{nombre_nota(ins.nota_max)}"
                  f"{'  (una nota a la vez)' if ins.monofonico else ''}")
        return 0
    if a.audio is None or a.instrumento is None:
        p.error("hacen falta el fichero de audio y --instrumento")

    try:
        if not a.audio.is_file():
            raise ErrorNotas(f"No encuentro el fichero {a.audio}")
        if not 0 < a.sensibilidad < 1:
            raise ErrorNotas("--sensibilidad tiene que estar entre 0 y 1 (sin incluirlos)")
        instrumento = busca_instrumento(a.instrumento)
        if instrumento.pista == "other" and instrumento.nombre != "otros" and not a.sin_separar:
            print(f"Aviso: el modelo no tiene pista propia de {instrumento.nombre}; se usa la pista "
                  "'otros', que puede incluir más instrumentos.", file=sys.stderr)
        notas = procesa(a.audio, instrumento, separar=not a.sin_separar, salida=a.salida,
                        notacion=a.notacion, sensibilidad=a.sensibilidad,
                        duracion_min_ms=a.duracion_min, guardar_pista=a.guardar_pista)
    except ErrorNotas as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(tabla(notas, a.notacion))
    print(f"\n{len(notas)} notas. Guardado en {a.salida}/ (CSV y MIDI).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
