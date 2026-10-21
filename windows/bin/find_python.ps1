param([switch]$InstallIfMissing)

$ErrorActionPreference = "Stop"

# Lite Record/Replay needs Python 3.10+ (robstride-dynamics itself requires it).
$MIN_VERSION = 10   # minor version of 3.x

function Test-CompatiblePython([string]$exe) {
    if (-not $exe) { return $false }
    try {
        & $exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $MIN_VERSION) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-CompatiblePython {
    # 1) py launcher / python on PATH
    $commands = @(
        { py -3 -c "import sys; print(sys.executable)" 2>$null },
        { python -c "import sys; print(sys.executable)" 2>$null }
    )
    foreach ($command in $commands) {
        try {
            $path = (& $command | Select-Object -First 1)
            if ($path) { $path = $path.Trim() }
            if ($path -and (Test-Path -LiteralPath $path) -and (Test-CompatiblePython $path)) {
                return $path
            }
        } catch { }
    }

    # 2) common install locations
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        "C:\Program Files\Python",
        "C:\Python312", "C:\Python311", "C:\Python310"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $candidates = Get-ChildItem -LiteralPath $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        foreach ($candidate in $candidates) {
            if (Test-CompatiblePython $candidate.FullName) { return $candidate.FullName }
        }
    }
    return $null
}

$python = Find-CompatiblePython

if (-not $python -and $InstallIfMissing) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        [Console]::Error.WriteLine("Python 3.10+ not found, and winget is unavailable.")
        [Console]::Error.WriteLine("Please install Python 3.12 from https://www.python.org/downloads/windows/")
        exit 1
    }
    [Console]::Error.WriteLine("Python 3.10+ not found. Installing Python 3.12 with winget ...")
    & $winget.Source install --id Python.Python.3.12 --exact --scope user `
        --accept-package-agreements --accept-source-agreements --silent | Out-Null
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("winget failed to install Python (exit code $LASTEXITCODE).")
        exit 1
    }
    $python = Find-CompatiblePython
}

if (-not $python) {
    [Console]::Error.WriteLine("Python 3.10 or newer is required.")
    exit 1
}

Write-Output $python
