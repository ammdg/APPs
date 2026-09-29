"use strict";
// Versión GitHub Pages: las canciones las procesa GitHub Actions (publica.py) y aquí solo se
// reproducen. También graba con el micrófono y sube la grabación al repositorio para que se procese.

const $ = (s) => document.querySelector(s);
const pasos = ["cargando", "paso-lista", "paso-token", "paso-grabar", "paso-enviando", "paso-cancion"];

let indice = [];

function muestra(id) {
  for (const p of pasos) $("#" + p).hidden = p !== id;
}

function error(texto) {
  const p = $("#error-general");
  p.textContent = texto;
  p.hidden = !texto;
}

// --------------------------------------------------------------------------- ficheros

async function bytes(ruta, sinCache = false) {
  const r = await fetch(ruta, sinCache ? { cache: "no-cache" } : {});
  if (!r.ok) throw new Error(r.status === 404 ? "no encontrado" : `error ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}

async function json(ruta, sinCache = false) {
  return JSON.parse(new TextDecoder().decode(await bytes(ruta, sinCache)));
}

// --------------------------------------------------------------------------- lista

function formatoDuracion(s) {
  return `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}

async function cargaIndice() {
  try {
    indice = (await json("canciones.json", true)).canciones;
  } catch (e) {
    indice = [];
    if (!/no encontrado/.test(e.message)) error(`No se pudo leer la lista de canciones: ${e.message}`);
  }
  const ul = $("#lista");
  ul.innerHTML = "";
  for (const c of indice) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "#" + c.id;
    a.textContent = c.titulo;
    const extra = document.createElement("span");
    extra.className = "apagado pequeno";
    extra.textContent = ` ${formatoDuracion(c.duracion)} · ${new Date(c.fecha).toLocaleDateString("es-ES")}`;
    li.append(a, extra);
    ul.append(li);
  }
  $("#vacia").hidden = indice.length > 0;
  $("#olvidar-token").hidden = !leeToken();
  const p = leePendiente();
  if (p && !location.hash) return sigue(p);
  ruta();
}

// Repositorio de GitHub: se deduce de la dirección (https://<usuario>.github.io/<repo>/...).
// Fuera de GitHub Pages (pruebas) se puede fijar con localStorage "notas-instrumento-repo".
const REPO = (() => {
  if (location.hostname.endsWith(".github.io")) {
    const repo = location.pathname.split("/")[1];
    return repo ? `${location.hostname.split(".")[0]}/${repo}` : null;
  }
  try { return localStorage.getItem("notas-instrumento-repo"); } catch (_) { return null; }
})();
const API = "https://api.github.com";

(function enlaces() {
  if (!REPO) return;
  const base = `https://github.com/${REPO}`;
  $("#enlace-subir").href = `${base}/upload/main/notas-instrumento/canciones`;
  $("#enlace-acciones").href = `${base}/actions/workflows/notas-instrumento.yml`;
  $("#enlace-workflow").href = `${base}/actions/workflows/notas-instrumento.yml`;
  $("#nombre-repo").textContent = REPO;
})();

// --------------------------------------------------------------------------- canción

function fuenteWeb(id) {
  const base = `canciones/${id}/`;
  const partituras = {};
  return {
    modos: (pista) => (pista.audio ? ["mezcla", "solo"] : ["mezcla"]),
    audio: async (modo, pista) => base + (modo === "solo" ? pista.audio : "mezcla.mp3"),
    partitura: async (pista, sensibilidad, notacion) => {
      partituras[pista.clave] ??= json(`${base}partitura-${pista.clave}.json`);
      const todas = await partituras[pista.clave];
      const d = todas[Number(sensibilidad).toFixed(2)];
      return { cantidad: d.cantidad, tempo: d.tempo, abc: d.abc, eventos: d.eventos,
               abc_nombres: d.nombres[notacion].abc, eventos_nombres: d.nombres[notacion].eventos };
    },
    midi: async (pista, sensibilidad) => `${base}${pista.clave}-${Number(sensibilidad).toFixed(2)}.mid`,
  };
}

async function abreCancion(id) {
  muestra("paso-cancion");
  $("#titulo-cancion").textContent = "Cargando...";
  $("#instrumentos").innerHTML = "";
  try {
    const datos = await json(`canciones/${id}/datos.json`);
    if (location.hash.slice(1) !== id) return;
    Reproductor.abre(datos, fuenteWeb(id));
  } catch (e) {
    error(`No se pudo abrir la canción: ${e.message}`);
    muestra("paso-lista");
  }
}

$("#otra").addEventListener("click", () => { location.hash = ""; });

function ruta() {
  Reproductor.cierra();
  error("");
  const id = decodeURIComponent(location.hash.slice(1));
  if (id && indice.some((c) => c.id === id)) abreCancion(id);
  else muestra("paso-lista");
}
window.addEventListener("hashchange", ruta);

// --------------------------------------------------------------------------- grabar

const MAX_SEGUNDOS = 10 * 60; // GitHub no acepta ficheros muy grandes por la API
const guarda = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (_) { /* sin almacenamiento */ } };
const lee = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
const leeToken = () => lee("notas-instrumento-token");
const leePendiente = () => { try { return JSON.parse(lee("notas-instrumento-pendiente")); } catch (_) { return null; } };

const grab = { grabador: null, flujo: null, trozos: [], inicio: 0, contexto: null, bloqueo: null, blob: null, url: null };

$("#empezar").addEventListener("click", empiezaGrabacion);
$("#parar").addEventListener("click", () => grab.grabador?.state === "recording" && grab.grabador.stop());
$("#otra-vez").addEventListener("click", empiezaGrabacion);
$("#descartar").addEventListener("click", () => { suelta(); muestra("paso-lista"); });
$("#olvidar-token").addEventListener("click", () => { guarda("notas-instrumento-token", null); $("#olvidar-token").hidden = true; });

function suelta() {
  if (grab.url) URL.revokeObjectURL(grab.url);
  grab.url = null;
  grab.blob = null;
}

function formatoReloj(s) {
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

async function empiezaGrabacion() {
  error("");
  suelta();
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    return error("Este navegador no puede grabar con el micrófono.");
  }
  try {
    // Sin los filtros pensados para voz (quitan música como si fuera ruido).
    grab.flujo = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch (e) {
    return error(e.name === "NotAllowedError"
      ? "No hay permiso para usar el micrófono. Actívalo en los ajustes del navegador para esta página."
      : `No se pudo abrir el micrófono: ${e.message}`);
  }
  const tipo = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]
    .find((t) => MediaRecorder.isTypeSupported(t));
  grab.grabador = new MediaRecorder(grab.flujo, { ...(tipo ? { mimeType: tipo } : {}), audioBitsPerSecond: 128000 });
  grab.trozos = [];
  grab.grabador.ondataavailable = (e) => { if (e.data.size) grab.trozos.push(e.data); };
  grab.grabador.onstop = terminaGrabacion;
  grab.grabador.start(1000);
  grab.inicio = performance.now();

  try { grab.bloqueo = await navigator.wakeLock?.request("screen"); } catch (_) { /* sin bloqueo de pantalla */ }
  $("#grabando").hidden = false;
  $("#grabado").hidden = true;
  muestra("paso-grabar");

  // Medidor de nivel y reloj.
  grab.contexto = new (window.AudioContext || window.webkitAudioContext)();
  const analizador = grab.contexto.createAnalyser();
  analizador.fftSize = 1024;
  grab.contexto.createMediaStreamSource(grab.flujo).connect(analizador);
  const muestras = new Float32Array(analizador.fftSize);
  const pinta = () => {
    if (grab.grabador?.state !== "recording") return;
    const s = (performance.now() - grab.inicio) / 1000;
    $("#reloj").textContent = formatoReloj(s);
    analizador.getFloatTimeDomainData(muestras);
    const rms = Math.sqrt(muestras.reduce((a, x) => a + x * x, 0) / muestras.length);
    const db = 20 * Math.log10(rms + 1e-9);
    $("#nivel").style.width = `${Math.max(0, Math.min(100, (db + 60) / 60 * 100))}%`;
    $("#aviso-nivel").textContent = db < -45 ? "Se oye muy poco: acerca el móvil a la música."
      : "Acerca el móvil a la música. Mantén la pantalla encendida.";
    if (s >= MAX_SEGUNDOS) return grab.grabador.stop();
    requestAnimationFrame(pinta);
  };
  pinta();
}

function terminaGrabacion() {
  for (const t of grab.flujo?.getTracks() || []) t.stop();
  grab.contexto?.close();
  grab.bloqueo?.release?.();
  grab.bloqueo = null;
  grab.blob = new Blob(grab.trozos, { type: grab.grabador.mimeType || "audio/webm" });
  grab.url = URL.createObjectURL(grab.blob);
  $("#escucha").src = grab.url;
  const ext = extension(grab.blob.type);
  const ahora = new Date();
  $("#titulo-grabacion").value = `Grabación ${ahora.toLocaleDateString("es-ES")} ${ahora.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })}`;
  $("#descargar-grabacion").href = grab.url;
  $("#descargar-grabacion").download = `grabacion${ext}`;
  $("#grabando").hidden = true;
  $("#grabado").hidden = false;
}

function extension(tipo) {
  return tipo.includes("mp4") ? ".mp4" : tipo.includes("ogg") ? ".ogg" : ".webm";
}

// --------------------------------------------------------------------------- enviar a GitHub

$("#form-enviar").addEventListener("submit", (e) => { e.preventDefault(); envia(); });
$("#cancelar-token").addEventListener("click", () => muestra("paso-grabar"));
$("#form-token").addEventListener("submit", async (e) => {
  e.preventDefault();
  const token = $("#token").value.trim();
  const p = $("#error-token");
  p.hidden = true;
  try {
    const r = await fetch(`${API}/repos/${REPO}`, { headers: cabeceras(token) });
    if (!r.ok) throw new Error(r.status === 401 ? "El token no es válido." : `GitHub respondió ${r.status}.`);
  } catch (err) {
    p.textContent = err.message.startsWith("El token") || err.message.startsWith("GitHub") ? err.message : "No se pudo conectar con GitHub.";
    p.hidden = false;
    return;
  }
  guarda("notas-instrumento-token", token);
  $("#token").value = "";
  envia();
});

function cabeceras(token) {
  return { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
}

const aBase64 = (blob) => new Promise((ok, mal) => {
  const r = new FileReader();
  r.onload = () => ok(r.result.slice(r.result.indexOf(",") + 1));
  r.onerror = () => mal(r.error);
  r.readAsDataURL(blob);
});

async function envia() {
  if (!REPO) return error("No sé a qué repositorio de GitHub mandar la grabación.");
  const token = leeToken();
  if (!token) return muestra("paso-token");
  // El título es el nombre del fichero (publica.py lo lee de ahí): sin caracteres que no valen.
  const titulo = $("#titulo-grabacion").value.normalize().replace(/[\\/:*?"<>|#%]/g, "-")
    .replace(/\s+/g, " ").trim() || "Grabación";
  const ext = extension(grab.blob.type);

  muestra("paso-enviando");
  $("#titulo-enviando").textContent = titulo;
  $("#error-envio").hidden = true;
  $("#enlace-ejecucion").hidden = true;
  marcaPaso("enviar");
  $("#mensaje-envio").textContent = "Enviando...";

  const cuerpo = grab.blob;
  const nombre = titulo + ext;

  let r;
  try {
    r = await fetch(`${API}/repos/${REPO}/contents/notas-instrumento/canciones/${encodeURIComponent(nombre)}`, {
      method: "PUT",
      headers: cabeceras(token),
      body: JSON.stringify({ message: "Notas de un instrumento: grabación desde el móvil", content: await aBase64(cuerpo), branch: "main" }),
    });
  } catch (_) {
    return falloEnvio("No se pudo conectar con GitHub. Comprueba la conexión y vuelve a intentarlo.", true);
  }
  if (!r.ok) {
    const motivo = r.status === 401 ? "El token no es válido o ha caducado."
      : r.status === 403 || r.status === 404 ? "El token no tiene permiso de escritura (Contents: Read and write) en este repositorio."
      : r.status === 422 ? "Ya hay una grabación con ese nombre esperando. Cambia el título."
      : `GitHub respondió ${r.status}.`;
    if (r.status === 401) guarda("notas-instrumento-token", null);
    return falloEnvio(motivo, true);
  }
  const sha = (await r.json()).commit?.sha;
  const pendiente = { titulo, desde: Date.now(), sha };
  guarda("notas-instrumento-pendiente", JSON.stringify(pendiente));
  suelta();
  sigue(pendiente);
}

function falloEnvio(texto, reintentable) {
  marcaPaso("enviar", true);
  $("#mensaje-envio").textContent = "";
  const p = $("#error-envio");
  p.textContent = texto + (reintentable ? " La grabación sigue en el móvil." : "");
  p.hidden = false;
  if (reintentable) {
    const b = document.createElement("button");
    b.className = "boton";
    b.type = "button";
    b.textContent = "Volver a la grabación";
    b.style.display = "block";
    b.style.marginTop = "8px";
    b.onclick = () => muestra("paso-grabar");
    p.append(b);
  }
}

// --------------------------------------------------------------------------- seguimiento

const ORDEN = ["enviar", "cola", "analizar", "publicar"];
let seguimiento = 0;

function marcaPaso(actual, fallo = false) {
  const i = ORDEN.indexOf(actual);
  for (const li of document.querySelectorAll("#progreso-pasos li")) {
    const j = ORDEN.indexOf(li.dataset.paso);
    li.className = j < i ? "hecho" : j === i ? (fallo ? "fallo" : "actual") : "";
  }
}

$("#dejar-de-esperar").addEventListener("click", () => {
  seguimiento++;
  guarda("notas-instrumento-pendiente", null);
  muestra("paso-lista");
});

async function sigue(p) {
  const n = ++seguimiento;
  muestra("paso-enviando");
  $("#titulo-enviando").textContent = p.titulo;
  $("#error-envio").hidden = true;
  marcaPaso("cola");
  const token = leeToken();
  const hasta = p.desde + 60 * 60 * 1000;
  while (n === seguimiento) {
    const min = Math.round((Date.now() - p.desde) / 60000);
    $("#mensaje-envio").textContent = `Enviada hace ${min} min.`;

    // ¿Ya está publicada?
    try {
      const lista = (await json(`canciones.json?t=${Date.now()}`, true)).canciones;
      const hecha = lista.find((c) => c.titulo.normalize() === p.titulo.normalize() && Date.parse(c.fecha) >= p.desde - 10 * 60 * 1000);
      if (hecha) {
        guarda("notas-instrumento-pendiente", null);
        indice = lista;
        location.hash = hecha.id;
        return;
      }
    } catch (_) { /* aún no hay índice */ }

    // Estado en GitHub Actions (si el token puede verlo).
    if (token && p.sha) {
      try {
        const r = await fetch(`${API}/repos/${REPO}/actions/runs?head_sha=${p.sha}`, { headers: cabeceras(token) });
        const ej = r.ok ? (await r.json()).workflow_runs.find((w) => (w.path || "").endsWith("notas-instrumento.yml")) : null;
        if (ej) {
          $("#enlace-ejecucion").href = ej.html_url;
          $("#enlace-ejecucion").hidden = false;
          if (ej.status === "completed" && ej.conclusion !== "success") {
            marcaPaso("analizar", true);
            const e = $("#error-envio");
            e.textContent = "El análisis ha fallado en GitHub. Mira el detalle en «Ver en GitHub Actions».";
            e.hidden = false;
            guarda("notas-instrumento-pendiente", null);
            return;
          }
          marcaPaso(ej.status === "completed" ? "publicar" : ej.status === "in_progress" ? "analizar" : "cola");
        }
      } catch (_) { /* sin acceso a Actions: se sigue mirando el índice */ }
    }
    if (Date.now() > hasta) {
      const e = $("#error-envio");
      e.textContent = "Está tardando más de una hora. Mira en GitHub Actions si ha habido algún problema.";
      e.hidden = false;
      return;
    }
    await new Promise((ok) => setTimeout(ok, 15000));
  }
}

// --------------------------------------------------------------------------- arranque

cargaIndice();
