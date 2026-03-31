@echo off
chcp 65001 >nul
title SENRG7 — Instalar Dependencias

cd /d "%~dp0.."
echo ============================================
echo   SENRG7 - Instalacion de dependencias
echo ============================================
echo.

REM ── Verificar que Python 3.10+ esté instalado ──
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no encontrado en PATH.
    echo Instalar desde: https://python.org/downloads
    echo Marcar la opcion "Add Python to PATH"
    pause
    exit /b 1
)
echo [OK] Python encontrado:
python --version

echo.
echo ── Modulo 1: Bridge MQTT ───────────────────
cd modulo_1_bridge

if exist venv (
    echo [INFO] Entorno virtual ya existe, actualizando...
) else (
    echo [INFO] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

echo [INFO] Instalando dependencias del bridge...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Error instalando dependencias del bridge.
    pause
    exit /b 1
)
call venv\Scripts\deactivate.bat
echo [OK] Bridge listo.

echo.
echo ── Modulo 2: API Web ───────────────────────
cd ..\modulo_2_api

if exist venv (
    echo [INFO] Entorno virtual ya existe, actualizando...
) else (
    echo [INFO] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

echo [INFO] Instalando dependencias de la API...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Error instalando dependencias de la API.
    pause
    exit /b 1
)
call venv\Scripts\deactivate.bat
echo [OK] API lista.

cd ..

echo.
echo ============================================
echo   Instalacion completada correctamente.
echo.
echo   Para iniciar el sistema:
echo     1. scripts\iniciar_bridge.bat
echo     2. scripts\iniciar_api.bat
echo     3. Navegador: http://localhost:8000
echo ============================================
echo.
pause
