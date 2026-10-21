@echo off
rem 打成单文件 exe（在目标架构的 Windows 上运行；x64 / arm64 各打一次）
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo 运行环境不存在，正在自动安装 ...
  call "windows\bin\install.bat" --no-pause
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

rem 注意：--specpath 会让 .spec 里的相对路径按 spec 所在目录解析，
rem 所以输入路径一律用绝对路径，否则会报 "Unable to find app\assets"。
set "ROOT=%cd%"

rem python-can 的后端是**按名字动态导入**的（importlib.import_module），
rem PyInstaller 静态分析看不到，必须显式 hidden-import，否则运行时
rem 报 "Cannot import module can.interfaces.gs_usb"，CAN 适配器全都打不开。
rem 注意 candle 是**独立插件包**（python-can-candle），入口是 candle.candle_bus，
rem 还要把它的 entry point 元数据带上，否则 python-can 认不出 "candle" 这个名字。
set "CAN_BACKENDS=gs_usb pcan canalystii slcan serial kvaser vector systec ixxat usb2can cantact socketcan socketcand udp_multicast virtual"
set "CAN_HIDDEN="
for %%B in (%CAN_BACKENDS%) do set "CAN_HIDDEN=!CAN_HIDDEN! --hidden-import can.interfaces.%%B"

echo 安装 PyInstaller ...
".venv\Scripts\python.exe" -m pip install -q pyinstaller
echo 开始打包 ...
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm --clean --windowed --onefile ^
  --name "LiteRecordReplay" ^
  --icon "%ROOT%\app\assets\logo-emblem.ico" ^
  --add-data "%ROOT%\app\assets;assets" ^
  --add-data "%ROOT%\app\calibration.json;." ^
  --paths "%ROOT%\app" ^
  --manifest "%ROOT%\windows\bin\app.manifest" ^
  --hidden-import "usb.backend.libusb1" ^
  --hidden-import "candle.candle_bus" ^
  --hidden-import "can_scan" ^
  !CAN_HIDDEN! ^
  --collect-all candle ^
  --collect-all candle_api ^
  --copy-metadata python-can-candle ^
  --collect-submodules gs_usb ^
  --collect-all libusb_package ^
  --distpath "%ROOT%\dist" --workpath "%ROOT%\build" --specpath "%ROOT%\build" ^
  "%ROOT%\app\main.py"
if errorlevel 1 (
  echo.
  echo 打包失败，请查看上方错误。
  pause
  exit /b 1
)
echo.
echo 完成：dist\LiteRecordReplay.exe
echo 自检（会打印/写出 CAN 诊断报告）：
echo   dist\LiteRecordReplay.exe --diagnose
echo 注意 exe 与架构相关，x64 与 arm64 需要各自打一次。
pause
