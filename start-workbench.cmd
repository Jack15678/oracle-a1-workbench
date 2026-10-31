@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launcher.py gui
) else (
  python launcher.py gui
)
if errorlevel 1 pause
