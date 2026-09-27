"""Pruebas sin red ni aparatos: python -m unittest -v"""

import unittest

from aiohttp.test_utils import TestClient, TestServer

from marcas.netatmo import leer_casa, leer_estaciones
from servidor import crear_app


class LecturaNetatmo(unittest.TestCase):
    def test_estacion_con_modulo_exterior_desconectado(self):
        body = {"devices": [{
            "_id": "70:ee:50:00:00:01", "station_name": "Casa", "module_name": "Salón",
            "dashboard_data": {"Temperature": 21.3, "Humidity": 50, "CO2": 700},
            "modules": [{"_id": "02:00:00:00:00:02", "module_name": "Exterior", "reachable": False}],
        }]}
        salon, exterior = leer_estaciones(body)
        self.assertEqual(salon["id"], "netatmo:70:ee:50:00:00:01")
        self.assertEqual(salon["estado"], {"temperatura": 21.3, "humedad": 50, "co2": 700, "conectado": True})
        self.assertEqual(exterior["nombre"], "Exterior")
        self.assertFalse(exterior["estado"]["conectado"])

    def test_casa_con_termostato_y_camara(self):
        casa = {"id": "abc", "name": "Piso", "rooms": [{"id": "1", "name": "Salón"}, {"id": "2", "name": "Baño"}],
                "modules": [{"id": "70:ee:50:aa", "name": "Puerta", "type": "NACamera"}]}
        estado = {"rooms": [{"id": "1", "therm_measured_temperature": 19.5, "therm_setpoint_temperature": 21,
                             "therm_setpoint_mode": "schedule"},
                            {"id": "2"}],
                  "modules": [{"id": "70:ee:50:aa", "type": "NACamera", "status": "on", "vpn_url": "https://v"},
                              {"id": "04:00", "type": "NATherm1"}]}
        disp, urls = leer_casa(casa, estado)
        self.assertEqual([d["tipo"] for d in disp], ["termostato", "camara"])
        self.assertEqual(disp[0]["id"], "netatmo:abc~1")
        self.assertEqual(disp[0]["estado"]["consigna"], 21)
        self.assertEqual(disp[1]["nombre"], "Puerta")
        self.assertEqual(urls, {"netatmo:abc~70:ee:50:aa": "https://v"})


class ServidorDemo(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cliente = TestClient(TestServer(crear_app({}, demo=True)))
        await self.cliente.start_server()

    async def asyncTearDown(self):
        await self.cliente.close()

    async def estado(self):
        r = await self.cliente.get("/api/estado")
        self.assertEqual(r.status, 200)
        return {d["id"]: d for d in (await r.json())["dispositivos"]}

    async def test_portada(self):
        r = await self.cliente.get("/")
        self.assertIn("Mi casa", await r.text())

    async def test_enchufe_y_termostato(self):
        r = await self.cliente.post("/api/dispositivos/demo:lampara/apagar", json={})
        self.assertEqual(r.status, 200)
        await self.cliente.post("/api/dispositivos/demo:dormitorio/consigna", json={"temperatura": 22.5})
        d = await self.estado()
        self.assertFalse(d["demo:lampara"]["estado"]["encendido"])
        self.assertEqual(d["demo:dormitorio"]["estado"]["consigna"], 22.5)

    async def test_riego(self):
        await self.cliente.post("/api/dispositivos/demo:riego/regar", json={"zona": 2, "minutos": 10})
        zonas = (await self.estado())["demo:riego"]["estado"]["zonas"]
        self.assertEqual([z["regando"] for z in zonas], [False, True, False])

    async def test_errores(self):
        r = await self.cliente.post("/api/dispositivos/demo:lampara/volar", json={})
        self.assertEqual(r.status, 400)
        r = await self.cliente.post("/api/dispositivos/nada:1/encender", json={})
        self.assertEqual(r.status, 404)

    async def test_foto(self):
        r = await self.cliente.get("/api/dispositivos/demo:entrada/foto")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.content_type, "image/svg+xml")


class Clave(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cliente = TestClient(TestServer(crear_app({"clave_acceso": "secreta"})))
        await self.cliente.start_server()

    async def asyncTearDown(self):
        await self.cliente.close()

    async def test_pide_clave(self):
        self.assertEqual((await self.cliente.get("/api/estado")).status, 401)
        self.assertEqual((await self.cliente.post("/api/entrar", json={"clave": "mala"})).status, 401)
        self.assertEqual((await self.cliente.post("/api/entrar", json={"clave": "secreta"})).status, 200)
        r = await self.cliente.get("/api/estado")
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["dispositivos"], [])


if __name__ == "__main__":
    unittest.main()
