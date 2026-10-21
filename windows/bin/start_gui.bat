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

rem pythonw 出错时不会留下任何窗口，看起来就是"闪一下就没了"。
rem 这里等几秒确认进程还在；不在了就把话说清楚，并指出能看报错的那个入口。
timeout /t 4 /nobreak >nul
tasklist /fi "imagename eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
if errorlevel 1 (
  echo.
  echo [启动失败] 界面没能起来，通常是依赖没装好或图形环境有问题。
  echo 用这个命令启动可以看到完整报错：
  echo     windows\bin\run.bat
  echo.
  echo 也可以先跑一次自检：
  echo     .venv\Scripts\python.exe windows\bin\selfcheck.py
  pause
  exit /b 1
)
exit /b 0
