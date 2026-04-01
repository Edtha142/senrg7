# PROGRESS.md — SENRG7

> **Claude Code debe leer este archivo al iniciar cada sesión.**
> Actualizar la sección "ÚLTIMA SESIÓN" al terminar cada tarea.

---

## 📅 ÚLTIMA SESIÓN
- **Fecha:** 2026-04-01
- **Estado:** Sistema funcionando con ESP32 conectado y leyendo corriente. Semana 2 implementada.
- **Próximo paso:** Configurar Telegram bot (token + chat_id), calibrar FACTOR_CAL, preparar instalador

---

## ✅ COMPLETADO

### Archivos de contexto
- [x] CLAUDE.md — contexto completo del agente
- [x] ARCHITECTURE.md — decisiones técnicas documentadas
- [x] PROGRESS.md — este archivo
- [x] README.md — inicio rápido

### Día 1 — Base de datos y configuración
- [x] `config.json` — toda la configuración centralizada (MQTT, BD, máquinas, umbrales, alertas)
- [x] `modulo_1_bridge/db.py` — esquema SQLite completo + helpers + standalone
- [x] `modulo_1_bridge/requirements.txt` — paho-mqtt 2.1.0

### Día 2 — Bridge MQTT
- [x] `modulo_1_bridge/mqtt_bridge.py`
  - Clase `MqttBridge` con callbacks paho-mqtt
  - Máquina de estados (APAGADA / ENCENDIDA / BORDANDO / SOBRECARGA)
  - Detección de rotura de hilo (caída brusca dentro de ventana deslizante)
  - Registro automático de eventos: CAMBIO_ESTADO, ROTURA_HILO, SOBRECARGA, INICIO, PAUSA
  - Reconexión automática con backoff
  - Manejo limpio de señales SIGINT/SIGTERM

### Día 3 — Firmware ESP32
- [x] `modulo_3_esp32/firmware/main.ino`
  - ADS1115 modo diferencial (AIN0 - AIN1), ganancia ±2.048V, 860 SPS
  - Cálculo RMS con 100 muestras + filtro de ruido
  - Publicación MQTT JSON cada 1 segundo
  - Reconexión automática WiFi y MQTT con watchdog
  - Heartbeat cada 30 segundos
  - Publicación de eventos de cambio de estado

### Día 4 — API y WebSocket
- [x] `modulo_2_api/api_server.py`
  - FastAPI con lifespan (startup/shutdown limpio)
  - Autenticación por sesión (cookie httponly, token en SQLite)
  - `GET  /api/maquinas` — estado actual con última lectura y roturas del día
  - `GET  /api/maquinas/{id}/lecturas` — historial de corriente (parámetro minutos)
  - `GET  /api/resumen` — resumen productivo del día (eficiencia %, tiempo por estado)
  - `GET  /api/eventos` — log de eventos recientes
  - `GET/PUT /api/config` — leer y guardar configuración
  - `WS /ws` — stream en tiempo real autenticado (reenvía MQTT al navegador)
  - `GET /health` — health check público
- [x] `modulo_2_api/requirements.txt`

### Día 5 — Frontend
- [x] `modulo_2_api/frontend/login.html` — diseño industrial oscuro, manejo de error JS
- [x] `modulo_2_api/frontend/index.html`
  - Dashboard en tiempo real con tarjetas por máquina
  - Gráfico de corriente deslizante (Chart.js CDN, 60 puntos)
  - Colores por estado (gris/ámbar/verde/rojo)
  - Log de eventos en vivo con íconos Bootstrap Icons
  - Barras de resumen productivo del día
  - WebSocket con reconexión automática (backoff exponencial)
- [x] `modulo_2_api/frontend/config.html`
  - Panel de configuración completo (6 secciones colapsables)
  - Máquinas dinámicas (agregar / eliminar filas)
  - Toggle switches para alertas
  - Guardado via PUT /api/config con validación y toast de confirmación

### Día 7 — Scripts Windows
- [x] `scripts/instalar_dependencias.bat` — crea venvs e instala todo
- [x] `scripts/iniciar_bridge.bat` — arranca módulo 1
- [x] `scripts/iniciar_api.bat` — arranca módulo 2

### Semana 2 — Alertas y Reportes
- [x] `modulo_4_alertas/telegram_bot.py` — bot standalone MQTT→Telegram con cooldown anti-spam
- [x] `modulo_4_alertas/requirements.txt`
- [x] `scripts/iniciar_alertas.bat`
- [x] `GET /api/reporte?fecha=YYYY-MM-DD&formato=json|csv` — reporte productivo diario con descarga CSV
- [x] `modulo_2_api/frontend/reporte.html` — página de reporte con tabla y descarga CSV
- [x] Dashboard: stats rápidas en navbar (activas, bordando, roturas)
- [x] Tema claro/oscuro aplicado correctamente en todas las páginas

---

## 🔄 EN PROGRESO
_Semana 1 completa. Lista para prueba integrada._

---

## 📋 PENDIENTE — PRUEBA INTEGRADA (antes de Semana 2)

### Validación del sistema core
- [ ] Ejecutar `scripts/instalar_dependencias.bat` en una PC limpia
- [ ] Verificar que `python db.py` crea `senrg7.db` correctamente
- [ ] Prueba MQTT manual: `mosquitto_pub -t "senrg7/maquina/1/corriente" -m '{"maquina_id":1,"corriente":12.5,"estado":"BORDANDO","ts":0}'`
- [ ] Verificar que el bridge detecta y registra el dato en la BD
- [ ] Abrir dashboard en el navegador y ver la tarjeta actualizar en tiempo real
- [ ] Calibrar FACTOR_CAL con pinza amperimétrica en máquina real
- [ ] Test de 1 hora continua sin errores
- [ ] Simular rotura de hilo y verificar alerta en el log de eventos

---

## 📋 PENDIENTE — SEMANA 2 (producto terminado)

### Alertas Telegram
- [x] Crear `modulo_4_alertas/telegram_bot.py`
- [ ] Integrar con mqtt_bridge.py
- [x] Configuración desde dashboard (token, chat_id, toggles)
- [ ] Test: rotura de hilo genera mensaje en Telegram

### Reporte de producción
- [x] Endpoint `GET /api/reporte?fecha=YYYY-MM-DD`
- [x] Genera resumen: tiempo productivo, roturas, eficiencia por máquina
- [x] Exportable como CSV desde el dashboard

### Empaquetado instalable
- [ ] Crear `installer/senrg7.iss` — script Inno Setup
- [ ] Crear `installer/build.bat` — genera SENRG7_Setup.exe
- [ ] Incluir Python embebido, módulos, Mosquitto
- [ ] Test en PC limpia (sin Python instalado)

---

## 🐛 BUGS CONOCIDOS
- Firmware: WiFi SSID en main.ino dice "FLIALOAYZAI" pero red real es "FLIALOAYZA" — verificar al calibrar
- Tema: si el sistema operativo cambia de preferencia de color, recargar página para que se aplique

---

## 📝 NOTAS PARA CLAUDE CODE

### Sobre Mosquitto en Windows
El problema histórico con Mosquitto fue el BOM (Byte Order Mark) en mosquitto.conf.
El archivo de configuración DEBE guardarse en UTF-8 sin BOM.
En Python: `open(ruta, 'w', encoding='utf-8')` — nunca `utf-8-sig`.
Comando de verificación: `mosquitto -v` en CMD debe iniciar sin errores.

### Sobre el factor de calibración
`FACTOR_CAL` en main.ino convierte voltaje diferencial a Amperios.
Valor inicial: 30.0 (para SCT-013-030 con ganancia ±2.048V).
Calibración: FACTOR_CAL_nuevo = FACTOR_CAL_actual × (A_real / A_medido).
Donde A_real se mide con pinza amperimétrica en la máquina encendida.
El valor calibrado se guarda en config.json como `factor_calibracion` por máquina.

### Sobre paho-mqtt 2.x
Se usa la versión 2.1.0 con `CallbackAPIVersion.VERSION1` para mantener
las firmas de callback compatibles con la documentación estándar.
Inicialización: `mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1, client_id="...")`

### Sobre el proyecto anterior
El proyecto anterior se llamaba loom-stitch-dash y usaba Docker + React + Node.js.
Nunca funcionó completamente. No existe código reutilizable de esa versión.
