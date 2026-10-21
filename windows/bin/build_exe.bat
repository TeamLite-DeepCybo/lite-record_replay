@echo off
rem 打成单文件 exe（在目标架构的 Windows 上运行；x64 / arm64 各打一次）
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo 运行环境不存在，正在自动安装 ...
  call "windows\bin\install.bat" --no-pause
  if errorlevel 1 pause & exit /b 1
)

echo 安装 PyInstaller ...
".venv\Scripts\python.exe" -m pip install -q pyinstaller
echo 开始打包 ...
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm --clean --windowed --onefile ^
  --name "LiteRecordReplay" ^
  --icon "app\assets\logo-emblem.ico" ^
  --add-data "app\assets;assets" ^
  --add-data "app\calibration.json;." ^
  --paths "app" ^
  --distpath "dist" --workpath "build" --specpath "build" ^
  "app\main.py"
if errorlevel 1 (
  echo.
  echo 打包失败，请查看上方错误。
  pause
  exit /b 1
)
echo.
echo 完成：dist\LiteRecordReplay.exe
echo 注意 exe 与架构相关，x64 与 arm64 需要各自打一次。
pause
