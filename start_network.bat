@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Сначала запустите setup.bat.
    pause
    exit /b 1
)
echo Запуск чат-сервера для компьютеров в одной локальной сети.
echo На втором компьютере откройте http://IP_ЭТОГО_КОМПЬЮТЕРА:8000
echo IP можно посмотреть командой ipconfig, строка IPv4 у Wi-Fi или Ethernet.
echo Это окно должно оставаться открытым. Остановка сервера: Ctrl+C.
".venv\Scripts\python.exe" main.py --host 0.0.0.0 --port 8000
pause
