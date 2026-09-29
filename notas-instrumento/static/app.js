"use strict";
// App local (app.py): subir la canción, esperar al análisis y abrir el reproductor.

const $ = (s) => document.querySelector(s);
const pasos = { subir: $("#paso-subir"), analizar: $("#paso-analizar"), cancion: $("#paso-cancion") };
let idActual = null;

function muestra(paso) {
  for (const [nombre, el] of Object.entries(pasos)) el.hidden = nombre !== paso;
}

// Datos sacados del servidor local.
function fuenteLocal(id) {
  const base = `/api/canciones/${id}`;
  return {
    modos: () => ["mezcla", "solo", "sin"],
    audio: async (modo, pista) =>
      `${base}/audio/${modo === "solo" ? pista.clave : modo === "sin" ? "sin-" + pista.clave : "mezcla"}`,
    partitura: async (pista, sensibilidad, notacion) => {
      const q = new URLSearchParams({ sensibilidad, notacion });
      const r = await fetch(`${base}/partitura/${pista.clave}?${q}`);
      const datos = await r.json();
      if (!r.ok) throw new Error(datos.error || r.status);
      return datos;
    },
    midi: async (pista, sensibilidad) => `${base}/midi/${pista.clave}?sensibilidad=${sensibilidad}`,
  };
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

const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));

async function abreCancion(id) {
  idActual = id;
  muestra("analizar");
  for (;;) {
    if (idActual !== id) return;
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
    if (idActual !== id) return;
    $("#titulo-analisis").textContent = t.titulo;
    if (t.estado === "error") return errorSubida(t.mensaje);
    if (t.estado === "listo") {
      muestra("cancion");
      return Reproductor.abre(t, fuenteLocal(id));
    }
    const pct = t.estado === "separando" && t.progreso < 1 ? ` ${Math.round(t.progreso * 100)}%` : "";
    $("#mensaje-analisis").textContent = (t.mensaje || "Analizando...") + pct;
    ponProgreso(t.estado === "separando" && t.progreso < 1 ? t.progreso : null);
    await espera(1000);
  }
}

// --------------------------------------------------------------------------- arranque

$("#otra").addEventListener("click", () => { location.hash = ""; });

function ruta() {
  const id = location.hash.slice(1);
  Reproductor.cierra();
  if (/^[0-9a-f]{32}$/.test(id)) abreCancion(id);
  else { idActual = null; muestra("subir"); }
}
window.addEventListener("hashchange", ruta);
ruta();
