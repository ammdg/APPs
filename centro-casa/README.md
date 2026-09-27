# Centro de control de casa

Una página web para ver y manejar desde el móvil o el ordenador los aparatos de casa:
**Netatmo** (estación, termostato, cámaras), **Rain Bird** (riego), **Wemo** (enchufes) y
**cámaras YI**.

- Clima: temperatura, humedad, CO₂, ruido, presión; subir o bajar la consigna del termostato.
- Enchufes: encender y apagar, consumo (Wemo Insight) y brillo (Wemo Dimmer).
- Riego: ver qué zona riega, regar una zona X minutos, parar y poner retraso por lluvia.
- Cámaras: ver la última foto.

Se refresca sola cada 30 segundos.

## Por qué hace falta un pequeño servidor en casa

No basta con una página en GitHub Pages: tres de las cuatro marcas **no se pueden controlar desde
internet con una API pública**, así que alguien dentro de tu red tiene que hablar con ellas.

| Marca | Cómo se conecta | Qué hay que saber |
|---|---|---|
| Netatmo | Su nube, con la **API oficial** (dev.netatmo.com) | Hay que crear una "app" gratuita en dev.netatmo.com. |
| Rain Bird | **Red local**, directo al módulo WiFi LNK/LNK2 | Rain Bird no publica una API de su nube. Se usa [pyrainbird](https://github.com/allenporter/pyrainbird), la librería que usa Home Assistant (no es oficial). |
| Wemo | **Red local** | Belkin [cerró la nube de Wemo el 31/01/2026](https://www.belkin.com/support-article/?articleNum=335419): ya no hay app ni acceso remoto oficial. En la red local siguen respondiendo; se usa [pywemo](https://github.com/pywemo/pywemo). |
| YI Home | **Red local**, con firmware libre | La nube de YI no tiene API pública. Hay que instalar en la cámara el firmware [yi-hack](https://github.com/roleoroleo/yi-hack-MStar) de su modelo (MStar, Allwinner-v2…), que da una URL de foto. Si no quieres tocar el firmware, las YI no se pueden ver aquí. |

El servidor (`servidor.py`) corre en cualquier ordenador de casa que esté siempre encendido: una
Raspberry Pi, un NAS con Docker, un PC viejo… Sirve la página y habla con los aparatos.

```
móvil / PC ──► servidor.py (en casa) ──► Netatmo (internet, API oficial)
                                     ├─► Rain Bird (red local)
                                     ├─► Wemo (red local)
                                     └─► Cámaras YI con yi-hack (red local)
```

## 1. Pruébalo sin configurar nada

```bash
cd centro-casa
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python servidor.py --demo
```

Abre http://localhost:8090 y verás aparatos de mentira que responden a los botones.

## 2. Configura tus aparatos: `config.json`

```bash
cp config.ejemplo.json config.json
```

Borra las secciones de las marcas que no tengas. `config.json` está en `.gitignore`: **no lo subas
al repositorio**, lleva tus claves.

- `clave_acceso`: la clave que pedirá la página. Ponla siempre.

### Netatmo

1. Entra en https://dev.netatmo.com con tu cuenta Netatmo → *My apps* → *Create*.
2. Copia `client_id` y `client_secret`.
3. En la ficha de la app, en *Token generator*, marca los permisos que necesites
   (`read_station`, `read_thermostat`, `write_thermostat`, `read_camera`, `access_camera`…),
   genera el token y copia el **refresh token** en `refresh_token`.
4. Si no tienes estación, termostato o cámaras, pon `"estaciones": false`, etc.

Netatmo cambia el refresh token cada vez que renueva el acceso; el servidor guarda el último en
`netatmo_token.json` (también ignorado por git). Si lo borras, vuelve a usar el de `config.json`, que
seguramente ya habrá caducado: genera uno nuevo.

### Rain Bird

- `ip`: la IP del controlador en tu red (mírala en el router). Conviene fijarla en el router.
- `clave`: la contraseña del controlador que pusiste en la app Rain Bird.
- `zonas`: nombres opcionales por número de zona.

El módulo WiFi atiende peticiones de una en una y a veces responde "ocupado"; si falla, sale un
aviso arriba de la página y se reintenta en el siguiente refresco.

### Wemo

Con `"buscar": true` los busca solos en la red (necesita que el servidor esté en la misma red, no
funciona desde Docker salvo con `network_mode: host`). Si alguno no aparece, añade su IP en `ips`.

### Cámaras YI

Una entrada por cámara con `url_foto`. Con yi-hack suele ser
`http://IP-CAMARA:8080/cgi-bin/snapshot.sh?res=low` (el puerto y la ruta dependen de la versión del
firmware: compruébalo abriéndola en el navegador). Si le pusiste usuario y contraseña, rellena
`usuario` y `clave`. Vale cualquier cámara que dé una foto JPEG por URL.

## 3. Arráncalo

```bash
python servidor.py
```

Abre `http://IP-DEL-SERVIDOR:8090` desde el móvil (en la misma WiFi) y añádela a la pantalla de
inicio. Opciones: `--puerto`, `--config`, `--host` (`127.0.0.1` = solo desde ese ordenador).

### Que arranque solo (Raspberry Pi / Linux)

`/etc/systemd/system/centro-casa.service`:

```ini
[Unit]
Description=Centro de control de casa
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/APPs/centro-casa
ExecStart=/home/pi/APPs/centro-casa/.venv/bin/python servidor.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now centro-casa
```

## Verla fuera de casa

**No abras el puerto en el router.** La página enciende enchufes y riega; la clave y la conexión
son HTTP simple, pensadas para la red de casa. Para usarla fuera, lo más sencillo y seguro es una
VPN como [Tailscale](https://tailscale.com) (gratis para uso personal) en el servidor y en el móvil:
abres `http://nombre-del-servidor:8090` como si estuvieras en casa.

## Alternativa: Home Assistant

Si prefieres algo ya hecho y con automatizaciones, [Home Assistant](https://www.home-assistant.io)
tiene integraciones para Netatmo, Rain Bird y Wemo, y para YI con yi-hack. Esta página es más
simple: un solo archivo de configuración y una pantalla.

## Pruebas

```bash
python -m unittest -v
```

Prueban la lectura de las respuestas de Netatmo, el modo demostración y la clave de acceso. No hay
pruebas contra aparatos reales: lo de Rain Bird, Wemo y yi-hack está escrito según sus librerías y
documentación, pero **no se ha probado con tus aparatos**.

## Añadir otra marca

Crea `marcas/otra.py` con una clase que tenga `prefijo`, `avisos`, `async listar()`,
`async accion(id, accion, datos)` y, si es una cámara, `async foto(id)`. Los dispositivos siguen la
forma descrita en `marcas/__init__.py`. Luego regístrala en `crear_adaptadores()` de `servidor.py`.
