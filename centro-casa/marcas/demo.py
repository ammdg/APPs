"""Aparatos de mentira para probar la página sin cuentas ni aparatos reales."""

from __future__ import annotations

import copy
import random
from typing import Any

INICIO = [
    {"id": "demo:salon", "marca": "Netatmo", "tipo": "sensor", "nombre": "Salón", "grupo": "Estación",
     "estado": {"temperatura": 21.4, "humedad": 48, "co2": 612, "ruido": 38, "presion": 1016.2, "conectado": True}},
    {"id": "demo:exterior", "marca": "Netatmo", "tipo": "sensor", "nombre": "Exterior", "grupo": "Estación",
     "estado": {"temperatura": 14.8, "humedad": 71, "bateria": 64, "conectado": True}},
    {"id": "demo:dormitorio", "marca": "Netatmo", "tipo": "termostato", "nombre": "Dormitorio", "grupo": "Casa",
     "estado": {"temperatura": 19.6, "consigna": 20.0, "modo": "schedule", "conectado": True}},
    {"id": "demo:lampara", "marca": "Wemo", "tipo": "enchufe", "nombre": "Lámpara del salón",
     "grupo": "", "estado": {"encendido": True, "conectado": True}},
    {"id": "demo:calefactor", "marca": "Wemo", "tipo": "enchufe", "nombre": "Calefactor (Insight)",
     "grupo": "",
     "estado": {"encendido": False, "potencia_w": 0.0, "hoy_kwh": 0.412, "conectado": True}},
    {"id": "demo:pasillo", "marca": "Wemo", "tipo": "enchufe", "nombre": "Luz del pasillo (regulable)",
     "grupo": "", "estado": {"encendido": True, "brillo": 60, "conectado": True}},
    {"id": "demo:riego", "marca": "Rain Bird", "tipo": "riego", "nombre": "Jardín", "grupo": "",
     "estado": {"zonas": [{"numero": 1, "nombre": "Césped delantero", "regando": False},
                          {"numero": 2, "nombre": "Setos", "regando": False},
                          {"numero": 3, "nombre": "Huerto", "regando": False}],
                "lluvia_detectada": False, "retraso_lluvia_dias": 0, "conectado": True}},
    {"id": "demo:entrada", "marca": "YI", "tipo": "camara", "nombre": "Entrada", "grupo": "",
     "estado": {"conectado": True}},
]

FOTO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#23302e"/><rect y="250" width="640" height="110" fill="#2f403d"/>
<rect x="250" y="120" width="140" height="150" fill="#3b4f4b"/><circle cx="360" cy="200" r="6" fill="#9fb3ae"/>
<text x="20" y="36" fill="#cfdcd9" font-family="monospace" font-size="20">DEMO · {hora}</text></svg>"""


class Demo:
    prefijo = "demo"

    def __init__(self):
        self.aparatos = {d["id"].split(":", 1)[1]: copy.deepcopy(d) for d in INICIO}
        self.avisos: list[str] = []

    async def listar(self) -> list[dict[str, Any]]:
        for d in self.aparatos.values():
            e = d["estado"]
            if "temperatura" in e:
                e["temperatura"] = round(e["temperatura"] + random.uniform(-0.1, 0.1), 1)
            if "potencia_w" in e:
                e["potencia_w"] = round(random.uniform(1150, 1250), 1) if e["encendido"] else 0.0
        return copy.deepcopy(list(self.aparatos.values()))

    async def accion(self, id_local: str, accion: str, datos: dict[str, Any]) -> None:
        e = self.aparatos[id_local]["estado"]
        if accion in ("encender", "apagar"):
            e["encendido"] = accion == "encender"
        elif accion == "brillo":
            e["brillo"] = max(1, min(100, int(datos["brillo"])))
        elif accion == "consigna":
            e["consigna"], e["modo"] = float(datos["temperatura"]), "manual"
        elif accion == "programa":
            e["consigna"], e["modo"] = 20.0, "schedule"
        elif accion == "regar":
            for z in e["zonas"]:
                z["regando"] = z["numero"] == int(datos["zona"])
        elif accion == "parar":
            for z in e["zonas"]:
                z["regando"] = False
        elif accion == "retraso":
            e["retraso_lluvia_dias"] = int(datos["dias"])
        else:
            raise ValueError(f"acción desconocida: {accion}")

    async def foto(self, id_local: str) -> tuple[bytes, str]:
        import datetime
        hora = datetime.datetime.now().strftime("%H:%M:%S")
        return FOTO.format(hora=hora).encode(), "image/svg+xml"
