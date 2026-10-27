@echo off
rem Launcher with a console window - use this if the GUI does not start,
rem so Python/Qt errors stay visible.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python run.py %*
echo.
echo [OmniCOM] exited with code %ERRORLEVEL%
pause
endlocal
