@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Сначала запустите setup.bat.
    pause
    exit /b 1
)
if not exist "tools\cloudflared.exe" (
    ".venv\Scripts\python.exe" scripts\download_cloudflared.py
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
echo Запуск чата для Wi-Fi и доступа через интернет.
echo Если уже открыт другой режим, остановите его перед этим запуском.
".venv\Scripts\python.exe" main.py --internet %*
pause
