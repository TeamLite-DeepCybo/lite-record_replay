@echo off
rem start_gui.bat —— 启动 Lite 动作录制/回放（Windows，无控制台窗口）
rem 需要看日志/报错时改用：bin\run.bat
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\pythonw.exe" (
  echo 首次运行，正在自动安装运行环境 ...
  call "windows\bin\install.bat" --no-pause
  if errorlevel 1 (
    echo.
    echo 自动安装失败，请运行 windows\1-安装.bat 查看详细原因。
    pause
    exit /b 1
  )
)

start "Lite 动作录制/回放" ".venv\Scripts\pythonw.exe" "app\main.py" %*
exit /b 0
