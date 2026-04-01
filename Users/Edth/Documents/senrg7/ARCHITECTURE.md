# ARCHITECTURE.md — Decisiones de Arquitectura SENRG7

> Documenta el POR QUÉ de cada decisión técnica.
> Antes de cambiar cualquier decisión, actualizar este archivo.

---

## Diagrama de flujo de datos

```
[Motor eléctrico de la bordadora]
        ↓ corriente AC
[SCT-013 — sensor no invasivo]
        ↓ señal analógica diferencial
[ADS1115 — ADC 16 bits, modo diferencial AIN0/AIN1]
        ↓ I2C (GPIO21/22)
[ESP32 — calcula RMS, determina estado]
        ↓ WiFi / MQTT cada 1 segundo
[Mosquitto — broker MQTT, puerto 1883]
        ↓ suscripción
[mqtt_bridge.py — detecta estados y eventos]
        ↓ escritura
[senrg7.db — SQLite]
        ↓ consulta
[api_server.py — FastAPI]
        ↓ WebSocket / REST
[Navegador — dashboard HTML/JS]
```

---

## Decisiones y justificaciones

### SQLite en lugar de PostgreSQL
PostgreSQL requiere instalación de servidor, configuración de usuario/contraseña
y un servicio de Windows adicional. En un entorno industrial o universitario,
esto puede estar bloqueado o requerir permisos de administrador.
SQLite es un archivo único (`senrg7.db`). No requiere instalación ni configuración.
Soporta sin problemas 4 máquinas a 1 lectura/segundo = ~345.600 filas/día.
Si en el futuro se necesita escalar, la migración a PostgreSQL es directa.

### Sin Docker
Docker Desktop en Windows requiere WSL2 o Hyper-V habilitado, que en muchas
PCs industriales o universitarias está desactivado por política de IT.
Python instalado directamente + pip install funciona en cualquier Windows sin
necesidad de permisos especiales ni configuración de virtualización.

### HTML + Vanilla JS en lugar de React/Vue
React requiere Node.js, npm, un paso de build (Vite/Webpack) y una carpeta
node_modules de ~300MB. Un error de TypeScript o de dependencias bloquea
completamente el frontend. En el proyecto anterior (loom-stitch-dash) esto
fue exactamente lo que causó semanas de problemas sin resolución.
HTML/JS puro se abre directamente en el navegador. Chart.js y demás librerías
se cargan por CDN. Cero build, cero npm, cero errores de compilación.
El resultado visual es indistinguible de React para un cliente o tribunal.

### FastAPI en lugar de Flask
Flask necesita flask-socketio + eventlet para WebSocket, combinación que tiene
bugs conocidos en Windows con Python 3.10+. FastAPI tiene WebSocket nativo y
async, funciona perfectamente en Windows, y genera documentación Swagger
automática útil para la defensa de tesis.

### MQTT en lugar de HTTP polling
HTTP polling cada 1 segundo = 86.400 requests/día por máquina.
MQTT mantiene una sola conexión TCP abierta y hace push solo cuando hay dato.
MQTT es protocolo estándar en IoT industrial — correcto para el contexto académico
y comercial del proyecto.

### ADS1115 en modo diferencial
El modo single-ended (AIN0 vs GND) es susceptible a ruido de modo común
en ambientes industriales con motores eléctricos. El modo diferencial
(AIN0 - AIN1) cancela el ruido de modo común y da lecturas más limpias.
Con ganancia ±2.048V y 16 bits: resolución de 62.5µV por bit — suficiente
para detectar variaciones de corriente pequeñas como roturas de hilo.

### Inno Setup en lugar de PyInstaller solo
PyInstaller genera un .exe del programa Python pero no instala dependencias
del sistema (Mosquitto, certificados, etc.). Inno Setup envuelve todo en un
wizard de instalación profesional con: pantalla de bienvenida, licencia,
directorio de instalación, accesos directos, y desinstalador.
El usuario final solo ve "siguiente, siguiente, instalar" — experiencia
de producto comercial.

### config.json editable desde el dashboard
Hardcodear valores obliga a recompilar el código para cambiar una IP.
Un instalable comercial no puede funcionar así — cada cliente tiene su
propia red, sus propias máquinas, sus propios umbrales.
config.json centraliza todo. El dashboard en /configuracion permite
cambiarlo sin tocar código ni archivos.

---

## Escalabilidad

El sistema está diseñado para 1 máquina pero escala a N sin cambios:
- Cada ESP32 tiene su MACHINE_ID único (1, 2, 3, 4...)
- Topics MQTT incluyen el ID: `senrg7/maquina/{id}/...`
- La tabla `maquinas` en SQLite tiene una fila por máquina
- El dashboard genera una tarjeta por cada máquina activa
- La lista de máquinas en config.json es un array dinámico

Para más de 10 máquinas o más de 1 lectura/segundo por máquina,
migrar SQLite → PostgreSQL (cambio solo en db.py).

---

## Lo que NO entra en este proyecto (fuera de alcance)

- Control de máquinas (solo monitoreo, no actuadores)
- Integración con ERP o sistemas de gestión
- Acceso remoto por internet (solo red local)
- App móvil (el dashboard es responsive, funciona en celular por navegador)
- Machine Learning predictivo (queda para versión 2.0)
