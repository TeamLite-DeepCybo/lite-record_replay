param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

# repository root = windows/bin/../..
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$appDir = Join-Path $projectRoot 'app'
$venvDir = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvPythonW = Join-Path $venvDir 'Scripts\pythonw.exe'

Set-Location $projectRoot

function Complete-Install([int]$ExitCode) {
    if (-not $NoPause) {
        Write-Host ''
        Read-Host 'Press Enter to close'
    }
    exit $ExitCode
}

try {
    Write-Host '========================================'
    Write-Host ' Lite Motion Record / Replay  v2.0.5-win'
    Write-Host ' Windows Installer'
    Write-Host '========================================'
    Write-Host ''

    # ---- [1/6] Python ----
    $python = & powershell -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot 'find_python.ps1') -InstallIfMissing | Select-Object -First 1
    if (-not $python -or -not (Test-Path -LiteralPath $python)) {
        throw 'Python 3.10 or newer was not found.'
    }
    $pyInfo = & $python -c "import platform,struct;print(platform.machine(), struct.calcsize(chr(80))*8)"
    Write-Host "[1/6] Python: $python  ($pyInfo)"

    # ---- [2/6] architecture ----
    if ($pyInfo -match ' 32$') {
        throw 'A 32-bit (x86) Python was found. numpy has no 32-bit wheels any more; please install 64-bit Python (x64 or arm64).'
    }
    Write-Host '[2/6] Architecture check passed (64-bit).'

    # ---- [3/6] virtual environment ----
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host '[3/6] Creating the private Python environment (.venv) ...'
        & $python -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python environment.' }
    } else {
        Write-Host '[3/6] Python environment already exists (.venv).'
    }

    # ---- [4/6] dependencies ----
    Write-Host '[4/6] Installing required packages (first run: 1-3 min) ...'
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip | Out-Null
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-win.txt') `
        -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host '      mirror failed, retrying with the default index ...'
        & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-win.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install required packages.' }
    }

    # ---- [5/6] self check ----
    Write-Host '[5/6] Running the built-in check ...'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONPATH = $appDir
    & $venvPython (Join-Path $PSScriptRoot 'selfcheck.py')
    if ($LASTEXITCODE -ne 0) { throw 'The built-in check did not pass.' }

    # ---- [6/6] desktop shortcut ----
    Write-Host '[6/6] Creating the desktop shortcut ...'
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnkPath = Join-Path $desktop 'Lite Record Replay.lnk'
        $ws = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut($lnkPath)
        $lnk.TargetPath = $venvPythonW
        $lnk.Arguments = '"' + (Join-Path $appDir 'main.py') + '"'
        $lnk.WorkingDirectory = $appDir
        $lnk.IconLocation = (Join-Path $appDir 'assets\logo-emblem.ico')
        $lnk.Description = 'Lite Motion Record / Replay v2.0.5-win'
        $lnk.Save()
        Write-Host "      desktop shortcut: $lnkPath"
    } catch {
        Write-Host "      shortcut skipped: $($_.Exception.Message)"
    }

    Write-Host ''
    Write-Host '========================================'
    Write-Host ' Installation completed.'
    Write-Host ''
    Write-Host ' Double-click the desktop shortcut "Lite Record Replay",'
    Write-Host ' or double-click the BAT file in the windows folder'
    Write-Host ' whose name begins with "2-".'
    Write-Host '========================================'
    Complete-Install 0
} catch {
    Write-Host ''
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    Complete-Install 1
}
