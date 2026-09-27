"""Rain Bird: controladores con módulo WiFi LNK/LNK2, por red local.

Rain Bird no publica una API de su nube. La librería pyrainbird (la que usa Home Assistant) habla
directamente con el módulo WiFi en tu red usando la contraseña que pusiste en la app Rain Bird.
El módulo solo atiende una petición a la vez, por eso cada controlador tiene su candado.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from pyrainbird.async_client import CreateController


class RainBird:
    prefijo = "rainbird"

    def __init__(self, conf: dict[str, Any], sesion: aiohttp.ClientSession):
        self.controladores = {}
        for i, c in enumerate(conf.get("controladores", [])):
            self.controladores[str(i)] = {
                "conf": c,
                "api": CreateController(sesion, c["ip"], c["clave"]),
                "candado": asyncio.Lock(),
            }
        self.avisos: list[str] = []

    async def _leer(self, clave: str, ctl: dict[str, Any]) -> dict[str, Any]:
        api, conf = ctl["api"], ctl["conf"]
        async with ctl["candado"]:
            disponibles = (await api.get_available_stations()).active_set
            activas = (await api.get_zone_states()).active_set
            sensor = await api.get_rain_sensor_state()
            retraso = await api.get_rain_delay()
        nombres = conf.get("zonas", {})
        return {
            "id": f"rainbird:{clave}",
            "marca": "Rain Bird",
            "tipo": "riego",
            "nombre": conf.get("nombre", "Riego"),
            "grupo": "",
            "estado": {
                "zonas": [{"numero": n, "nombre": nombres.get(str(n), f"Zona {n}"), "regando": n in activas}
                          for n in sorted(disponibles)],
                "lluvia_detectada": sensor,
                "retraso_lluvia_dias": retraso,
                "conectado": True,
            },
        }

    async def listar(self) -> list[dict[str, Any]]:
        self.avisos = []
        salida = []
        for clave, ctl in self.controladores.items():
            try:
                salida.append(await asyncio.wait_for(self._leer(clave, ctl), 30))
            except Exception as e:  # noqa: BLE001
                self.avisos.append(f"Rain Bird ({ctl['conf'].get('nombre', ctl['conf']['ip'])}): {e or type(e).__name__}")
        return salida

    async def accion(self, id_local: str, accion: str, datos: dict[str, Any]) -> None:
        ctl = self.controladores[id_local]
        api = ctl["api"]
        async with ctl["candado"]:
            if accion == "regar":
                minutos = int(datos["minutos"])
                if not 1 <= minutos <= 120:
                    raise ValueError("los minutos deben estar entre 1 y 120")
                await api.irrigate_zone(int(datos["zona"]), minutos)
            elif accion == "parar":
                await api.stop_irrigation()
            elif accion == "retraso":
                dias = int(datos["dias"])
                if not 0 <= dias <= 14:
                    raise ValueError("el retraso debe estar entre 0 y 14 días")
                await api.set_rain_delay(dias)
            else:
                raise ValueError(f"acción desconocida: {accion}")
