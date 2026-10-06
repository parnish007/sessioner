param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AccountArguments)
$sessionerAccountCommand = Join-Path $PSScriptRoot '.venv\Scripts\sessioner-accounts.exe'
if (-not (Test-Path -LiteralPath $sessionerAccountCommand -PathType Leaf)) {
    Write-Error 'Run setup.ps1 first.'
    exit 1
}
& $sessionerAccountCommand @AccountArguments
exit $LASTEXITCODE
