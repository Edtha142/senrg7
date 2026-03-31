@echo off
chcp 65001 >nul
title SENRG7 — API Web

cd /d "%~dp0.."

echo ============================================
echo   SENRG7 - API Web + Dashboard (Modulo 2)
echo ============================================
echo.

REM ── Verificar que el venv existe ──
if not exist modulo_2_api\venv (
    echo [ERROR] Entorno virtual no encontrado.
    echo Ejecutar primero: scripts\instalar_dependencias.bat
    pause
    exit /b 1
)

REM ── Leer host y puerto del config.json con Python ──
for /f "delims=" %%i in ('python -c "import json; c=json.load(open(\"config.json\",encoding=\"utf-8\")); print(c[\"red\"][\"api_puerto\"])"') do set API_PORT=%%i
if "%API_PORT%"=="" set API_PORT=8000

echo [INFO] Iniciando servidor web en puerto %API_PORT%...
echo [INFO] Dashboard disponible en: http://localhost:%API_PORT%
echo [INFO] Presionar Ctrl+C para detener.
echo.

cd modulo_2_api
call venv\Scripts\activate.bat
python api_server.py

REM Si la API termina con error, mostrar pausa
if errorlevel 1 (
    echo.
    echo [ERROR] La API termino con errores. Ver mensajes arriba.
    pause
)
