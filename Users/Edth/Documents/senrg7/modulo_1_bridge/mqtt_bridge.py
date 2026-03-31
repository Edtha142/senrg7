#!/usr/bin/env python3
"""
mqtt_bridge.py — Suscripción MQTT → SQLite con detección de estados y eventos.
SENRG7 — Módulo 1 (Bridge)

Flujo:
  ESP32 publica → Mosquitto → este proceso suscribe → senrg7.db

Ejecutar:
    python mqtt_bridge.py
"""

import json
import logging
import signal
import sys
import time
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt

# Importar helpers de BD del mismo módulo
from db import cargar_config, get_db, inicializar_db, limpiar_datos_antiguos

# ── Logger ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "bridge.log", encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# ── Constantes de estados ────────────────────────────────
ESTADO_APAGADA    = "APAGADA"
ESTADO_ENCENDIDA  = "ENCENDIDA"
ESTADO_BORDANDO   = "BORDANDO"
ESTADO_SOBRECARGA = "SOBRECARGA"

# ── Topics MQTT ──────────────────────────────────────────
TOPIC_CORRIENTE  = "senrg7/maquina/+/corriente"
TOPIC_EVENTO     = "senrg7/maquina/+/evento"
TOPIC_HEARTBEAT  = "senrg7/sistema/heartbeat"


class HistorialMaquina:
    """
    Mantiene un buffer deslizante de lecturas recientes por máquina.
    Permite detectar caídas bruscas de corriente (rotura de hilo).
    """

    def __init__(self, ventana_segundos: int):
        self.ventana       = ventana_segundos
        self.lecturas      = deque()   # tuplas (timestamp, corriente)
        self.estado_previo = None
        self.corriente_max = 0.0       # máximo en la ventana actual

    def registrar(self, corriente: float, ts: float) -> None:
        """Agrega una lectura y elimina las fuera de la ventana."""
        self.lecturas.append((ts, corriente))
        # Limpiar lecturas antiguas
        while self.lecturas and (ts - self.lecturas[0][0]) > self.ventana:
            self.lecturas.popleft()
        # Recalcular máximo dentro de la ventana
        if self.lecturas:
            self.corriente_max = max(c for _, c in self.lecturas)
        else:
            self.corriente_max = 0.0

    def detectar_rotura_hilo(self, corriente_actual: float, caida_minima: float) -> bool:
        """
        Retorna True si se detecta una caída brusca compatible con rotura de hilo.
        Condición: venía BORDANDO y cayó al menos caida_minima amperios respecto
        al máximo reciente.
        """
        if self.estado_previo != ESTADO_BORDANDO:
            return False
        if len(self.lecturas) < 2:
            return False
        caida = self.corriente_max - corriente_actual
        return caida >= caida_minima


def determinar_estado(corriente: float, umbrales: dict) -> str:
    """
    Clasifica el estado de la máquina según la corriente medida.

    Rangos (con valores por defecto):
        APAGADA:    corriente < 0.5 A
        ENCENDIDA:  0.5 A  ≤ corriente < 2.0 A
        BORDANDO:   2.0 A  ≤ corriente < 20.0 A
        SOBRECARGA: corriente ≥ 20.0 A
    """
    if corriente >= umbrales["sobrecarga"]:
        return ESTADO_SOBRECARGA
    elif corriente >= umbrales["encendida"]:
        return ESTADO_BORDANDO
    elif corriente >= umbrales["apagada"]:
        return ESTADO_ENCENDIDA
    else:
        return ESTADO_APAGADA


class MqttBridge:
    """
    Suscribe a todos los topics de SENRG7, procesa mensajes y persiste en SQLite.
    Detecta cambios de estado, roturas de hilo y sobrecargas.
    """

    def __init__(self, config: dict):
        self.config    = config
        self.umbrales  = config["umbrales"]
        self.det_cfg   = config["deteccion_eventos"]
        self.corriendo = True

        # Estado por máquina: {maquina_id: HistorialMaquina}
        self.historiales: dict[int, HistorialMaquina] = {}

        # Inicializar historial para cada máquina configurada
        ventana = self.det_cfg["rotura_hilo_ventana_segundos"]
        for m in config.get("maquinas", []):
            self.historiales[m["id"]] = HistorialMaquina(ventana)

        # Cliente MQTT (API v1 de callbacks para compatibilidad)
        self.cliente = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id="senrg7_bridge",
        )
        self.cliente.on_connect    = self._on_connect
        self.cliente.on_disconnect = self._on_disconnect
        self.cliente.on_message    = self._on_message
        self.cliente.reconnect_delay_set(min_delay=2, max_delay=60)

        # Configurar credenciales si están definidas
        usuario = config["red"].get("mqtt_usuario", "")
        passwd  = config["red"].get("mqtt_password", "")
        if usuario:
            self.cliente.username_pw_set(usuario, passwd)

    # ── Callbacks MQTT ───────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc: int) -> None:
        if rc == 0:
            log.info("MQTT conectado al broker")
            client.subscribe(TOPIC_CORRIENTE)
            client.subscribe(TOPIC_EVENTO)
            client.subscribe(TOPIC_HEARTBEAT)
            log.info("Suscrito a: senrg7/#")
        else:
            log.error(f"MQTT falló la conexión, código: {rc}")

    def _on_disconnect(self, client, userdata, rc: int) -> None:
        if rc != 0:
            log.warning(f"MQTT desconectado inesperadamente (rc={rc}), reconectando...")

    def _on_message(self, client, userdata, msg) -> None:
        topic   = msg.topic
        payload = msg.payload.decode("utf-8", errors="ignore").strip()

        try:
            if "/corriente" in topic:
                datos = json.loads(payload)
                self._procesar_corriente(topic, datos)
            elif "/evento" in topic:
                datos = json.loads(payload)
                self._procesar_evento_externo(topic, datos)
            elif topic == TOPIC_HEARTBEAT.replace("#", "heartbeat"):
                log.debug(f"Heartbeat recibido: {payload}")
        except json.JSONDecodeError as e:
            log.warning(f"Payload JSON inválido en '{topic}': {e} | Payload: {payload[:80]}")
        except Exception as e:
            log.error(f"Error procesando mensaje de '{topic}': {e}")

    # ── Procesamiento de corriente ───────────────────────

    def _procesar_corriente(self, topic: str, datos: dict) -> None:
        """
        Valida el payload, determina el estado, detecta cambios y roturas,
        y persiste lectura + eventos en la BD.
        """
        maquina_id = datos.get("maquina_id")
        corriente  = datos.get("corriente")

        if maquina_id is None or corriente is None:
            log.warning(f"Payload de corriente incompleto: {datos}")
            return

        maquina_id = int(maquina_id)
        corriente  = float(corriente)
        ts_ahora   = time.time()

        # Obtener o crear historial para esta máquina
        if maquina_id not in self.historiales:
            ventana = self.det_cfg["rotura_hilo_ventana_segundos"]
            self.historiales[maquina_id] = HistorialMaquina(ventana)

        historial = self.historiales[maquina_id]

        # Determinar estado actual
        estado_actual = determinar_estado(corriente, self.umbrales)

        # ── Detectar rotura de hilo (antes de actualizar historial)
        caida_minima = self.det_cfg["rotura_hilo_caida_minima"]
        if historial.detectar_rotura_hilo(corriente, caida_minima):
            desc = (
                f"Caída de {historial.corriente_max:.2f}A → {corriente:.2f}A "
                f"(caída de {historial.corriente_max - corriente:.2f}A)"
            )
            log.warning(f"[M{maquina_id}] ROTURA DE HILO detectada: {desc}")
            self._guardar_evento(maquina_id, "ROTURA_HILO", desc)

        # ── Detectar cambio de estado
        estado_previo = historial.estado_previo
        if estado_previo is not None and estado_actual != estado_previo:
            desc = f"{estado_previo} → {estado_actual} ({corriente:.2f}A)"
            log.info(f"[M{maquina_id}] Cambio de estado: {desc}")
            self._guardar_evento(maquina_id, "CAMBIO_ESTADO", desc)

            # Registrar eventos específicos de inicio/fin de bordado
            if estado_actual == ESTADO_BORDANDO:
                self._guardar_evento(maquina_id, "INICIO", "Bordado iniciado")
            elif estado_previo == ESTADO_BORDANDO and estado_actual in (
                ESTADO_ENCENDIDA, ESTADO_APAGADA
            ):
                self._guardar_evento(maquina_id, "PAUSA", "Bordado pausado o detenido")

            # Alerta de sobrecarga
            if estado_actual == ESTADO_SOBRECARGA:
                log.warning(
                    f"[M{maquina_id}] SOBRECARGA detectada: {corriente:.2f}A"
                )
                self._guardar_evento(
                    maquina_id,
                    "SOBRECARGA",
                    f"Corriente de sobrecarga: {corriente:.2f}A",
                )

        # Actualizar historial con la lectura actual
        historial.registrar(corriente, ts_ahora)
        historial.estado_previo = estado_actual

        # ── Persistir lectura en BD
        self._guardar_lectura(maquina_id, corriente, estado_actual)

        log.debug(f"[M{maquina_id}] {corriente:.3f}A | {estado_actual}")

    def _procesar_evento_externo(self, topic: str, datos: dict) -> None:
        """Registra eventos publicados directamente por el ESP32."""
        maquina_id = datos.get("maquina_id")
        tipo       = datos.get("tipo", "DESCONOCIDO")
        desc       = datos.get("descripcion", "")
        if maquina_id is None:
            log.warning(f"Evento externo sin maquina_id: {datos}")
            return
        log.info(f"[M{maquina_id}] Evento externo: {tipo} — {desc}")
        self._guardar_evento(int(maquina_id), tipo, desc)

    # ── Persistencia en BD ───────────────────────────────

    def _guardar_lectura(self, maquina_id: int, corriente: float, estado: str) -> None:
        """Inserta una fila en la tabla lecturas."""
        try:
            with get_db(self.config) as conn:
                conn.execute(
                    "INSERT INTO lecturas (maquina_id, corriente, estado) VALUES (?, ?, ?)",
                    (maquina_id, round(corriente, 3), estado),
                )
        except Exception as e:
            log.error(f"Error guardando lectura de M{maquina_id}: {e}")

    def _guardar_evento(self, maquina_id: int, tipo: str, descripcion: str) -> None:
        """Inserta una fila en la tabla eventos."""
        try:
            with get_db(self.config) as conn:
                conn.execute(
                    "INSERT INTO eventos (maquina_id, tipo, descripcion) VALUES (?, ?, ?)",
                    (maquina_id, tipo, descripcion),
                )
        except Exception as e:
            log.error(f"Error guardando evento {tipo} de M{maquina_id}: {e}")

    # ── Ciclo de vida ────────────────────────────────────

    def iniciar(self) -> None:
        """Conecta al broker y entra en el loop de mensajes (bloqueante)."""
        broker = self.config["red"]["mqtt_broker"]
        puerto = self.config["red"]["mqtt_puerto"]
        log.info(f"Conectando a MQTT broker {broker}:{puerto}...")

        try:
            self.cliente.connect(broker, puerto, keepalive=60)
        except ConnectionRefusedError:
            log.error(
                f"No se pudo conectar a {broker}:{puerto}. "
                "¿Está Mosquitto corriendo?"
            )
            sys.exit(1)
        except OSError as e:
            log.error(f"Error de red al conectar al broker: {e}")
            sys.exit(1)

        log.info("Bridge SENRG7 iniciado. Esperando mensajes MQTT...")
        self.cliente.loop_forever()  # bloquea, maneja reconexiones automáticamente

    def detener(self) -> None:
        """Detiene el loop MQTT limpiamente."""
        log.info("Deteniendo bridge...")
        self.corriendo = False
        self.cliente.loop_stop()
        self.cliente.disconnect()
        log.info("Bridge detenido.")


# ── Punto de entrada standalone ──────────────────────────
if __name__ == "__main__":
    # Cargar configuración
    config = cargar_config()

    # Inicializar BD (crea tablas si no existen, sincroniza máquinas)
    inicializar_db(config)

    # Limpiar datos antiguos al arrancar
    limpiar_datos_antiguos(config)

    # Crear e iniciar el bridge
    bridge = MqttBridge(config)

    # Manejar Ctrl+C y señales de terminación limpiamente
    def _handler(signum, frame):
        bridge.detener()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _handler)
    signal.signal(signal.SIGTERM, _handler)

    bridge.iniciar()
