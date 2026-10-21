@echo off
chcp 65001 >nul
cd /d "%~dp0"
call bin\install.bat %*
exit /b %errorlevel%
