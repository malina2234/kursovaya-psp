@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Подготовка Python-окружения...
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Установите Python 3.12 с python.org, затем повторите запуск.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 (
    echo Не удалось установить библиотеки. Проверьте соединение с интернетом.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" scripts\download_model.py
if errorlevel 1 (
    pause
    exit /b 1
)
echo Готово. Запустите start.bat или main.py в Visual Studio Code.
pause
