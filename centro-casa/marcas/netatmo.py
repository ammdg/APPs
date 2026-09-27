"""Netatmo: API oficial (https://dev.netatmo.com), vía su nube.

- Estación meteorológica: /api/getstationsdata (permiso read_station).
- Termostato / válvulas: /api/homesdata + /api/homestatus (read_thermostat) y
  /api/setroomthermpoint (write_thermostat).
- Cámaras: /api/homestatus (read_camera); la foto se pide a <vpn_url>/live/snapshot_720.jpg.

Netatmo cambia el refresh_token cada vez que se renueva el acceso, así que el último se guarda en
netatmo_token.json. El primero se copia de dev.netatmo.com (ver README).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp

LOG = logging.getLogger(__name__)
API = "https://api.netatmo.com"
TIPOS_CAMARA = {"NACamera", "NOC", "NDB", "NPC"}


class ErrorNetatmo(Exception):
    pass


def leer_estaciones(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Convierte la respuesta de getstationsdata en dispositivos tipo "sensor"."""
    salida = []
    for estacion in body.get("devices", []):
        grupo = estacion.get("home_name") or estacion.get("station_name") or "Estación"
        for modulo in [estacion, *estacion.get("modules", [])]:
            datos = modulo.get("dashboard_data") or {}
            estado = {
                "temperatura": datos.get("Temperature"),
                "humedad": datos.get("Humidity"),
                "co2": datos.get("CO2"),
                "ruido": datos.get("Noise"),
                "presion": datos.get("Pressure"),
                "lluvia_1h": datos.get("sum_rain_1"),
                "viento": datos.get("WindStrength"),
                "conectado": bool(modulo.get("reachable", True)) and bool(datos),
                "bateria": modulo.get("battery_percent"),
            }
            salida.append({
                "id": f"netatmo:{modulo['_id']}",
                "marca": "Netatmo",
                "tipo": "sensor",
                "nombre": modulo.get("module_name") or grupo,
                "grupo": grupo,
                "estado": {k: v for k, v in estado.items() if v is not None},
            })
    return salida


def leer_casa(casa: dict[str, Any], estado_casa: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Convierte homesdata (una casa) + homestatus en termostatos y cámaras.

    Devuelve también {id_dispositivo: vpn_url} de las cámaras, para pedir la foto después.
    """
    id_casa = casa["id"]
    grupo = casa.get("name") or "Casa"
    nombres_hab = {h["id"]: h.get("name") for h in casa.get("rooms", [])}
    nombres_mod = {m["id"]: m.get("name") for m in casa.get("modules", [])}
    salida, urls = [], {}

    for hab in estado_casa.get("rooms", []):
        if "therm_setpoint_temperature" not in hab:
            continue
        salida.append({
            "id": f"netatmo:{id_casa}~{hab['id']}",
            "marca": "Netatmo",
            "tipo": "termostato",
            "nombre": nombres_hab.get(hab["id"]) or "Habitación",
            "grupo": grupo,
            "estado": {
                "temperatura": hab.get("therm_measured_temperature"),
                "consigna": hab.get("therm_setpoint_temperature"),
                "modo": hab.get("therm_setpoint_mode"),
                "conectado": hab.get("reachable", True),
            },
        })

    for mod in estado_casa.get("modules", []):
        if mod.get("type") not in TIPOS_CAMARA:
            continue
        id_disp = f"netatmo:{id_casa}~{mod['id']}"
        if mod.get("vpn_url"):
            urls[id_disp] = mod["vpn_url"]
        salida.append({
            "id": id_disp,
            "marca": "Netatmo",
            "tipo": "camara",
            "nombre": nombres_mod.get(mod["id"]) or "Cámara",
            "grupo": grupo,
            "estado": {
                "encendida": mod.get("monitoring", mod.get("status")) == "on",
                "conectado": bool(mod.get("vpn_url")),
            },
        })
    return salida, urls


class Netatmo:
    prefijo = "netatmo"

    def __init__(self, conf: dict[str, Any], sesion: aiohttp.ClientSession, carpeta: Path):
        self.conf = conf
        self.sesion = sesion
        self.ruta_token = carpeta / "netatmo_token.json"
        self.token: dict[str, Any] = {"refresh_token": conf.get("refresh_token", ""), "expira": 0}
        if self.ruta_token.exists():
            self.token = json.loads(self.ruta_token.read_text())
        self.urls_camara: dict[str, str] = {}
        self.avisos: list[str] = []

    async def _renovar(self) -> None:
        async with self.sesion.post(f"{API}/oauth2/token", data={
            "grant_type": "refresh_token",
            "refresh_token": self.token["refresh_token"],
            "client_id": self.conf["client_id"],
            "client_secret": self.conf["client_secret"],
        }) as r:
            datos = await r.json(content_type=None)
            if r.status != 200:
                raise ErrorNetatmo(f"no se pudo renovar el acceso ({r.status}): {datos}")
        self.token = {
            "access_token": datos["access_token"],
            "refresh_token": datos["refresh_token"],
            "expira": time.time() + int(datos.get("expires_in", 10800)) - 120,
        }
        self.ruta_token.write_text(json.dumps(self.token))

    async def _llamar(self, metodo: str, ruta: str, reintento: bool = True, **kw) -> Any:
        if not self.token.get("access_token") or time.time() > self.token.get("expira", 0):
            await self._renovar()
        cabeceras = {"Authorization": f"Bearer {self.token['access_token']}"}
        async with self.sesion.request(metodo, f"{API}{ruta}", headers=cabeceras, **kw) as r:
            datos = await r.json(content_type=None)
            if r.status in (401, 403) and reintento:
                self.token["expira"] = 0
                return await self._llamar(metodo, ruta, reintento=False, **kw)
            if r.status != 200:
                raise ErrorNetatmo(f"{ruta} respondió {r.status}: {datos.get('error', datos)}")
        return datos.get("body", datos)

    async def listar(self) -> list[dict[str, Any]]:
        self.avisos = []
        salida = []
        if self.conf.get("estaciones", True):
            try:
                salida += leer_estaciones(await self._llamar("GET", "/api/getstationsdata"))
            except Exception as e:  # noqa: BLE001 - un fallo no debe tapar el resto
                self.avisos.append(f"Netatmo (estación): {e}")
        if self.conf.get("termostatos", True) or self.conf.get("camaras", True):
            try:
                casas = (await self._llamar("GET", "/api/homesdata")).get("homes", [])
                for casa in casas:
                    estado = await self._llamar("GET", "/api/homestatus", params={"home_id": casa["id"]})
                    disp, urls = leer_casa(casa, estado.get("home", {}))
                    self.urls_camara.update(urls)
                    salida += [d for d in disp if self.conf.get(
                        "camaras" if d["tipo"] == "camara" else "termostatos", True)]
            except Exception as e:  # noqa: BLE001
                self.avisos.append(f"Netatmo (casa): {e}")
        return salida

    async def accion(self, id_local: str, accion: str, datos: dict[str, Any]) -> None:
        id_casa, id_hab = id_local.split("~", 1)
        if accion == "consigna":
            temp = float(datos["temperatura"])
            if not 5 <= temp <= 30:
                raise ValueError("la temperatura debe estar entre 5 y 30 °C")
            await self._llamar("POST", "/api/setroomthermpoint", data={
                "home_id": id_casa, "room_id": id_hab, "mode": "manual", "temp": temp})
        elif accion == "programa":
            await self._llamar("POST", "/api/setroomthermpoint", data={
                "home_id": id_casa, "room_id": id_hab, "mode": "home"})
        else:
            raise ValueError(f"acción desconocida: {accion}")

    async def foto(self, id_local: str) -> tuple[bytes, str]:
        url = self.urls_camara.get(f"netatmo:{id_local}")
        if not url:
            raise ErrorNetatmo("la cámara no está conectada")
        async with self.sesion.get(f"{url}/live/snapshot_720.jpg") as r:
            r.raise_for_status()
            return await r.read(), r.content_type
