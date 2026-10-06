param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SessionerArguments)
$sessionerCommand = Join-Path $PSScriptRoot '.venv\Scripts\sessioner.exe'
if (-not (Test-Path -LiteralPath $sessionerCommand -PathType Leaf)) {
    Write-Error 'Sessioner needs installation. Run .\setup.ps1 from the Sessioner folder.'
    exit 1
}
$sessionerPreviousCommand = $env:SESSIONER_COMMAND
$sessionerExitCode = 1
try {
    if ((Get-Location).ProviderPath -eq $PSScriptRoot) {
        $env:SESSIONER_COMMAND = '.\sessioner.ps1'
    } else {
        $sessionerQuotedLauncher = (Join-Path $PSScriptRoot 'sessioner.ps1').Replace("'", "''")
        $env:SESSIONER_COMMAND = "& '$sessionerQuotedLauncher'"
    }
    & $sessionerCommand @SessionerArguments
    $sessionerExitCode = $LASTEXITCODE
} finally {
    if ($null -eq $sessionerPreviousCommand) {
        Remove-Item -LiteralPath Env:\SESSIONER_COMMAND -ErrorAction SilentlyContinue
    } else {
        $env:SESSIONER_COMMAND = $sessionerPreviousCommand
    }
}
exit $sessionerExitCode
