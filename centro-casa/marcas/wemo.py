"""Wemo (Belkin): por red local.

Belkin apagó la nube de Wemo el 31 de enero de 2026: ya no funcionan su app ni el acceso remoto.
Los enchufes siguen respondiendo en la red local (protocolo UPnP), que es lo que usa pywemo.
pywemo es síncrono, así que cada llamada va a un hilo aparte.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pywemo


class Wemo:
    prefijo = "wemo"
    REDESCUBRIR_CADA = 600  # segundos

    def __init__(self, conf: dict[str, Any]):
        self.conf = conf
        self.aparatos: dict[str, Any] = {}
        self.ultimo_descubrimiento = 0.0
        self.avisos: list[str] = []

    def _descubrir(self) -> dict[str, Any]:
        encontrados = []
        if self.conf.get("buscar", True):
            encontrados += pywemo.discover_devices()
        for ip in self.conf.get("ips", []):
            url = pywemo.setup_url_for_address(ip)
            if url and (aparato := pywemo.device_from_description(url)):
                encontrados.append(aparato)
        return {a.serial_number or a.host: a for a in encontrados}

    def _leer(self, clave: str, a: Any) -> dict[str, Any]:
        estado: dict[str, Any] = {"encendido": bool(a.get_state(force_update=True)), "conectado": True}
        if isinstance(a, pywemo.Insight):
            a.update_insight_params()
            estado["potencia_w"] = round(a.current_power_watts, 1)
            estado["hoy_kwh"] = round(a.today_kwh, 3)
        if isinstance(a, pywemo.Dimmer):
            estado["brillo"] = a.get_brightness(force_update=True)
        return {
            "id": f"wemo:{clave}",
            "marca": "Wemo",
            "tipo": "enchufe",
            "nombre": a.name,
            "grupo": "",
            "estado": estado,
        }

    async def listar(self) -> list[dict[str, Any]]:
        self.avisos = []
        if not self.aparatos or time.time() - self.ultimo_descubrimiento > self.REDESCUBRIR_CADA:
            try:
                self.aparatos |= await asyncio.to_thread(self._descubrir)
                self.ultimo_descubrimiento = time.time()
            except Exception as e:  # noqa: BLE001
                self.avisos.append(f"Wemo (búsqueda): {e}")
        salida = []
        for clave, a in self.aparatos.items():
            try:
                salida.append(await asyncio.to_thread(self._leer, clave, a))
            except Exception:  # noqa: BLE001 - aparato apagado o sin red
                salida.append({"id": f"wemo:{clave}", "marca": "Wemo", "tipo": "enchufe", "nombre": a.name,
                               "grupo": "", "estado": {"conectado": False}})
        if not self.aparatos:
            self.avisos.append("Wemo: no se ha encontrado ningún aparato en la red local")
        return salida

    async def accion(self, id_local: str, accion: str, datos: dict[str, Any]) -> None:
        a = self.aparatos[id_local]
        if accion == "encender":
            await asyncio.to_thread(a.on)
        elif accion == "apagar":
            await asyncio.to_thread(a.off)
        elif accion == "brillo" and isinstance(a, pywemo.Dimmer):
            brillo = int(datos["brillo"])
            if not 1 <= brillo <= 100:
                raise ValueError("el brillo debe estar entre 1 y 100")
            await asyncio.to_thread(a.set_brightness, brillo)
        else:
            raise ValueError(f"acción desconocida: {accion}")
