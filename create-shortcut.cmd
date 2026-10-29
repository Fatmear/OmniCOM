@echo off
rem One-click desktop shortcut for this OmniCOM folder: the shortcut is named
rem OmniCOM and carries the application icon (R-097).
rem 一键在桌面创建 OmniCOM 快捷方式：名称就是工具名，图标就是软件图标。
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python run.py shortcut %*
echo.
pause
endlocal
