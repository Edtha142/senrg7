#!/usr/bin/env python3
"""
telegram_bot.py — Alertas Telegram para SENRG7.
SENRG7 — Módulo 4 (Alertas)

Suscribe a los topics MQTT de eventos y envía mensajes Telegram
según la configuración en config.json (alertas.*).

Ejecutar:
    python telegram_bot.py
"""

import json
import logging
import signal
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests

# ── Rutas ────────────────────────────────────────────────
PROYECTO_ROOT = Path(__file__).parent.parent
CONFIG_PATH   = PROYECTO_ROOT / "config.json"

# Agregar modulo_1_bridge al path para reusar cargar_config
sys.path.insert(0, str(PROYECTO_ROOT / "modulo_1_bridge"))
from db import cargar_config  # noqa: E402

# ── Logger ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "alertas.log", encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# ── Topics ───────────────────────────────────────────────
TOPIC_EVENTO    = "senrg7/maquina/+/evento"
TOPIC_HEARTBEAT = "senrg7/sistema/heartbeat"

# Máximo 1 alerta por tipo/máquina cada N segundos (evita spam)
COOLDOWN_SEG = 60

# ══════════════════════════════════════════════════════════
#  CLIENTE TELEGRAM
# ══════════════════════════════════════════════════════════

class TelegramBot:
    """Envía mensajes a un chat de Telegram usando la Bot API."""

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._activo = bool(token and chat_id)

    def enviar(self, texto: str) -> bool:
        """Envía un mensaje de texto. Retorna True si tuvo éxito."""
        if not self._activo:
            log.debug("Telegram no configurado — mensaje descartado")
            return False
        url = self.BASE_URL.format(token=self.token)
        try:
            resp = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": texto, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code == 200:
                log.info(f"Telegram OK: {texto[:60]}...")
                return True
            log.warning(f"Telegram error {resp.status_code}: {resp.text[:120]}")
            return False
        except requests.RequestException as e:
            log.error(f"No se pudo enviar mensaje Telegram: {e}")
            return False


# ══════════════════════════════════════════════════════════
#  BOT DE ALERTAS
# ══════════════════════════════════════════════════════════

class BotAlertas:
    """
    Escucha eventos MQTT y despacha alertas Telegram.
    Implementa cooldown por tipo+máquina para evitar spam.
    """

    def __init__(self):
        self.config      = cargar_config()
        self.alertas_cfg = self.config.get("alertas", {})
        self.bot         = TelegramBot(
            self.alertas_cfg.get("telegram_token", ""),
            self.alertas_cfg.get("telegram_chat_id", ""),
        )
        self._cooldowns: dict[str, float] = {}  # clave → timestamp último envío
        self._mqtt: mqtt.Client | None = None
        self._corriendo = True

        signal.signal(signal.SIGINT,  self._manejar_senal)
        signal.signal(signal.SIGTERM, self._manejar_senal)

    def _manejar_senal(self, sig, frame):
        log.info("Señal recibida, deteniendo bot de alertas...")
        self._corriendo = False
        if self._mqtt:
            self._mqtt.disconnect()

    # ── En cooldown? ─────────────────────────────────────
    def _en_cooldown(self, clave: str) -> bool:
        ultimo = self._cooldowns.get(clave, 0.0)
        return (time.time() - ultimo) < COOLDOWN_SEG

    def _marcar_cooldown(self, clave: str):
        self._cooldowns[clave] = time.time()

    # ── Procesar evento MQTT ──────────────────────────────
    def _procesar_evento(self, datos: dict) -> None:
        tipo       = datos.get("tipo", "")
        maquina_id = datos.get("maquina_id", "?")
        descripcion = datos.get("descripcion", "")

        clave = f"{tipo}_{maquina_id}"

        if tipo == "ROTURA_HILO" and self.alertas_cfg.get("alertar_rotura_hilo"):
            if not self._en_cooldown(clave):
                self.bot.enviar(
                    f"<b>⚠️ ROTURA DE HILO</b>\n"
                    f"Máquina: <b>M{maquina_id}</b>\n"
                    f"Detalle: {descripcion}"
                )
                self._marcar_cooldown(clave)

        elif tipo == "SOBRECARGA" and self.alertas_cfg.get("alertar_sobrecarga"):
            if not self._en_cooldown(clave):
                self.bot.enviar(
                    f"<b>🔴 SOBRECARGA DETECTADA</b>\n"
                    f"Máquina: <b>M{maquina_id}</b>\n"
                    f"Detalle: {descripcion}"
                )
                self._marcar_cooldown(clave)

        elif tipo == "CAMBIO_ESTADO":
            log.debug(f"Cambio de estado M{maquina_id}: {descripcion}")

    # ── Callbacks MQTT ───────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("Alertas-MQTT conectado al broker")
            client.subscribe(TOPIC_EVENTO)
            client.subscribe(TOPIC_HEARTBEAT)
            # Notificar inicio del sistema
            nombre = self.config["sistema"].get("nombre", "SENRG7")
            self.bot.enviar(f"<b>✅ {nombre}</b> — Bot de alertas iniciado")
        else:
            log.warning(f"Alertas-MQTT no pudo conectar, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            log.warning(f"Desconectado inesperadamente del broker (rc={rc}). Reconectando...")

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="ignore").strip()
        topic   = msg.topic

        if "/evento" in topic:
            try:
                datos = json.loads(payload)
                self._procesar_evento(datos)
            except json.JSONDecodeError as e:
                log.warning(f"Payload de evento inválido: {e} — payload: {payload[:80]}")

    # ── Bucle principal ──────────────────────────────────
    def iniciar(self) -> None:
        """Conecta al broker MQTT y entra al bucle de eventos."""
        cfg_red = self.config["red"]
        broker  = cfg_red["mqtt_broker"]
        puerto  = cfg_red["mqtt_puerto"]

        self._mqtt = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id="senrg7_alertas",
        )
        self._mqtt.on_connect    = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message    = self._on_message
        self._mqtt.reconnect_delay_set(min_delay=2, max_delay=60)

        usuario = cfg_red.get("mqtt_usuario", "")
        passwd  = cfg_red.get("mqtt_password", "")
        if usuario:
            self._mqtt.username_pw_set(usuario, passwd)

        log.info(f"Conectando al broker {broker}:{puerto}...")
        try:
            self._mqtt.connect(broker, puerto, keepalive=60)
        except Exception as e:
            log.error(f"No se pudo conectar al broker: {e}")
            sys.exit(1)

        log.info("Bot de alertas SENRG7 corriendo. Ctrl+C para detener.")
        self._mqtt.loop_forever()
        log.info("Bot de alertas detenido.")


# ── Punto de entrada ─────────────────────────────────────
if __name__ == "__main__":
    bot = BotAlertas()
    bot.iniciar()
