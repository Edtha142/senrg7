@echo off
title SENRG7 - Alertas Telegram

cd /d "%~dp0.."

if not exist modulo_4_alertas\venv\Scripts\activate.bat (
    echo [ERROR] Venv no encontrado. Ejecutar instalar_dependencias.bat primero.
    pause
    exit /b 1
)

cd modulo_4_alertas
call venv\Scripts\activate.bat
python telegram_bot.py
pause
