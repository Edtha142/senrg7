@echo off
title SENRG7 - API Web

cd /d "%~dp0.."

echo ============================================
echo   SENRG7 - API Web + Dashboard (Modulo 2)
echo ============================================
echo.

if not exist modulo_2_api\venv (
    echo [ERROR] Entorno virtual no encontrado.
    echo Ejecutar primero: scripts\instalar_dependencias.bat
    pause
    exit /b 1
)

echo [INFO] Iniciando servidor web...
echo [INFO] Dashboard disponible en: http://localhost:8000
echo [INFO] Presionar Ctrl+C para detener.
echo.

cd modulo_2_api
call venv\Scripts\activate.bat
python api_server.py

if errorlevel 1 (
    echo.
    echo [ERROR] La API termino con errores. Ver mensajes arriba.
    pause
)
