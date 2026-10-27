param(
    [string]$Python = "",
    [switch]$SkipMineru
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"
$codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

function Resolve-PythonCommand {
    if (-not [string]::IsNullOrWhiteSpace($Python)) {
        return @{ Command = $Python; Arguments = @() }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Command = "python"; Arguments = @() }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Command = "py"; Arguments = @("-3") }
    }
    if (Test-Path $codexPython) {
        return @{ Command = $codexPython; Arguments = @() }
    }
    throw "Python 3 was not found. Install Python, then run setup.ps1 again."
}

if (-not (Test-Path $pythonExe)) {
    if (Test-Path $venv) {
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
    $resolvedPython = Resolve-PythonCommand
    Write-Host "Creating virtual environment with $($resolvedPython.Command)"
    & $resolvedPython.Command @($resolvedPython.Arguments) -m venv $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pythonExe)) {
        throw "Failed to create the virtual environment with $($resolvedPython.Command)."
    }
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $root "requirements.txt")
if (-not $SkipMineru) {
    Write-Host "Installing MinerU 4.x base package. The first parse may download models."
    & $pythonExe -m pip install --upgrade "mineru>=4.0,<5"
}

Write-Host "Ready. Try:"
Write-Host "  .\run.ps1 -Pdf .\book.pdf -OutputDir .\book-output"
