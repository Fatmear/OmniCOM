@echo off
rem Double-click launcher for OmniCOM (no console window).
rem Company policy blocks self-built .exe files, so the app runs from source.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

where pythonw >nul 2>nul
if errorlevel 1 (
  echo [OmniCOM] pythonw not found, falling back to python
  python run.py %*
) else (
  start "" pythonw run.py %*
)
endlocal
