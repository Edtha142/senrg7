# SENRG7
## Sistema IoT de Monitoreo de Energía para Máquinas Industriales

**Proyecto de Grado — ITSa, Informática Industrial, Cochabamba, Bolivia**

---

## ¿Qué es?
SENRG7 monitorea el consumo de corriente eléctrica de máquinas bordadoras industriales
en tiempo real. Detecta estados operativos, roturas de hilo y genera alertas automáticas.
Se entrega como instalador `.exe` para Windows 10/11.

---

## Requisitos previos (solo la primera vez)
1. **Python 3.10+** — https://python.org/downloads (marcar "Add to PATH")
2. **Mosquitto for Windows** — https://mosquitto.org/download
3. **Arduino IDE** — para programar el ESP32 (si se modifica el firmware)

---

## Inicio rápido (desarrollo)
```
1. Iniciar Mosquitto (servicio Windows o: mosquitto -v en CMD)
2. Doble clic en scripts/instalar_dependencias.bat  ← solo la primera vez
3. Doble clic en scripts/iniciar_bridge.bat
4. Doble clic en scripts/iniciar_api.bat
5. Abrir navegador → http://localhost:8000
6. Login: admin / senrg7admin  ← cambiar en config.json
```

---

## Estructura del proyecto
```
senrg7/
├── config.json              ← toda la configuración aquí
├── modulo_1_bridge/         ← MQTT → base de datos
├── modulo_2_api/            ← API + dashboard web
├── modulo_3_esp32/          ← firmware del sensor
├── modulo_4_alertas/        ← alertas Telegram (semana 2)
├── installer/               ← genera SENRG7_Setup.exe
└── scripts/                 ← arranque en Windows
```

---

## Módulos
| Módulo | Descripción | Cómo correr |
|--------|-------------|-------------|
| Bridge | MQTT → SQLite | `scripts/iniciar_bridge.bat` |
| API    | Dashboard web | `scripts/iniciar_api.bat` |
| ESP32  | Firmware sensor | Arduino IDE → main.ino |
| Alertas| Telegram | Semana 2 |

---

## Configuración
Todo se configura en `config.json` o desde el dashboard en `http://localhost:8000/configuracion`.
No editar valores directamente en el código.

---

## Generar instalador .exe
```
installer/build.bat
```
Genera: `installer/output/SENRG7_Setup.exe`
Ver `skills/installer.md` para instrucciones detalladas.

---

## Documentación técnica
- `CLAUDE.md` — contexto completo del sistema
- `ARCHITECTURE.md` — decisiones de arquitectura y justificaciones
- `PROGRESS.md` — estado actual y tareas pendientes
- `skills/` — guías por tecnología
