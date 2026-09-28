# Notas de un instrumento

Sube una canción (MP3, WAV, FLAC, OGG, M4A...), la aplicación separa los instrumentos, te dice
cuáles suenan, eliges uno y **escuchas la canción mientras se ilumina su partitura**.

Hay dos formas de usarlo:

- **Aplicación web** (`app.py`): se ejecuta en tu ordenador y se usa desde el navegador.
- **Línea de comandos** (`notas_instrumento.py`): saca las notas a una tabla, un CSV y un MIDI.

## Aplicación web

```bash
python app.py            # abre http://127.0.0.1:5000 en el navegador
```

1. Arrastra la canción a la página (o pulsa para elegirla).
2. Espera a que separe los instrumentos (sin tarjeta gráfica, unos minutos por canción).
3. Salen los 6 grupos que distingue el modelo: **voz, guitarra, bajo, piano/teclado, otros
   instrumentos y batería**, cada uno con cuánto tiempo suena. Los que apenas suenan salen en gris,
   pero puedes elegirlos igualmente.
4. Elige uno: aparece su partitura. Dale a ▶ y la nota que suena se ilumina y la página la sigue.
   - **Canción / Solo … / Sin …**: escuchar la mezcla, solo ese instrumento, o la canción sin él
     (para tocar encima).
   - **Nombres de las notas**: escribe Do, Re, Mi... debajo de cada nota.
   - **Notas: menos / normal / más**: sensibilidad de la detección.
   - Pulsa una nota de la partitura para saltar a ese momento. Barra espaciadora = play/pausa.
   - **Descargar MIDI** para abrirlo en MuseScore, GarageBand, etc.

Todo se queda en tu ordenador: las canciones y las pistas separadas se guardan en `trabajos/`
(puedes borrar esa carpeta cuando quieras). Si recargas la página, la dirección
(`...#<código>`) vuelve a abrir la misma canción sin analizarla otra vez.

### Qué significa «identificar los instrumentos»

La app **no reconoce instrumentos de cualquier tipo**: el modelo separa siempre en esos 6 grupos
fijos y la app mide cuánto suena cada uno. Un violín, una trompeta o un sintetizador acaban todos
en «otros instrumentos», juntos. El umbral para decidir que un grupo «suena» está puesto a ojo,
no calibrado.

### Cómo se hace la partitura

- El ritmo se ajusta a semicorcheas sobre el pulso que detecta
  [librosa](https://librosa.org), así que sigue los cambios de tempo de la canción.
- **Se supone compás de 4/4** y que el primer pulso detectado es el principio de un compás
  (no se detecta el compás real, ni la tonalidad: las alteraciones se escriben con sostenidos).
- Las notas que empiezan a la vez se escriben como acorde; piano a dos pentagramas (separados
  en el do central); bajo en clave de fa, a la altura real (más grave de como se escribe
  normalmente para bajo, que va una octava por encima).
- Se dibuja con [abcjs](https://www.abcjs.net/) (MIT), incluido en `static/vendor/`.

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

## Línea de comandos

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
- Sin GPU, separar una canción de 4 minutos tarda varios minutos (no lo he medido con precisión).

## Pruebas

```bash
pip install pytest
python -m pytest
```

Las pruebas no usan los modelos (sustituyen separación, transcripción y detección de pulso por
funciones falsas), así que no comprueban la calidad de la separación ni de la transcripción.
