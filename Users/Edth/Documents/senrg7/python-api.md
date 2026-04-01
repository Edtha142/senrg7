# Skill: Python API y Bridge

## Versión y entorno
- Python 3.10+ en Windows 10/11
- Un virtualenv por módulo (nunca instalar paquetes globalmente)
- Crear venv: `python -m venv venv`
- Activar en Windows: `venv\Scripts\activate`

## Estructura estándar de cada archivo Python
```python
#!/usr/bin/env python3
"""
nombre_archivo.py — Descripción en una línea.
SENRG7 — Módulo N
"""

# ── Imports estándar ─────────────────────────────────────
import logging
import json
from pathlib import Path

# ── Imports de terceros ──────────────────────────────────
# (paquetes de requirements.txt)

# ── Configuración — leer desde config.json ───────────────
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def cargar_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

config = cargar_config()

# Variables desde config (MAYÚSCULAS)
MQTT_BROKER = config["red"]["mqtt_broker"]
# etc.

# ── Logger ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("nombre.log", encoding="utf-8")
    ]
)
log = logging.getLogger(__name__)

# ── Código ───────────────────────────────────────────────
```

## Manejo de errores — SIEMPRE explícito
```python
# CORRECTO
try:
    resultado = operacion_riesgosa()
except sqlite3.Error as e:
    log.error(f"Error de base de datos: {e}")
    return None
except Exception as e:
    log.error(f"Error inesperado: {e}")
    raise

# NUNCA hacer esto
try:
    resultado = operacion_riesgosa()
except:
    pass
```

## SQLite — patrón context manager
```python
import sqlite3
from contextlib import contextmanager

@contextmanager
def get_db(config: dict):
    ruta = config["base_datos"]["ruta"]
    conn = sqlite3.connect(ruta, check_same_thread=False)
    conn.row_factory = sqlite3.Row      # acceso por nombre de columna
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"Error BD: {e}")
        raise
    finally:
        conn.close()

# Uso:
with get_db(config) as conn:
    conn.execute("INSERT INTO lecturas ...", (valores,))
```

## MQTT — cliente Paho
```python
import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("MQTT conectado")
        client.subscribe("senrg7/#")
    else:
        log.error(f"MQTT error: código {rc}")

def on_message(client, userdata, msg):
    topic   = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore")
    try:
        datos = json.loads(payload)
        # procesar datos...
    except json.JSONDecodeError as e:
        log.warning(f"Payload inválido en {topic}: {e}")

client = mqtt.Client(client_id="senrg7_bridge")
client.on_connect = on_connect
client.on_message = on_message
client.reconnect_delay_set(min_delay=1, max_delay=30)
client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
client.loop_start()  # hilo en background — no bloquea
```

## FastAPI — patrones estándar
```python
from fastapi import FastAPI, WebSocket, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

app = FastAPI(title="SENRG7", version="1.0.0")

# Montar frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Health check — siempre incluir
@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

# Proteger rutas con dependencia de auth
@app.get("/api/maquinas")
async def get_maquinas(usuario: str = Depends(verificar_token)):
    ...

# Correr: uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
```

## Guardar config.json desde Python
```python
# SIEMPRE utf-8 sin BOM — nunca 'utf-8-sig'
with open(CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
```

## Requirements por módulo

### modulo_1_bridge/requirements.txt
```
paho-mqtt==1.6.1
```

### modulo_2_api/requirements.txt
```
fastapi==0.111.0
uvicorn[standard]==0.29.0
paho-mqtt==1.6.1
websockets==12.0
python-multipart==0.0.9
```

### modulo_4_alertas/requirements.txt (semana 2)
```
paho-mqtt==1.6.1
python-telegram-bot==20.7
```
