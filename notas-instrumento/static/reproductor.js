"use strict";
// Parte común de la app local (static/app.js) y de la web de GitHub Pages (web/web.js):
// lista de instrumentos, reproductor y partitura iluminada.
//
// Quien lo usa llama a Reproductor.abre(cancion, fuente), donde `fuente` sabe de dónde sacar
// las cosas:
//   fuente.audio(modo, pista)              -> Promise<url>  (modo: "mezcla" | "solo" | "sin")
//   fuente.partitura(pista, sens, notacion) -> Promise<{cantidad, tempo, abc, eventos,
//                                                       abc_nombres, eventos_nombres}>
//   fuente.midi(pista, sens)               -> Promise<url>
//   fuente.modos(pista)                    -> modos disponibles para esa pista

const Reproductor = (() => {
  const $ = (s) => document.querySelector(s);

  const estado = {
    cancion: null,
    fuente: null,
    instrumento: null, // objeto pista
    modo: "mezcla",
    partitura: null,
    eventos: [], // {inicio, fin, elementos: [svg...]}
    activos: [],
    peticion: 0, // para descartar respuestas viejas al cambiar rápido de instrumento
    peticionAudio: 0,
    urlAudio: null,
  };

  function formatoTiempo(s) {
    if (!isFinite(s)) return "0:00";
    const m = Math.floor(s / 60);
    return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  }

  // ------------------------------------------------------------------------- canción

  function abre(cancion, fuente) {
    cierra();
    estado.cancion = cancion;
    estado.fuente = fuente;
    $("#titulo-cancion").textContent = cancion.titulo;
    const cont = $("#instrumentos");
    cont.innerHTML = "";
    for (const p of cancion.pistas) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "inst" + (p.suena ? "" : " no-suena");
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", "false");
      b.dataset.clave = p.clave;
      const nombre = document.createElement("span");
      nombre.className = "nombre";
      nombre.textContent = p.nombre;
      const est = document.createElement("span");
      est.className = "estado";
      const pct = Math.round(p.actividad * 100);
      est.textContent = (p.suena ? `suena ~${pct}% del tiempo` : `apenas suena (~${pct}%)`) + (p.partitura ? "" : " · sin partitura");
      b.append(nombre, est);
      b.addEventListener("click", () => eligeInstrumento(p));
      cont.append(b);
    }
    $("#bloque-reproductor").hidden = true;
    $("#bloque-partitura").hidden = true;
    $("#aviso-partitura").hidden = true;
    const primera = cancion.pistas.find((p) => p.suena && p.partitura);
    if (primera) eligeInstrumento(primera);
  }

  function cierra() {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    estado.urlAudio = null;
    estado.cancion = null;
    estado.partitura = null;
    estado.eventos = [];
    estado.activos = [];
    estado.peticion++;
    estado.peticionAudio++;
    ponModo("mezcla");
  }

  function eligeInstrumento(p) {
    estado.instrumento = p;
    for (const b of document.querySelectorAll(".inst")) {
      b.setAttribute("aria-checked", String(b.dataset.clave === p.clave));
    }
    for (const s of document.querySelectorAll(".nombre-inst")) s.textContent = p.nombre.split(" ")[0].toLowerCase();
    const modos = estado.fuente.modos(p);
    for (const b of document.querySelectorAll(".segmentado button")) {
      b.hidden = !modos.includes(b.dataset.modo);
    }
    if (!modos.includes(estado.modo)) ponModo("mezcla");
    $("#bloque-reproductor").hidden = false;
    ponAudio(estado.modo);
    cargaPartitura();
  }

  // ------------------------------------------------------------------------- audio

  const audio = $("#audio");
  const play = $("#play");
  const posicion = $("#posicion");
  let arrastrando = false;

  function ponModo(modo) {
    estado.modo = modo;
    for (const o of document.querySelectorAll(".segmentado button")) o.classList.toggle("activo", o.dataset.modo === modo);
  }

  async function ponAudio(modo) {
    const n = ++estado.peticionAudio;
    const t = audio.currentTime || 0;
    const sonando = !audio.paused;
    play.disabled = true;
    let url;
    try {
      url = await estado.fuente.audio(modo, estado.instrumento);
    } catch (e) {
      if (n === estado.peticionAudio) {
        play.disabled = false;
        $("#info-partitura").textContent = `No se pudo cargar el audio: ${e.message}`;
      }
      return;
    }
    if (n !== estado.peticionAudio) return;
    play.disabled = false;
    if (url === estado.urlAudio) return;
    estado.urlAudio = url;
    audio.src = url;
    audio.addEventListener("loadedmetadata", () => {
      audio.currentTime = Math.min(t, audio.duration || t);
      if (sonando) audio.play();
    }, { once: true });
  }

  for (const b of document.querySelectorAll(".segmentado button")) {
    b.addEventListener("click", () => {
      ponModo(b.dataset.modo);
      ponAudio(estado.modo);
    });
  }

  play.addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  audio.addEventListener("play", () => { play.textContent = "❚❚"; play.setAttribute("aria-label", "Pausa"); bucle(); });
  audio.addEventListener("pause", () => { play.textContent = "▶"; play.setAttribute("aria-label", "Reproducir"); });
  audio.addEventListener("loadedmetadata", () => { $("#total").textContent = formatoTiempo(audio.duration); });
  audio.addEventListener("timeupdate", actualizaPosicion);
  audio.addEventListener("seeked", () => { actualizaPosicion(); ilumina(true); });
  posicion.addEventListener("input", () => {
    arrastrando = true;
    $("#tiempo").textContent = formatoTiempo(posicion.value / 1000 * audio.duration);
  });
  posicion.addEventListener("change", () => {
    arrastrando = false;
    if (isFinite(audio.duration)) audio.currentTime = posicion.value / 1000 * audio.duration;
  });
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || !estado.cancion || $("#bloque-reproductor").hidden
        || ["INPUT", "SELECT", "BUTTON", "TEXTAREA"].includes(e.target.tagName)) return;
    e.preventDefault();
    play.click();
  });

  function actualizaPosicion() {
    $("#tiempo").textContent = formatoTiempo(audio.currentTime);
    if (!arrastrando && isFinite(audio.duration)) posicion.value = Math.round(1000 * audio.currentTime / audio.duration);
  }

  function bucle() {
    ilumina(false);
    if (!audio.paused) requestAnimationFrame(bucle);
  }

  // ------------------------------------------------------------------------- partitura

  const opciones = { nombres: $("#nombres"), inglesa: $("#inglesa"), sensibilidad: $("#sensibilidad") };
  opciones.nombres.addEventListener("change", dibuja);
  opciones.inglesa.addEventListener("change", cargaPartitura);
  opciones.sensibilidad.addEventListener("change", cargaPartitura);

  $("#midi").addEventListener("click", async (e) => {
    e.preventDefault();
    const p = estado.instrumento;
    if (!p?.partitura) return;
    const url = await estado.fuente.midi(p, opciones.sensibilidad.value);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${estado.cancion.titulo} - ${p.clave}.mid`;
    document.body.append(a);
    a.click();
    a.remove();
  });

  async function cargaPartitura() {
    const p = estado.instrumento;
    if (!p) return;
    const bloque = $("#bloque-partitura");
    const aviso = $("#aviso-partitura");
    const n = ++estado.peticion;
    estado.eventos = [];
    estado.activos = [];
    $("#midi").hidden = !p.partitura;
    if (!p.partitura) {
      bloque.hidden = true;
      aviso.hidden = false;
      aviso.textContent = "La batería no toca notas con altura, así que no tiene partitura. Puedes escucharla con «Solo».";
      return;
    }
    aviso.hidden = true;
    bloque.hidden = false;
    $("#partitura").innerHTML = "";
    $("#info-partitura").textContent = `Cargando las notas de ${p.nombre.toLowerCase()}...`;

    let datos;
    try {
      datos = await estado.fuente.partitura(p, opciones.sensibilidad.value, opciones.inglesa.checked ? "inglesa" : "latina");
    } catch (e) {
      if (n === estado.peticion) $("#info-partitura").textContent = `No se pudo sacar la partitura: ${e.message}`;
      return;
    }
    if (n !== estado.peticion) return;
    estado.partitura = datos;
    $("#info-partitura").textContent =
      `${datos.cantidad} notas · tempo detectado ≈ ${datos.tempo} negras/min · compás supuesto 4/4. Pulsa una nota para ir a ese momento.`;
    dibuja();
  }

  function dibuja() {
    const d = estado.partitura;
    if (!d || !estado.instrumento?.partitura) return;
    const conNombres = opciones.nombres.checked;
    const abc = conNombres ? d.abc_nombres : d.abc;
    const eventos = conNombres ? d.eventos_nombres : d.eventos;
    const porCaracter = new Map(eventos.map((e) => [e.char, e]));
    // abcjs a veces incluye en la nota el espacio de antes: se busca en todo su rango de texto.
    const eventoDe = (elem) => {
      for (let c = elem.startChar; c < elem.endChar; c++) if (porCaracter.has(c)) return porCaracter.get(c);
      return null;
    };

    // Compases por línea según el ancho: en el móvil, 2; en pantallas grandes, 4.
    const ancho = Math.max(280, $("#partitura").clientWidth - 24);
    const [visual] = ABCJS.renderAbc("partitura", abc, {
      staffwidth: ancho,
      wrap: { minSpacing: 1.6, maxSpacing: 2.8, preferredMeasuresPerLine: ancho < 560 ? 2 : 4 },
      add_classes: true,
      paddingleft: 12, paddingright: 12,
      clickListener: (elem) => {
        const e = eventoDe(elem);
        if (e) audio.currentTime = e.inicio;
      },
    });

    // Une cada evento (tiempo) con los elementos SVG de su nota.
    const lista = [];
    for (const linea of visual.lines) {
      for (const pentagrama of linea.staff || []) {
        for (const voz of pentagrama.voices || []) {
          for (const elem of voz) {
            const e = elem.el_type === "note" ? eventoDe(elem) : null;
            if (e && elem.abselem) lista.push({ inicio: e.inicio, fin: e.fin, elementos: elem.abselem.elemset || [] });
          }
        }
      }
    }
    lista.sort((a, b) => a.inicio - b.inicio);
    estado.eventos = lista;
    estado.activos = [];
    ilumina(true);
  }

  function ilumina(forzarScroll) {
    const t = audio.currentTime;
    const ev = estado.eventos;
    // último evento con inicio <= t (búsqueda binaria), y unos cuantos anteriores por los acordes/voces
    let lo = 0, hi = ev.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (ev[m].inicio <= t) lo = m + 1; else hi = m; }
    const ahora = [];
    for (let i = lo - 1; i >= 0 && i >= lo - 24; i--) if (ev[i].fin > t) ahora.push(ev[i]);
    const iguales = ahora.length === estado.activos.length && ahora.every((e, i) => e === estado.activos[i]);
    if (iguales && !forzarScroll) return;

    for (const e of estado.activos) for (const el of e.elementos) el.classList.remove("nota-activa");
    for (const e of ahora) for (const el of e.elementos) el.classList.add("nota-activa");
    estado.activos = ahora;

    const primero = ahora[0]?.elementos[0] || (lo > 0 ? ev[lo - 1].elementos[0] : null);
    if (primero && $("#seguir").checked) {
      const r = primero.getBoundingClientRect();
      const alto = $("#bloque-reproductor").getBoundingClientRect().bottom;
      if (r.top < alto + 20 || r.bottom > innerHeight - 40) {
        window.scrollBy({ top: r.top - (alto + innerHeight) / 2 + 40, behavior: forzarScroll ? "auto" : "smooth" });
      }
    }
  }

  let anchoAnterior = 0;
  let redibujo = null;
  window.addEventListener("resize", () => {
    clearTimeout(redibujo);
    redibujo = setTimeout(() => {
      const ancho = $("#partitura").clientWidth;
      if (Math.abs(ancho - anchoAnterior) > 40) { anchoAnterior = ancho; dibuja(); }
    }, 250);
  });

  return { abre, cierra };
})();
