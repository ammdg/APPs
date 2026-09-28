"use strict";

const $ = (s) => document.querySelector(s);
const pasos = { subir: $("#paso-subir"), analizar: $("#paso-analizar"), cancion: $("#paso-cancion") };

const estado = {
  id: null,
  cancion: null,
  instrumento: null, // objeto pista
  modo: "mezcla",
  partitura: null, // respuesta del servidor
  eventos: [], // {inicio, fin, elementos: [svg...]}
  activos: [],
  peticion: 0, // para descartar respuestas viejas al cambiar rápido de instrumento
};

function muestra(paso) {
  for (const [nombre, el] of Object.entries(pasos)) el.hidden = nombre !== paso;
}

function formatoTiempo(s) {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

// --------------------------------------------------------------------------- subir

const zona = $("#zona");
const entrada = $("#fichero");
zona.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); entrada.click(); }
});
zona.addEventListener("dragover", (e) => { e.preventDefault(); zona.classList.add("encima"); });
zona.addEventListener("dragleave", () => zona.classList.remove("encima"));
zona.addEventListener("drop", (e) => {
  e.preventDefault();
  zona.classList.remove("encima");
  if (e.dataTransfer.files.length) sube(e.dataTransfer.files[0]);
});
entrada.addEventListener("change", () => { if (entrada.files.length) sube(entrada.files[0]); });

function errorSubida(texto) {
  muestra("subir");
  const p = $("#error-subir");
  p.textContent = texto;
  p.hidden = false;
}

function sube(fichero) {
  $("#error-subir").hidden = true;
  muestra("analizar");
  $("#titulo-analisis").textContent = fichero.name;
  $("#mensaje-analisis").textContent = "Subiendo...";
  ponProgreso(0);

  const datos = new FormData();
  datos.append("audio", fichero);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/canciones");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) $("#mensaje-analisis").textContent = `Subiendo... ${Math.round(100 * e.loaded / e.total)}%`;
  };
  xhr.onload = () => {
    let r = {};
    try { r = JSON.parse(xhr.responseText); } catch (_) { /* respuesta no JSON */ }
    if (xhr.status !== 202) return errorSubida(r.error || `Error al subir (${xhr.status}).`);
    location.hash = r.id;
  };
  xhr.onerror = () => errorSubida("No se pudo conectar con el servidor.");
  xhr.send(datos);
  entrada.value = "";
}

function ponProgreso(fraccion) {
  const barra = $("#barra-progreso");
  barra.parentElement.classList.toggle("indefinida", fraccion == null);
  barra.style.width = fraccion == null ? "" : `${Math.round(fraccion * 100)}%`;
}

// --------------------------------------------------------------------------- analizar

async function abreCancion(id) {
  estado.id = id;
  muestra("analizar");
  for (;;) {
    if (estado.id !== id) return;
    let t;
    try {
      const r = await fetch(`/api/canciones/${id}`);
      if (r.status === 404) { location.hash = ""; return errorSubida("No encuentro esa canción. Súbela otra vez."); }
      t = await r.json();
    } catch (_) {
      $("#mensaje-analisis").textContent = "Sin conexión con el servidor, reintentando...";
      await espera(2000);
      continue;
    }
    $("#titulo-analisis").textContent = t.titulo;
    if (t.estado === "error") return errorSubida(t.mensaje);
    if (t.estado === "listo") return muestraCancion(t);
    const pct = t.estado === "separando" && t.progreso < 1 ? ` ${Math.round(t.progreso * 100)}%` : "";
    $("#mensaje-analisis").textContent = (t.mensaje || "Analizando...") + pct;
    ponProgreso(t.estado === "separando" && t.progreso < 1 ? t.progreso : null);
    await espera(1000);
  }
}

const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));

// --------------------------------------------------------------------------- canción

function muestraCancion(t) {
  estado.cancion = t;
  muestra("cancion");
  $("#titulo-cancion").textContent = t.titulo;
  const cont = $("#instrumentos");
  cont.innerHTML = "";
  for (const p of t.pistas) {
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
  const primera = t.pistas.find((p) => p.suena && p.partitura);
  if (primera) eligeInstrumento(primera);
}

$("#otra").addEventListener("click", () => {
  audio.pause();
  location.hash = "";
});

function eligeInstrumento(p) {
  estado.instrumento = p;
  for (const b of document.querySelectorAll(".inst")) {
    b.setAttribute("aria-checked", String(b.dataset.clave === p.clave));
  }
  for (const s of document.querySelectorAll(".nombre-inst")) s.textContent = p.nombre.split(" ")[0].toLowerCase();
  $("#bloque-reproductor").hidden = false;
  if (estado.modo !== "mezcla") ponAudio(estado.modo);
  else if (!audio.src) ponAudio("mezcla");
  cargaPartitura();
}

// --------------------------------------------------------------------------- audio

const audio = $("#audio");
const play = $("#play");
const posicion = $("#posicion");
let arrastrando = false;

function urlAudio(modo) {
  const base = `/api/canciones/${estado.id}/audio/`;
  if (modo === "solo") return base + estado.instrumento.clave;
  if (modo === "sin") return base + "sin-" + estado.instrumento.clave;
  return base + "mezcla";
}

function ponAudio(modo) {
  const url = urlAudio(modo);
  if (audio.src.endsWith(url)) return;
  const t = audio.currentTime || 0;
  const sonando = !audio.paused;
  audio.src = url;
  audio.addEventListener("loadedmetadata", () => {
    audio.currentTime = Math.min(t, audio.duration || t);
    if (sonando) audio.play();
  }, { once: true });
}

for (const b of document.querySelectorAll(".segmentado button")) {
  b.addEventListener("click", () => {
    estado.modo = b.dataset.modo;
    for (const o of document.querySelectorAll(".segmentado button")) o.classList.toggle("activo", o === b);
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
  if (e.code !== "Space" || pasos.cancion.hidden || ["INPUT", "SELECT", "BUTTON"].includes(e.target.tagName)) return;
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

// --------------------------------------------------------------------------- partitura

const opciones = { nombres: $("#nombres"), inglesa: $("#inglesa"), sensibilidad: $("#sensibilidad") };
opciones.nombres.addEventListener("change", dibuja);
opciones.inglesa.addEventListener("change", cargaPartitura);
opciones.sensibilidad.addEventListener("change", cargaPartitura);

async function cargaPartitura() {
  const p = estado.instrumento;
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
  $("#info-partitura").textContent = `Sacando las notas de ${p.nombre.toLowerCase()}... (puede tardar un poco)`;

  const q = new URLSearchParams({ sensibilidad: opciones.sensibilidad.value, notacion: opciones.inglesa.checked ? "inglesa" : "latina" });
  $("#midi").href = `/api/canciones/${estado.id}/midi/${p.clave}?${q}`;
  let datos;
  try {
    const r = await fetch(`/api/canciones/${estado.id}/partitura/${p.clave}?${q}`);
    datos = await r.json();
    if (!r.ok) throw new Error(datos.error || r.status);
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
window.addEventListener("resize", () => {
  clearTimeout(window._redibujo);
  window._redibujo = setTimeout(() => {
    const ancho = $("#partitura").clientWidth;
    if (Math.abs(ancho - anchoAnterior) > 40) { anchoAnterior = ancho; dibuja(); }
  }, 250);
});

// --------------------------------------------------------------------------- arranque

function ruta() {
  const id = location.hash.slice(1);
  audio.pause();
  audio.removeAttribute("src");
  estado.partitura = null;
  estado.modo = "mezcla";
  for (const o of document.querySelectorAll(".segmentado button")) o.classList.toggle("activo", o.dataset.modo === "mezcla");
  if (/^[0-9a-f]{32}$/.test(id)) abreCancion(id);
  else { estado.id = null; muestra("subir"); }
}
window.addEventListener("hashchange", ruta);
ruta();
