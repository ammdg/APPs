"""Cámaras con una URL de foto (YI Home con firmware yi-hack, u otras).

La nube de YI (Kami) no tiene API pública. Para verlas aquí hay que instalar en la cámara el
firmware libre yi-hack que corresponda a su modelo (yi-hack-MStar, yi-hack-Allwinner-v2…), que
publica la foto en la red local, p. ej. http://IP-CAMARA:8080/cgi-bin/snapshot.sh?res=low
"""

from __future__ import annotations

from typing import Any

import aiohttp


class Camaras:
    prefijo = "camara"

    def __init__(self, conf: list[dict[str, Any]], sesion: aiohttp.ClientSession):
        self.conf = conf
        self.sesion = sesion
        self.avisos: list[str] = []

    async def listar(self) -> list[dict[str, Any]]:
        return [{
            "id": f"camara:{i}",
            "marca": c.get("marca", "YI"),
            "tipo": "camara",
            "nombre": c.get("nombre", f"Cámara {i + 1}"),
            "grupo": "",
            "estado": {"conectado": True},
        } for i, c in enumerate(self.conf)]

    async def accion(self, id_local: str, accion: str, datos: dict[str, Any]) -> None:
        raise ValueError("estas cámaras solo admiten ver la foto")

    async def foto(self, id_local: str) -> tuple[bytes, str]:
        c = self.conf[int(id_local)]
        auth = aiohttp.BasicAuth(c["usuario"], c.get("clave", "")) if c.get("usuario") else None
        async with self.sesion.get(c["url_foto"], auth=auth, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            return await r.read(), r.content_type
