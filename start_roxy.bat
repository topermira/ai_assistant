@echo off
rem Zapusk Roxy odnim klikom (iz papki proekta). Okno konsoli svernuto, logi - v logs\roxy.log
cd /d "%~dp0"
start "Roxy" /min ".venv\Scripts\python.exe" main.py
