@echo off
title SENRG7 - Instalar Dependencias

cd /d "%~dp0.."

echo ============================================
echo   SENRG7 - Instalacion de dependencias
echo ============================================
echo.

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
echo --- Modulo 1: Bridge MQTT ---
cd modulo_1_bridge

if not exist venv (
    echo [INFO] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo [INFO] Entorno virtual ya existe, actualizando...
)

echo [INFO] Instalando dependencias del bridge...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Error instalando dependencias del bridge.
    call venv\Scripts\deactivate.bat
    pause
    exit /b 1
)
call venv\Scripts\deactivate.bat
echo [OK] Bridge listo.

echo.
echo --- Modulo 2: API Web ---
cd ..\modulo_2_api

if not exist venv (
    echo [INFO] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo [INFO] Entorno virtual ya existe, actualizando...
)

echo [INFO] Instalando dependencias de la API...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Error instalando dependencias de la API.
    call venv\Scripts\deactivate.bat
    pause
    exit /b 1
)
call venv\Scripts\deactivate.bat
echo [OK] API lista.

echo.
echo --- Modulo 4: Alertas Telegram ---
cd ..\modulo_4_alertas

if not exist venv (
    echo [INFO] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo [INFO] Entorno virtual ya existe, actualizando...
)

echo [INFO] Instalando dependencias de alertas...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Error instalando dependencias de alertas.
    call venv\Scripts\deactivate.bat
    pause
    exit /b 1
)
call venv\Scripts\deactivate.bat
echo [OK] Alertas listo.

cd ..

echo.
echo ============================================
echo   Instalacion completada correctamente.
echo.
echo   Para iniciar el sistema:
echo     1. scripts\iniciar_bridge.bat
echo     2. scripts\iniciar_api.bat
echo     3. scripts\iniciar_alertas.bat  (opcional)
echo     4. Navegador: http://localhost:8000
echo ============================================
echo.
pause
