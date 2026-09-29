# Diario de peso

Página web (un solo `index.html`, sin dependencias ni servidor) para apuntar el peso por voz o a mano,
con fecha y hora, y ver su evolución en una gráfica.

Se publica con GitHub Pages en https://ammdg.github.io/APPs/diario-peso/ (la dirección antigua,
https://ammdg.github.io/APPs/, redirige aquí).

## Qué hace

- Micrófono: dices el peso («ochenta y dos con cinco», «82,5 kilos», «82 y medio») y lo guarda,
  con o sin pedir confirmación.
- Campo de texto para escribirlo (o dictarlo con el micrófono del teclado del iPhone).
- Gráfica de 7 días, 30 días, 90 días, 1 año o todo, con la media de 7 días.
- Lista de registros: al tocar uno se puede corregir el valor, la fecha o eliminarlo.
- Exportar a CSV e importar una copia CSV.

## Dónde se guardan los datos

En el `localStorage` del navegador (clave `peso.v1`), es decir, solo en ese dispositivo y ese navegador.
Conviene exportar el CSV de vez en cuando como copia de seguridad.

Cuando la página se abre como artefacto de claude.ai, además se sincroniza con la cuenta usando
`window.claude` (capacidades `db`, `user` y `downloads`); fuera de claude.ai esa parte simplemente no se activa.
