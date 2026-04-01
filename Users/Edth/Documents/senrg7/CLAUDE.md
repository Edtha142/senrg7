# CLAUDE.md — SENRG7
## Sistema IoT de Monitoreo de Energía para Máquinas Industriales

---

## 👤 DESARROLLADOR
- **Nombre:** Edsson Nefy Loayza (Edth)
- **Instituto:** Instituto Tecnológico Sacaba (ITSa) — Informática Industrial, 5to semestre
- **Ciudad:** Cochabamba, Bolivia
- **Tutor:** Ing. Tito Mauricio Arispe
- **Objetivo académico:** Proyecto de grado — sistema IoT para monitoreo de máquinas bordadoras industriales
- **Objetivo comercial:** Producto vendible como instalador .exe para empresas industriales en Bolivia

---

## 🏷️ MARCA
- **Producto:** SENRG7
- **Empresa:** Por definir
- Significado del nombre: energía (NRG) + 7 (Iglesia Adventista del Séptimo Día)

---

## 🎯 QUÉ HACE EL SISTEMA
Monitorea el consumo de corriente eléctrica de máquinas bordadoras industriales en tiempo real.
Detecta automáticamente si cada máquina está apagada, encendida, bordando o en sobrecarga.
Detecta roturas de hilo por caída brusca de corriente y genera alertas.
Presenta todo en un dashboard web accesible desde cualquier PC de la red local.
Al final se entrega como instalador .exe (Inno Setup) para Windows 10/11.

---

## 🏗️ ARQUITECTURA

```
[Máquina Bordadora]
      |
[SCT-013] → [ADS1115 I2C modo diferencial] → [ESP32]
      |
   WiFi / MQTT (protocolo estándar IoT)
      |
[PC Windows 10/11 — Servidor]
   |
   ├── Mosquitto for Windows   (broker MQTT, puerto 1883)
   |       instalado como servicio de Windows
   |
   ├── modulo_1_bridge/
   |   └── mqtt_bridge.py      MQTT → SQLite + detección de estados y eventos
   |
   ├── modulo_2_api/
   |   ├── api_server.py       FastAPI + WebSocket + autenticación por sesión
   |   └── frontend/
   |       ├── login.html      página de login
   |       ├── index.html      dashboard monitoreo en tiempo real
   |       └── config.html     panel de configuración completo
   |
   └── senrg7.db               SQLite — un solo archivo, cero instalación extra
```

**Acceso al dashboard:** `http://IP-DEL-SERVIDOR:8000` desde cualquier PC en la red local.

---

## 📁 ESTRUCTURA COMPLETA DEL PROYECTO

```
senrg7/
├── CLAUDE.md                          ← contexto del agente (este archivo)
├── ARCHITECTURE.md                    ← decisiones técnicas
├── PROGRESS.md                        ← estado actual y tareas
├── README.md                          ← inicio rápido
│
├── .claude/
│   └── settings.json                  ← permisos Claude Code
│
├── skills/
│   ├── esp32.md                       ← patrones firmware Arduino
│   ├── python-api.md                  ← patrones backend Python
│   ├── sqlite-schema.md               ← esquema BD y queries
│   └── installer.md                   ← empaquetado .exe con Inno Setup
│
├── config.json                        ← TODA la configuración del sistema
│
├── modulo_1_bridge/
│   ├── mqtt_bridge.py                 ← suscribe MQTT, detecta estados, guarda en BD
│   ├── db.py                          ← esquema SQLite y helpers compartidos
│   └── requirements.txt
│
├── modulo_2_api/
│   ├── api_server.py                  ← FastAPI: REST + WebSocket + auth + sirve frontend
│   ├── requirements.txt
│   └── frontend/
│       ├── login.html
│       ├── index.html
│       └── config.html
│
├── modulo_3_esp32/
│   └── firmware/
│       └── main.ino                   ← Arduino IDE: ADS1115 + SCT-013 + MQTT
│
├── modulo_4_alertas/                  ← Semana 2 (no tocar hasta que core funcione)
│   └── telegram_bot.py
│
├── installer/
│   ├── senrg7.iss                     ← script Inno Setup
│   ├── build.bat                      ← genera el .exe instalador
│   └── assets/
│       ├── icon.ico
│       └── banner.bmp
│
└── scripts/
    ├── iniciar_bridge.bat             ← doble clic → arranca módulo 1
    ├── iniciar_api.bat                ← doble clic → arranca módulo 2
    └── instalar_dependencias.bat      ← primera vez: crea venvs e instala todo
```

---

## ⚙️ CONFIGURACIÓN (config.json)
**REGLA ABSOLUTA:** ningún valor configurable va hardcodeado en el código.
Todo se lee desde `config.json`. El usuario puede cambiarlo desde el dashboard en `/configuracion`.

Secciones del config.json:
- `sistema` — nombre, versión, descripción
- `auth` — usuario admin, password, duración de sesión
- `red` — MQTT broker/puerto/credenciales, API host/puerto
- `base_datos` — ruta del archivo .db
- `maquinas` — lista dinámica: id, nombre, descripción, activa, factor_calibracion
- `umbrales` — apagada/encendida/bordando/sobrecarga en Amperios
- `medicion` — intervalo_segundos, n_muestras_rms, historial_minutos
- `deteccion_eventos` — parámetros de rotura de hilo
- `alertas` — Telegram token/chat_id, toggles por tipo

---

## 🔌 HARDWARE

### Conexiones ADS1115
```
ADS1115 VCC  → ESP32 3.3V
ADS1115 GND  → ESP32 GND
ADS1115 SDA  → ESP32 GPIO21
ADS1115 SCL  → ESP32 GPIO22
ADS1115 ADDR → GND  (dirección I2C: 0x48)
SCT-013 +    → ADS1115 AIN0
SCT-013 -    → ADS1115 AIN1
Modo: diferencial (AIN0 - AIN1)
Ganancia: ±2.048V | Velocidad: 860 SPS
```

### Topics MQTT
```
senrg7/maquina/{id}/corriente   payload JSON: {maquina_id, corriente, estado, ts}
senrg7/maquina/{id}/evento      payload JSON: {maquina_id, tipo, descripcion, ts}
senrg7/sistema/heartbeat        string: estado del nodo cada 30s
```

### Estados de máquina
```
APAGADA     corriente < umbral_apagada      (default 0.5A)
ENCENDIDA   corriente < umbral_encendida    (default 2.0A)
BORDANDO    corriente < umbral_bordando     (default 20.0A)
SOBRECARGA  corriente >= umbral_sobrecarga  (default 20.0A)
```

---

## 🚀 ORDEN DE INICIO (siempre el mismo)
```
1. Mosquitto corriendo (servicio Windows — se instala una vez)
2. scripts/iniciar_bridge.bat   (doble clic)
3. scripts/iniciar_api.bat      (doble clic)
4. Encender ESP32
5. Navegador → http://localhost:8000
```

---

## 📦 PRODUCTO FINAL
El sistema se entrega como instalador Windows generado con Inno Setup.
El instalador incluye: Python embebido, todos los módulos, Mosquitto, dependencias.
El usuario final solo hace doble clic en `SENRG7_Setup.exe` y el sistema queda listo.
Ver `skills/installer.md` para el proceso de build.

---

## 📋 REGLAS PARA CLAUDE CODE

### AL ESCRIBIR CÓDIGO SIEMPRE:
1. Comentarios en español
2. Errores manejados explícitamente — nunca `except: pass`
3. Logging con timestamps (`logging` de Python, no `print`)
4. Todo valor configurable se lee desde `config.json`
5. Cada módulo puede correr standalone con `python archivo.py`
6. Al terminar una tarea, actualizar `PROGRESS.md`

### NUNCA:
- Docker o docker-compose
- React, Vue, Angular ni ningún framework con build step (npm, vite, webpack)
- PostgreSQL, asyncpg, psycopg2
- Credenciales o IPs hardcodeadas en el código
- Paquetes instalados globalmente — siempre dentro del venv del módulo
- Modificar `config.json` directamente desde el código — solo escribirlo cuando el usuario guarda desde el dashboard

### STACK FIJO (no proponer alternativas):
- Backend: Python 3.10+ + FastAPI + uvicorn
- Base de datos: SQLite (archivo senrg7.db)
- Broker MQTT: Mosquitto for Windows
- Cliente MQTT Python: paho-mqtt
- Frontend: HTML + Vanilla JS + CSS (Chart.js por CDN)
- Empaquetado: PyInstaller + Inno Setup

---

## 📊 ESTADO ACTUAL
Ver: `PROGRESS.md`

## 🏛️ DECISIONES DE ARQUITECTURA
Ver: `ARCHITECTURE.md`

## ⏱️ HISTORIAL
Proyecto rediseñado completamente desde cero.
Versión anterior (`loom-stitch-dash`) descartada — usaba Docker + React + Node.js,
generó problemas irresolubles con Mosquitto (BOM characters), CORS, puertos y build tools.
Nunca llegó a funcionar. No reutilizar ningún código de esa versión.
