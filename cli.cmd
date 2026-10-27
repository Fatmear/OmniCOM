@echo off
rem OmniCOM CLI wrapper — keeps stdout pure JSON.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m omnicom.cli %*
exit /b %ERRORLEVEL%
