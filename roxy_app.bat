@echo off
rem Roxy launcher: window with start button and settings (no console)
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" roxy_launcher.py
