@echo off
setlocal
cd /d "%~dp0..\.."
set "NO_PAUSE="
if /i "%~1"=="--no-pause" set "NO_PAUSE=-NoPause"
powershell -NoProfile -ExecutionPolicy Bypass -File "windows\bin\install_windows.ps1" %NO_PAUSE%
exit /b %errorlevel%
