@echo off
chcp 65001 >nul
cd /d "%~dp0"
call bin\start_gui.bat %*
exit /b %errorlevel%
