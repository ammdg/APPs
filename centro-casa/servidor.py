"""Centro de control de casa: una página web para Netatmo, Rain Bird, Wemo y cámaras YI.

Se ejecuta en un ordenador de casa (una Raspberry Pi vale) porque Rain Bird, Wemo y las cámaras
YI solo se pueden controlar desde la red local; Netatmo va por su nube oficial.

Uso:
    python servidor.py --demo                 # aparatos de mentira, para ver la página
    python servidor.py                        # usa config.json
    python servidor.py --puerto 8080 --config otra.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

AQUI = Path(__file__).resolve().parent
LOG = logging.getLogger("centro-casa")
COOKIE = "centro_casa"

ADAPTADORES = web.AppKey("adaptadores", dict)
CLAVE = web.AppKey("clave", str)
SESIONES = web.AppKey("sesiones", set)
DEMO = web.AppKey("demo", bool)


def crear_adaptadores(conf: dict[str, Any], sesion: ClientSession) -> dict[str, Any]:
    """Crea solo los adaptadores de las marcas que aparecen en la configuración."""
    adaptadores: dict[str, Any] = {}
    if "netatmo" in conf:
        from marcas.netatmo import Netatmo
        adaptadores["netatmo"] = Netatmo(conf["netatmo"], sesion, AQUI)
    if "rainbird" in conf:
        from marcas.rainbird import RainBird
        adaptadores["rainbird"] = RainBird(conf["rainbird"], sesion)
    if "wemo" in conf:
        from marcas.wemo import Wemo
        adaptadores["wemo"] = Wemo(conf["wemo"])
    if "camaras" in conf:
        from marcas.camaras import Camaras
        adaptadores["camara"] = Camaras(conf["camaras"], sesion)
    return adaptadores


@web.middleware
async def exigir_clave(request: web.Request, handler):
    """Si hay clave_acceso, todo /api/ (salvo entrar) necesita la cookie de sesión."""
    clave = request.app[CLAVE]
    if (clave and request.path.startswith("/api/") and request.path != "/api/entrar"
            and request.cookies.get(COOKIE) not in request.app[SESIONES]):
        return web.json_response({"error": "hace falta la clave"}, status=401)
    return await handler(request)


async def entrar(request: web.Request) -> web.Response:
    datos = await request.json()
    clave = request.app[CLAVE]
    if clave and not secrets.compare_digest(str(datos.get("clave", "")), clave):
        await asyncio.sleep(1)  # frena los intentos a ciegas
        return web.json_response({"error": "clave incorrecta"}, status=401)
    token = secrets.token_urlsafe(32)
    request.app[SESIONES].add(token)
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=90 * 24 * 3600)
    return resp


async def estado(request: web.Request) -> web.Response:
    adaptadores = request.app[ADAPTADORES]

    async def uno(nombre: str, a: Any) -> list[dict[str, Any]]:
        try:
            return await asyncio.wait_for(a.listar(), 45)
        except Exception as e:  # noqa: BLE001 - una marca caída no tapa las demás
            LOG.exception("fallo al leer %s", nombre)
            a.avisos = [f"{nombre}: {e or type(e).__name__}"]
            return []

    listas = await asyncio.gather(*(uno(n, a) for n, a in adaptadores.items()))
    return web.json_response({
        "dispositivos": [d for lista in listas for d in lista],
        "avisos": [av for a in adaptadores.values() for av in a.avisos],
        "demo": request.app[DEMO],
    })


def buscar(request: web.Request) -> tuple[Any, str]:
    prefijo, _, id_local = request.match_info["id"].partition(":")
    adaptador = request.app[ADAPTADORES].get(prefijo)
    if adaptador is None or not id_local:
        raise web.HTTPNotFound(text="aparato desconocido")
    return adaptador, id_local


async def accion(request: web.Request) -> web.Response:
    adaptador, id_local = buscar(request)
    datos = await request.json() if request.can_read_body else {}
    try:
        await adaptador.accion(id_local, request.match_info["accion"], datos)
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:  # noqa: BLE001
        LOG.exception("fallo en la acción")
        return web.json_response({"error": f"el aparato no respondió: {e or type(e).__name__}"}, status=502)
    return web.json_response({"ok": True})


async def foto(request: web.Request) -> web.Response:
    adaptador, id_local = buscar(request)
    if not hasattr(adaptador, "foto"):
        raise web.HTTPNotFound(text="este aparato no tiene cámara")
    try:
        contenido, tipo = await adaptador.foto(id_local)
    except Exception as e:  # noqa: BLE001
        LOG.warning("sin foto de %s: %s", request.match_info["id"], e)
        raise web.HTTPBadGateway(text="la cámara no respondió") from e
    return web.Response(body=contenido, content_type=tipo, headers={"Cache-Control": "no-store"})


async def portada(request: web.Request) -> web.FileResponse:
    return web.FileResponse(AQUI / "web" / "index.html", headers={"Cache-Control": "no-cache"})


def crear_app(conf: dict[str, Any], demo: bool = False) -> web.Application:
    app = web.Application(middlewares=[exigir_clave])
    app[CLAVE] = "" if demo else str(conf.get("clave_acceso", ""))
    app[SESIONES] = set()
    app[DEMO] = demo

    async def ciclo(app: web.Application):
        sesion = ClientSession(timeout=ClientTimeout(total=30))
        if demo:
            from marcas.demo import Demo
            app[ADAPTADORES] = {"demo": Demo()}
        else:
            app[ADAPTADORES] = crear_adaptadores(conf, sesion)
        yield
        await sesion.close()

    app.cleanup_ctx.append(ciclo)
    app.router.add_get("/", portada)
    app.router.add_post("/api/entrar", entrar)
    app.router.add_get("/api/estado", estado)
    app.router.add_post("/api/dispositivos/{id}/{accion}", accion)
    app.router.add_get("/api/dispositivos/{id}/foto", foto)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=AQUI / "config.json")
    p.add_argument("--demo", action="store_true", help="aparatos simulados, sin config.json")
    p.add_argument("--puerto", type=int, default=8090)
    p.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = accesible desde toda la red de casa")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    conf: dict[str, Any] = {}
    if not args.demo:
        if not args.config.exists():
            raise SystemExit(f"No existe {args.config}. Copia config.ejemplo.json o prueba con --demo.")
        conf = json.loads(args.config.read_text(encoding="utf-8"))
        if not conf.get("clave_acceso"):
            LOG.warning("Sin clave_acceso: cualquiera en tu red puede manejar los aparatos.")
    web.run_app(crear_app(conf, args.demo), host=args.host, port=args.puerto)


if __name__ == "__main__":
    main()
