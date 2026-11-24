@echo off
chcp 65001 >nul
rem Установка Рокси на этом компьютере из архива pack_roxy.py
cd /d "%~dp0"
set "PYVER=3.14"
if exist python_version.txt set /p PYVER=<python_version.txt
echo === Установка Рокси (Python %PYVER%) ===
py -%PYVER% --version >nul 2>&1
if errorlevel 1 (
  echo Python %PYVER% не найден — ставлю через winget...
  winget install -e --id Python.Python.%PYVER% --accept-source-agreements --accept-package-agreements
)
py -%PYVER% --version >nul 2>&1
if errorlevel 1 (
  echo Не получилось найти Python %PYVER%. Поставь его с python.org ^(галочка "Add to PATH"^) и запусти этот файл ещё раз.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Создаю окружение .venv ...
  py -%PYVER% -m venv .venv
)
".venv\Scripts\python.exe" install_helper.py
pause
