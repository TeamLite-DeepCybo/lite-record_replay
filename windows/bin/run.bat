@echo off
rem run.bat —— 带控制台启动，能看到完整日志（排查 CAN 问题用）
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo 运行环境不存在，正在自动安装 ...
  call "windows\bin\install.bat" --no-pause
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" "app\main.py" %*
echo.
echo 程序已退出，退出码 %errorlevel%
pause
