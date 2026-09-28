# Notas de un instrumento

Le das una canción (MP3, WAV, FLAC, OGG...) y un instrumento, y te dice qué notas toca y cuándo.
Deja además un **CSV** (para Excel) y un **MIDI** (para abrirlo en MuseScore, GarageBand,
un DAW..., donde puedes verlo como partitura o piano roll).

```
$ python notas_instrumento.py cancion.mp3 --instrumento bajo
    inicio  duración  nota
  0:00.512     0.45s  Mi1
  0:01.004     0.49s  Mi1
  0:01.498     0.49s  Sol1
  ...
```

(Ese ejemplo es ilustrativo, no una salida real.)

## Cómo funciona

1. **Separación**: [Demucs](https://github.com/facebookresearch/demucs) (modelo `htdemucs_6s`,
   de Meta, MIT) divide la mezcla en 6 pistas: voz, bajo, guitarra, piano, batería y "otros".
2. **Transcripción**: [Basic Pitch](https://github.com/spotify/basic-pitch) (de Spotify,
   Apache 2.0) convierte la pista del instrumento en notas.
3. Limpieza: se descartan las notas fuera del registro del instrumento y, en los que solo tocan
   una nota a la vez (voz, flauta, saxo, trompeta), se queda la más fuerte cuando se solapan.

## Instalación

Necesita Python 3.10 o 3.11 (Basic Pitch aún no instala bien en versiones más nuevas).

```bash
cd notas-instrumento
python -m venv .venv && source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

La primera vez que separe una canción, Demucs descarga su modelo (unos 50-80 MB).

## Uso

```bash
python notas_instrumento.py cancion.mp3 -i guitarra
python notas_instrumento.py cancion.mp3 -i voz --notacion inglesa     # C D E en vez de Do Re Mi
python notas_instrumento.py solo_piano.mp3 -i piano --sin-separar     # el audio ya es solo piano
python notas_instrumento.py cancion.mp3 -i bajo --guardar-pista       # guarda también el bajo aislado
python notas_instrumento.py --instrumentos                            # lista de instrumentos
```

| Opción | Qué hace |
|---|---|
| `-i`, `--instrumento` | `voz`, `bajo`, `guitarra`, `piano`, `violin`, `flauta`, `saxofon`, `trompeta`, `otros` (acepta tildes y algunos sinónimos: `saxo`, `teclado`, `bass`...) |
| `-o`, `--salida` | carpeta de resultados (por defecto `salida/`) |
| `--notacion` | `latina` (Do Re Mi, por defecto) o `inglesa` (C D E) |
| `--sin-separar` | no separa pistas; úsalo si la grabación ya es el instrumento solo (sale mucho mejor) |
| `--sensibilidad` | entre 0 y 1 (por defecto 0.5). Más alto = más notas, también más falsas |
| `--duracion-min` | descarta notas más cortas que esto, en milisegundos (por defecto 100) |
| `--guardar-pista` | guarda el WAV del instrumento separado, útil para escuchar qué se ha transcrito |

Las octavas usan la notación científica: **Do4 = do central** (MIDI 60). En algunos métodos
españoles ese mismo do se llama Do3.

Resultados, en `salida/`: `<canción>_<instrumento>.csv`, `.mid` y (con `--guardar-pista`) `.wav`.

## Limitaciones (léelas)

- **Es aproximado.** Ni la separación ni la transcripción son perfectas: habrá notas de más
  (a menudo la misma nota una octava arriba, por los armónicos), notas perdidas y ritmos algo
  desplazados. Sirve como punto de partida para sacar una canción, no como partitura final.
- Violín, flauta, saxo y trompeta **no tienen pista propia** en el modelo: salen de la pista
  "otros", que mezcla todo lo que no es voz, bajo, guitarra, piano o batería. Si en la canción hay
  varios de esos instrumentos, se transcribirán juntos (solo se filtra por registro).
- La pista de piano de `htdemucs_6s` es la más floja del modelo (lo dicen sus propios autores).
- Si hay dos guitarras, sale la suma de las dos.
- La batería no se puede transcribir en notas (no tiene altura).
- Sin GPU, separar una canción de 4 minutos tarda varios minutos.

## Pruebas

```bash
pip install pytest
python -m pytest
```

Las pruebas no usan los modelos (sustituyen separación y transcripción por funciones falsas).
