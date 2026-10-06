param([switch]$NoOpen)

$ErrorActionPreference = 'Stop'
$sessionerPythonWindowless = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $sessionerPythonWindowless -PathType Leaf)) {
    throw 'Sessioner needs installation. Run .\setup.ps1 from the Sessioner folder.'
}
$sessionerDesktopArguments = @('-m', 'sessioner.desktop')
if ($NoOpen) {
    $sessionerDesktopArguments += '--no-open'
}
Start-Process -FilePath $sessionerPythonWindowless -ArgumentList $sessionerDesktopArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
