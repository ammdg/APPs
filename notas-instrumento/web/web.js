"use strict";
// Versión GitHub Pages: las canciones ya vienen procesadas por GitHub Actions (publica.py).
// Si existe clave.json, todo va cifrado (AES-256-GCM, clave PBKDF2-SHA256 de
// "usuario:contraseña", igual que en publica.py): se descifra aquí, en el navegador.

const $ = (s) => document.querySelector(s);
const pasos = ["cargando", "paso-login", "paso-lista", "paso-cancion"];
const GUARDADO = "notas-instrumento-clave";

let clave = null; // CryptoKey, o null si no está cifrado
let cifrado = false;
let indice = [];
let blobs = []; // URLs creadas para la canción abierta (se liberan al cerrarla)

function muestra(id) {
  for (const p of pasos) $("#" + p).hidden = p !== id;
}

function error(texto) {
  const p = $("#error-general");
  p.textContent = texto;
  p.hidden = !texto;
}

// --------------------------------------------------------------------------- ficheros

const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

async function bytes(ruta, sinCache = false) {
  const r = await fetch(ruta, sinCache ? { cache: "no-cache" } : {});
  if (!r.ok) throw new Error(r.status === 404 ? "no encontrado" : `error ${r.status}`);
  const datos = new Uint8Array(await r.arrayBuffer());
  if (!cifrado) return datos;
  const claro = await crypto.subtle.decrypt({ name: "AES-GCM", iv: datos.slice(0, 12) }, clave, datos.slice(12));
  return new Uint8Array(claro);
}

async function json(ruta, sinCache = false) {
  return JSON.parse(new TextDecoder().decode(await bytes(ruta, sinCache)));
}

async function urlDe(ruta, tipo) {
  if (!cifrado) return ruta;
  const url = URL.createObjectURL(new Blob([await bytes(ruta)], { type: tipo }));
  blobs.push(url);
  return url;
}

// --------------------------------------------------------------------------- entrar

async function derivaClave(usuario, password, info) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(`${usuario}:${password}`), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt: b64(info.sal), iterations: info.iteraciones },
    base, { name: "AES-GCM", length: 256 }, true, ["decrypt"]);
}

async function compruebaClave(k, info) {
  const c = b64(info.comprobante);
  const claro = await crypto.subtle.decrypt({ name: "AES-GCM", iv: c.slice(0, 12) }, k, c.slice(12));
  return new TextDecoder().decode(claro) === "notas-instrumento";
}

function recuerda(k) {
  try {
    if (!k) return localStorage.removeItem(GUARDADO);
    crypto.subtle.exportKey("raw", k).then((raw) =>
      localStorage.setItem(GUARDADO, btoa(String.fromCharCode(...new Uint8Array(raw)))));
  } catch (_) { /* sin almacenamiento: no se recuerda */ }
}

async function claveGuardada(info) {
  try {
    const g = localStorage.getItem(GUARDADO);
    if (!g) return null;
    const k = await crypto.subtle.importKey("raw", b64(g), "AES-GCM", true, ["decrypt"]);
    return (await compruebaClave(k, info)) ? k : null;
  } catch (_) {
    return null;
  }
}

function pideLogin(info) {
  muestra("paso-login");
  $("#usuario").focus();
  $("#form-login").onsubmit = async (e) => {
    e.preventDefault();
    const boton = $("#form-login button");
    boton.disabled = true;
    $("#error-login").hidden = true;
    try {
      const k = await derivaClave($("#usuario").value.trim(), $("#password").value, info);
      if (!(await compruebaClave(k, info))) throw new Error();
      clave = k;
      if ($("#recordar").checked) recuerda(k);
      $("#password").value = "";
      await cargaIndice();
    } catch (_) {
      const p = $("#error-login");
      p.textContent = "Usuario o contraseña incorrectos.";
      p.hidden = false;
    } finally {
      boton.disabled = false;
    }
  };
}

$("#salir").addEventListener("click", () => {
  recuerda(null);
  location.hash = "";
  location.reload();
});

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
  $("#salir").hidden = !cifrado;
  ruta();
}

// Enlaces de ayuda al repositorio (https://<usuario>.github.io/<repo>/...).
(function enlaces() {
  const usuario = location.hostname.endsWith(".github.io") ? location.hostname.split(".")[0] : null;
  const repo = location.pathname.split("/")[1];
  if (!usuario || !repo) return;
  const base = `https://github.com/${usuario}/${repo}`;
  $("#enlace-subir").href = `${base}/upload/main/notas-instrumento/canciones`;
  $("#enlace-acciones").href = `${base}/actions/workflows/notas-instrumento.yml`;
  $("#enlace-workflow").href = `${base}/actions/workflows/notas-instrumento.yml`;
})();

// --------------------------------------------------------------------------- canción

function fuenteWeb(id) {
  const base = `canciones/${id}/`;
  const partituras = {};
  const audios = {};
  return {
    modos: (pista) => (pista.audio ? ["mezcla", "solo"] : ["mezcla"]),
    audio: (modo, pista) => {
      const fichero = modo === "solo" ? pista.audio : "mezcla.mp3";
      audios[fichero] ??= urlDe(base + fichero, "audio/mpeg");
      return audios[fichero];
    },
    partitura: async (pista, sensibilidad, notacion) => {
      partituras[pista.clave] ??= json(`${base}partitura-${pista.clave}.json`);
      const todas = await partituras[pista.clave];
      const d = todas[Number(sensibilidad).toFixed(2)];
      return { cantidad: d.cantidad, tempo: d.tempo, abc: d.abc, eventos: d.eventos,
               abc_nombres: d.nombres[notacion].abc, eventos_nombres: d.nombres[notacion].eventos };
    },
    midi: (pista, sensibilidad) => urlDe(`${base}${pista.clave}-${Number(sensibilidad).toFixed(2)}.mid`, "audio/midi"),
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
  for (const u of blobs) URL.revokeObjectURL(u);
  blobs = [];
  error("");
  const id = decodeURIComponent(location.hash.slice(1));
  if (id && indice.some((c) => c.id === id)) abreCancion(id);
  else muestra("paso-lista");
}
window.addEventListener("hashchange", () => { if (!cifrado || clave) ruta(); });

// --------------------------------------------------------------------------- arranque

(async function arranca() {
  let info = null;
  try {
    const r = await fetch("clave.json", { cache: "no-cache" });
    if (r.ok) info = await r.json();
  } catch (_) { /* sin clave.json: sin cifrar */ }
  cifrado = !!info;
  if (!cifrado) return cargaIndice();
  if (!window.crypto?.subtle) {
    muestra("paso-lista");
    return error("Este navegador no puede descifrar las canciones (hace falta una conexión https).");
  }
  clave = await claveGuardada(info);
  if (clave) return cargaIndice();
  pideLogin(info);
})();
