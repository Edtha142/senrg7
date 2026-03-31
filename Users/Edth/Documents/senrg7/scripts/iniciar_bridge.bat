@echo off
chcp 65001 >nul
title SENRG7 — Bridge MQTT

cd /d "%~dp0.."

echo ============================================
echo   SENRG7 - Bridge MQTT (Modulo 1)
echo ============================================
echo.

REM ── Verificar que el venv existe ──
if not exist modulo_1_bridge\venv (
    echo [ERROR] Entorno virtual no encontrado.
    echo Ejecutar primero: scripts\instalar_dependencias.bat
    pause
    exit /b 1
)

REM ── Verificar que Mosquitto está corriendo ──
sc query mosquitto >nul 2>&1
if errorlevel 1 (
    echo [AVISO] El servicio Mosquitto no se detectó como servicio de Windows.
    echo Si Mosquitto corre manualmente, ignorar este aviso.
    echo.
)

echo [INFO] Iniciando bridge MQTT...
echo [INFO] Presionar Ctrl+C para detener.
echo.

cd modulo_1_bridge
call venv\Scripts\activate.bat
python mqtt_bridge.py

REM Si el bridge termina con error, mostrar pausa para leer el mensaje
if errorlevel 1 (
    echo.
    echo [ERROR] El bridge termino con errores. Ver mensajes arriba.
    pause
)
