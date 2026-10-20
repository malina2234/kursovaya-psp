@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Не найдено Python-окружение.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\stop_server.py %*
pause
