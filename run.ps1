param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Pdf,
    [string]$OutputDir = "./mineru-output",
    [string]$MineruCommand = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}
if ([string]::IsNullOrWhiteSpace($MineruCommand)) {
    $venvMineruKit = Join-Path $root ".venv\Scripts\mineru-kit.exe"
    if (Test-Path $venvMineruKit) {
        $MineruCommand = $venvMineruKit
    } else {
        $MineruCommand = "mineru-kit"
    }
}
& $pythonExe (Join-Path $root "mineru_book.py") convert $Pdf -o $OutputDir --mineru-command $MineruCommand
