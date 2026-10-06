$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    Write-Host 'Installing Sessioner...'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and run setup.ps1 again.'
    }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & uv venv --python 3.13 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment setup failed.' }
    }
    & uv pip install --python '.venv\Scripts\python.exe' --link-mode copy --quiet -e '.'
    if ($LASTEXITCODE -ne 0) { throw 'Sessioner installation failed. Check the message above and run setup.ps1 again.' }
    Write-Host ''
    Write-Host 'Sessioner is installed.'
    Write-Host 'Get started: .\sessioner.ps1 setup'
    Write-Host 'Check status: .\sessioner.ps1 status'
} finally {
    Pop-Location
}
