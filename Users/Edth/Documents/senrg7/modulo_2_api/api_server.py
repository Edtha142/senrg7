#!/usr/bin/env python3
"""
api_server.py — FastAPI: REST + WebSocket + autenticación por sesión + frontend.
SENRG7 — Módulo 2 (API)

Endpoints principales:
    POST /login                        — autenticación
    POST /logout                       — cerrar sesión
    GET  /                             — redirige al dashboard o login
    GET  /dashboard                    — SPA principal (protegida)
    GET  /configuracion                — panel de config (protegida)
    GET  /health                       — health check público
    GET  /api/maquinas                 — estado actual de todas las máquinas
    GET  /api/maquinas/{id}/lecturas   — historial de corriente
    GET  /api/resumen                  — resumen productivo del día
    GET  /api/eventos                  — eventos recientes
    GET  /api/config                   — leer configuración
    PUT  /api/config                   — guardar configuración
    WS   /ws                           — stream en tiempo real

Ejecutar:
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator

import paho.mqtt.client as mqtt
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# ── Rutas ────────────────────────────────────────────────
PROYECTO_ROOT = Path(__file__).parent.parent
CONFIG_PATH   = PROYECTO_ROOT / "config.json"
FRONTEND_DIR  = Path(__file__).parent / "frontend"

# Agregar modulo_1_bridge al path para importar db.py
sys.path.insert(0, str(PROYECTO_ROOT / "modulo_1_bridge"))
from db import cargar_config, get_db, inicializar_db  # noqa: E402

# ── Logger ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "api.log", encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  GESTOR DE CONEXIONES WEBSOCKET
# ══════════════════════════════════════════════════════════

class GestorWebSocket:
    """
    Mantiene el conjunto de clientes WebSocket conectados.
    Permite difundir mensajes a todos desde el hilo MQTT (thread-safe).
    """

    def __init__(self):
        self.clientes: set[WebSocket] = set()
        self._cola: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def configurar_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._cola = asyncio.Queue()

    async def conectar(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clientes.add(ws)
        log.info(f"WebSocket conectado. Clientes activos: {len(self.clientes)}")

    def desconectar(self, ws: WebSocket) -> None:
        self.clientes.discard(ws)
        log.info(f"WebSocket desconectado. Clientes activos: {len(self.clientes)}")

    def encolar_desde_hilo(self, mensaje: dict) -> None:
        """
        Llamado desde el hilo de paho-mqtt.
        Usa run_coroutine_threadsafe para cruzar al event loop de asyncio.
        """
        if self._loop and self._cola and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._cola.put(mensaje), self._loop
            )

    async def procesar_cola(self) -> None:
        """
        Tarea asyncio que lee la cola de mensajes y los difunde a todos
        los clientes WebSocket activos.
        """
        while True:
            mensaje = await self._cola.get()
            await self._difundir(mensaje)

    async def _difundir(self, mensaje: dict) -> None:
        if not self.clientes:
            return
        data = json.dumps(mensaje, ensure_ascii=False, default=str)
        caidos: set[WebSocket] = set()
        for ws in self.clientes:
            try:
                await ws.send_text(data)
            except Exception:
                caidos.add(ws)
        self.clientes -= caidos


gestor_ws = GestorWebSocket()


# ══════════════════════════════════════════════════════════
#  CLIENTE MQTT (hilo separado, alimenta el GestorWebSocket)
# ══════════════════════════════════════════════════════════

cliente_mqtt: mqtt.Client | None = None


def _mqtt_on_connect(client, userdata, flags, rc: int) -> None:
    if rc == 0:
        log.info("API-MQTT conectado al broker")
        client.subscribe("senrg7/#")
    else:
        log.warning(f"API-MQTT no pudo conectar, rc={rc}")


def _mqtt_on_message(client, userdata, msg) -> None:
    """
    Recibe mensajes MQTT y los reenvía al GestorWebSocket
    para que los clientes del dashboard los reciban en tiempo real.
    """
    topic   = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()
    try:
        if "/corriente" in topic:
            datos = json.loads(payload)
            gestor_ws.encolar_desde_hilo({"tipo": "lectura", **datos})
        elif "/evento" in topic:
            datos = json.loads(payload)
            gestor_ws.encolar_desde_hilo({"tipo": "evento", **datos})
        elif "heartbeat" in topic:
            gestor_ws.encolar_desde_hilo(
                {"tipo": "heartbeat", "mensaje": payload}
            )
    except json.JSONDecodeError:
        pass  # payload malformado — no reenviar


def iniciar_mqtt(config: dict) -> None:
    """Crea el cliente MQTT de la API y lo conecta en background."""
    global cliente_mqtt
    cliente_mqtt = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
        client_id="senrg7_api",
    )
    cliente_mqtt.on_connect = _mqtt_on_connect
    cliente_mqtt.on_message = _mqtt_on_message
    cliente_mqtt.reconnect_delay_set(min_delay=2, max_delay=60)

    usuario = config["red"].get("mqtt_usuario", "")
    passwd  = config["red"].get("mqtt_password", "")
    if usuario:
        cliente_mqtt.username_pw_set(usuario, passwd)

    broker = config["red"]["mqtt_broker"]
    puerto = config["red"]["mqtt_puerto"]
    try:
        cliente_mqtt.connect(broker, puerto, keepalive=60)
        cliente_mqtt.loop_start()   # hilo en background — no bloquea
        log.info(f"API-MQTT iniciado → {broker}:{puerto}")
    except Exception as e:
        log.warning(f"API-MQTT no conectó al broker: {e}. El WebSocket estará inactivo.")


# ══════════════════════════════════════════════════════════
#  APLICACIÓN FASTAPI — LIFESPAN
# ══════════════════════════════════════════════════════════

_config: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Inicialización y apagado de la aplicación."""
    global _config

    # Cargar configuración
    _config = cargar_config()
    log.info(f"SENRG7 v{_config['sistema']['version']} iniciando...")

    # Inicializar BD
    inicializar_db(_config)

    # Configurar loop de asyncio en el gestor de WebSockets
    loop = asyncio.get_running_loop()
    gestor_ws.configurar_loop(loop)

    # Iniciar tarea de difusión de mensajes WebSocket
    tarea_ws = asyncio.create_task(gestor_ws.procesar_cola())

    # Iniciar cliente MQTT
    iniciar_mqtt(_config)

    log.info(f"API escuchando en http://{_config['red']['api_host']}:{_config['red']['api_puerto']}")
    yield  # ← aquí corre la aplicación

    # Apagado limpio
    tarea_ws.cancel()
    if cliente_mqtt:
        cliente_mqtt.loop_stop()
        cliente_mqtt.disconnect()
    log.info("API detenida.")


app = FastAPI(
    title="SENRG7",
    version="1.0.0",
    description="Sistema IoT de Monitoreo de Energía",
    lifespan=lifespan,
    docs_url="/docs",  # Swagger UI — útil para demostración de tesis
)


# ══════════════════════════════════════════════════════════
#  HELPERS DE AUTENTICACIÓN
# ══════════════════════════════════════════════════════════

def _verificar_token(token: str) -> str | None:
    """
    Verifica que el token exista en la BD y no haya expirado.
    Retorna el nombre de usuario o None si inválido.
    """
    try:
        with get_db(_config) as conn:
            fila = conn.execute(
                "SELECT usuario FROM sesiones WHERE token = ? AND expira_en > datetime('now')",
                (token,),
            ).fetchone()
        return fila["usuario"] if fila else None
    except Exception as e:
        log.error(f"Error verificando token: {e}")
        return None


def _crear_sesion(usuario: str) -> str:
    """Genera un token seguro y lo guarda en la BD."""
    token = secrets.token_urlsafe(32)
    horas = _config["auth"]["duracion_sesion_horas"]
    expira = datetime.utcnow() + timedelta(hours=horas)
    with get_db(_config) as conn:
        conn.execute(
            "INSERT INTO sesiones (token, usuario, expira_en) VALUES (?, ?, ?)",
            (token, usuario, expira.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return token


def _eliminar_sesion(token: str) -> None:
    """Elimina el token de la BD (logout)."""
    with get_db(_config) as conn:
        conn.execute("DELETE FROM sesiones WHERE token = ?", (token,))


async def sesion_requerida(
    request: Request,
    session_token: str | None = Cookie(default=None),
) -> str:
    """
    Dependencia FastAPI para proteger endpoints REST.
    Lanza 401 si no hay sesión válida.
    """
    if not session_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    usuario = _verificar_token(session_token)
    if not usuario:
        raise HTTPException(status_code=401, detail="Sesión expirada o inválida")
    return usuario


async def pagina_protegida(
    request: Request,
    session_token: str | None = Cookie(default=None),
) -> str:
    """
    Dependencia para proteger páginas HTML.
    Redirige a /login si no hay sesión válida.
    """
    if not session_token:
        return RedirectResponse("/login", status_code=303)
    usuario = _verificar_token(session_token)
    if not usuario:
        return RedirectResponse("/login", status_code=303)
    return usuario


# ══════════════════════════════════════════════════════════
#  RUTAS DE AUTENTICACIÓN
# ══════════════════════════════════════════════════════════

@app.get("/login", response_class=FileResponse, include_in_schema=False)
async def pagina_login():
    return FileResponse(FRONTEND_DIR / "login.html")


@app.post("/login", include_in_schema=False)
async def hacer_login(
    username: str = Form(...),
    password: str = Form(...),
):
    """Verifica credenciales y crea sesión."""
    cfg_auth = _config["auth"]
    if username != cfg_auth["usuario"] or password != cfg_auth["password"]:
        # Retornar login con error (sin revelar qué campo falló)
        return FileResponse(
            FRONTEND_DIR / "login.html",
            status_code=401,
            headers={"X-Login-Error": "Credenciales incorrectas"},
        )

    token    = _crear_sesion(username)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=_config["auth"]["duracion_sesion_horas"] * 3600,
    )
    log.info(f"Login exitoso: {username}")
    return response


@app.post("/logout", include_in_schema=False)
async def hacer_logout(session_token: str | None = Cookie(default=None)):
    """Elimina la sesión y redirige al login."""
    if session_token:
        _eliminar_sesion(session_token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session_token")
    return response


# ══════════════════════════════════════════════════════════
#  RUTAS DE PÁGINAS HTML
# ══════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
async def raiz(session_token: str | None = Cookie(default=None)):
    """Redirige al dashboard si hay sesión activa, si no al login."""
    if session_token and _verificar_token(session_token):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def pagina_dashboard(usuario: str = Depends(pagina_protegida)):
    if isinstance(usuario, RedirectResponse):
        return usuario
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/configuracion", response_class=HTMLResponse, include_in_schema=False)
async def pagina_configuracion(usuario: str = Depends(pagina_protegida)):
    if isinstance(usuario, RedirectResponse):
        return usuario
    return FileResponse(FRONTEND_DIR / "config.html")


@app.get("/reporte", response_class=HTMLResponse, include_in_schema=False)
async def pagina_reporte(usuario: str = Depends(pagina_protegida)):
    if isinstance(usuario, RedirectResponse):
        return usuario
    return FileResponse(FRONTEND_DIR / "reporte.html")


# ══════════════════════════════════════════════════════════
#  API REST
# ══════════════════════════════════════════════════════════

@app.get("/health", tags=["Sistema"])
async def health_check():
    """Health check público — útil para scripts de monitoreo."""
    return {
        "status": "ok",
        "version": _config["sistema"]["version"],
        "nombre": _config["sistema"]["nombre"],
    }


@app.get("/api/maquinas", tags=["Máquinas"])
async def get_maquinas(usuario: str = Depends(sesion_requerida)):
    """
    Retorna todas las máquinas activas con su última lectura de corriente.
    """
    resultado = []
    with get_db(_config) as conn:
        maquinas = conn.execute(
            "SELECT * FROM maquinas WHERE activa = 1 ORDER BY id"
        ).fetchall()

        for m in maquinas:
            # Última lectura
            ultima = conn.execute(
                """SELECT corriente, estado, timestamp FROM lecturas
                   WHERE maquina_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (m["id"],),
            ).fetchone()

            # Conteo de roturas de hilo del día
            roturas = conn.execute(
                """SELECT COUNT(*) as total FROM eventos
                   WHERE maquina_id = ? AND tipo = 'ROTURA_HILO'
                   AND timestamp >= date('now')""",
                (m["id"],),
            ).fetchone()["total"]

            resultado.append({
                "id":                m["id"],
                "nombre":            m["nombre"],
                "descripcion":       m["descripcion"],
                "factor_calibracion":m["factor_calibracion"],
                "corriente":         ultima["corriente"]  if ultima else None,
                "estado":            ultima["estado"]     if ultima else "SIN_DATOS",
                "ultima_lectura":    ultima["timestamp"]  if ultima else None,
                "roturas_hoy":       roturas,
            })
    return resultado


@app.get("/api/maquinas/{maquina_id}/lecturas", tags=["Máquinas"])
async def get_lecturas(
    maquina_id: int,
    minutos: int = 60,
    usuario: str = Depends(sesion_requerida),
):
    """
    Historial de corriente de una máquina en los últimos N minutos.
    Por defecto: últimos 60 minutos.
    """
    if minutos < 1 or minutos > 1440:
        raise HTTPException(status_code=400, detail="minutos debe estar entre 1 y 1440")

    with get_db(_config) as conn:
        # Verificar que la máquina existe
        m = conn.execute(
            "SELECT id, nombre FROM maquinas WHERE id = ?", (maquina_id,)
        ).fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Máquina no encontrada")

        filas = conn.execute(
            """SELECT corriente, estado, timestamp FROM lecturas
               WHERE maquina_id = ?
                 AND timestamp >= datetime('now', ? || ' minutes')
               ORDER BY timestamp ASC""",
            (maquina_id, f"-{minutos}"),
        ).fetchall()

    return {
        "maquina_id": maquina_id,
        "nombre":     m["nombre"],
        "minutos":    minutos,
        "total":      len(filas),
        "lecturas": [
            {
                "corriente": f["corriente"],
                "estado":    f["estado"],
                "timestamp": f["timestamp"],
            }
            for f in filas
        ],
    }


@app.get("/api/resumen", tags=["Análisis"])
async def get_resumen(usuario: str = Depends(sesion_requerida)):
    """
    Resumen productivo del día actual para todas las máquinas.
    Retorna segundos en cada estado y conteo de roturas de hilo.
    """
    with get_db(_config) as conn:
        filas = conn.execute(
            """SELECT
                   maquina_id,
                   SUM(CASE WHEN estado = 'BORDANDO'   THEN 1 ELSE 0 END) AS seg_bordando,
                   SUM(CASE WHEN estado = 'ENCENDIDA'  THEN 1 ELSE 0 END) AS seg_encendida,
                   SUM(CASE WHEN estado = 'APAGADA'    THEN 1 ELSE 0 END) AS seg_apagada,
                   SUM(CASE WHEN estado = 'SOBRECARGA' THEN 1 ELSE 0 END) AS seg_sobrecarga,
                   COUNT(*)                                                AS total_lecturas
               FROM lecturas
               WHERE timestamp >= date('now')
               GROUP BY maquina_id""",
        ).fetchall()

        roturas = conn.execute(
            """SELECT maquina_id, COUNT(*) as total
               FROM eventos
               WHERE tipo = 'ROTURA_HILO' AND timestamp >= date('now')
               GROUP BY maquina_id""",
        ).fetchall()

    roturas_map = {r["maquina_id"]: r["total"] for r in roturas}

    resumen = []
    for f in filas:
        total = f["total_lecturas"] or 1  # evitar división por cero
        mid   = f["maquina_id"]
        resumen.append({
            "maquina_id":      mid,
            "seg_bordando":    f["seg_bordando"],
            "seg_encendida":   f["seg_encendida"],
            "seg_apagada":     f["seg_apagada"],
            "seg_sobrecarga":  f["seg_sobrecarga"],
            "eficiencia_pct":  round(f["seg_bordando"] / total * 100, 1),
            "roturas_hilo":    roturas_map.get(mid, 0),
        })
    return resumen


@app.get("/api/eventos", tags=["Eventos"])
async def get_eventos(
    limite: int = 50,
    usuario: str = Depends(sesion_requerida),
):
    """Eventos recientes de todas las máquinas (máx. 200)."""
    if limite < 1 or limite > 200:
        raise HTTPException(status_code=400, detail="limite debe estar entre 1 y 200")

    with get_db(_config) as conn:
        filas = conn.execute(
            """SELECT e.tipo, e.descripcion, e.timestamp, m.nombre, e.maquina_id
               FROM eventos e
               JOIN maquinas m ON m.id = e.maquina_id
               ORDER BY e.timestamp DESC
               LIMIT ?""",
            (limite,),
        ).fetchall()

    return [
        {
            "maquina_id":  f["maquina_id"],
            "maquina":     f["nombre"],
            "tipo":        f["tipo"],
            "descripcion": f["descripcion"],
            "timestamp":   f["timestamp"],
        }
        for f in filas
    ]


@app.get("/api/reporte", tags=["Análisis"])
async def get_reporte(
    fecha: str | None = None,
    formato: str = "json",
    usuario: str = Depends(sesion_requerida),
):
    """
    Reporte productivo de un día específico (default: hoy).
    fecha: YYYY-MM-DD. formato: json | csv
    """
    import csv
    import io

    if fecha is None:
        fecha = datetime.utcnow().strftime("%Y-%m-%d")

    with get_db(_config) as conn:
        maquinas_db = conn.execute(
            "SELECT id, nombre FROM maquinas WHERE activa = 1 ORDER BY id"
        ).fetchall()

        filas = conn.execute(
            """SELECT
                   maquina_id,
                   SUM(CASE WHEN estado = 'BORDANDO'   THEN 1 ELSE 0 END) AS seg_bordando,
                   SUM(CASE WHEN estado = 'ENCENDIDA'  THEN 1 ELSE 0 END) AS seg_encendida,
                   SUM(CASE WHEN estado = 'APAGADA'    THEN 1 ELSE 0 END) AS seg_apagada,
                   SUM(CASE WHEN estado = 'SOBRECARGA' THEN 1 ELSE 0 END) AS seg_sobrecarga,
                   COUNT(*) AS total_lecturas
               FROM lecturas
               WHERE date(timestamp) = ?
               GROUP BY maquina_id""",
            (fecha,),
        ).fetchall()

        roturas = conn.execute(
            """SELECT maquina_id, COUNT(*) as total
               FROM eventos
               WHERE tipo = 'ROTURA_HILO' AND date(timestamp) = ?
               GROUP BY maquina_id""",
            (fecha,),
        ).fetchall()

    nombres = {m["id"]: m["nombre"] for m in maquinas_db}
    roturas_map = {r["maquina_id"]: r["total"] for r in roturas}

    def fmt_tiempo(seg):
        h = seg // 3600
        m = (seg % 3600) // 60
        return f"{h}h {m:02d}m"

    datos = []
    for f in filas:
        total = f["total_lecturas"] or 1
        mid   = f["maquina_id"]
        datos.append({
            "maquina_id":     mid,
            "nombre":         nombres.get(mid, f"Maquina {mid}"),
            "fecha":          fecha,
            "seg_bordando":   f["seg_bordando"],
            "seg_encendida":  f["seg_encendida"],
            "seg_apagada":    f["seg_apagada"],
            "seg_sobrecarga": f["seg_sobrecarga"],
            "tiempo_bordando":   fmt_tiempo(f["seg_bordando"]),
            "tiempo_encendida":  fmt_tiempo(f["seg_encendida"]),
            "tiempo_apagada":    fmt_tiempo(f["seg_apagada"]),
            "eficiencia_pct": round(f["seg_bordando"] / total * 100, 1),
            "roturas_hilo":   roturas_map.get(mid, 0),
        })

    if formato == "csv":
        output = io.StringIO()
        campos = ["nombre", "fecha", "tiempo_bordando", "tiempo_encendida",
                  "tiempo_apagada", "eficiencia_pct", "roturas_hilo"]
        writer = csv.DictWriter(output, fieldnames=campos, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(datos)
        csv_str = output.getvalue()
        from fastapi.responses import Response
        return Response(
            content=csv_str,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=reporte_{fecha}.csv"},
        )

    return {"fecha": fecha, "maquinas": datos}


@app.get("/api/config", tags=["Configuración"])
async def get_config(usuario: str = Depends(sesion_requerida)):
    """Retorna la configuración actual (excluyendo la ruta interna _ruta_abs)."""
    cfg = cargar_config()
    # Eliminar campo interno antes de enviarlo al cliente
    cfg["base_datos"].pop("_ruta_abs", None)
    return cfg


@app.put("/api/config", tags=["Configuración"])
async def put_config(nueva_config: dict, usuario: str = Depends(sesion_requerida)):
    """
    Valida y guarda la configuración.
    Las máquinas nuevas se sincronizan en la BD inmediatamente.
    """
    global _config

    # Validaciones básicas de estructura
    campos_requeridos = ["sistema", "auth", "red", "base_datos", "maquinas",
                         "umbrales", "medicion", "deteccion_eventos", "alertas"]
    for campo in campos_requeridos:
        if campo not in nueva_config:
            raise HTTPException(
                status_code=422, detail=f"Campo requerido faltante: {campo}"
            )

    if not isinstance(nueva_config["maquinas"], list):
        raise HTTPException(status_code=422, detail="maquinas debe ser una lista")

    # Preservar la ruta de BD (no dejar que el frontend la cambie accidentalmente)
    nueva_config["base_datos"]["ruta"] = _config["base_datos"]["ruta"]

    # Guardar en disco — UTF-8 sin BOM (regla crítica para Mosquitto también)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(nueva_config, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.error(f"Error escribiendo config.json: {e}")
        raise HTTPException(status_code=500, detail="No se pudo guardar la configuración")

    # Recargar config en memoria y sincronizar máquinas en BD
    _config = cargar_config()
    from db import sincronizar_maquinas
    sincronizar_maquinas(_config)

    log.info(f"Configuración actualizada por {usuario}")
    return {"ok": True, "mensaje": "Configuración guardada correctamente"}


# ══════════════════════════════════════════════════════════
#  WEBSOCKET — STREAM EN TIEMPO REAL
# ══════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    session_token: str | None = Cookie(default=None),
):
    """
    Conexión WebSocket autenticada.
    Reenvía en tiempo real los mensajes MQTT al navegador.
    """
    # Verificar sesión antes de aceptar la conexión
    if not session_token or not _verificar_token(session_token):
        await ws.close(code=4001)
        return

    await gestor_ws.conectar(ws)
    try:
        # Mantener la conexión abierta; el cliente puede enviar "ping"
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text('{"tipo":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"WebSocket error: {e}")
    finally:
        gestor_ws.desconectar(ws)


# ── Servir archivos estáticos del frontend ───────────────
# (debe ir DESPUÉS de todas las rutas nombradas)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")


# ── Punto de entrada standalone ──────────────────────────
if __name__ == "__main__":
    import uvicorn

    cfg = cargar_config()
    uvicorn.run(
        "api_server:app",
        host=cfg["red"]["api_host"],
        port=cfg["red"]["api_puerto"],
        reload=False,
        log_level="info",
    )
