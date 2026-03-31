@echo off
title SENRG7 - Bridge MQTT

cd /d "%~dp0.."

echo ============================================
echo   SENRG7 - Bridge MQTT (Modulo 1)
echo ============================================
echo.

if not exist modulo_1_bridge\venv (
    echo [ERROR] Entorno virtual no encontrado.
    echo Ejecutar primero: scripts\instalar_dependencias.bat
    pause
    exit /b 1
)

echo [INFO] Iniciando bridge MQTT...
echo [INFO] Presionar Ctrl+C para detener.
echo.

cd modulo_1_bridge
call venv\Scripts\activate.bat
python mqtt_bridge.py

if errorlevel 1 (
    echo.
    echo [ERROR] El bridge termino con errores. Ver mensajes arriba.
    pause
)
