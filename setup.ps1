param([switch]$DesktopShortcut, [switch]$NoDesktopShortcut)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    Write-Host 'Installing Sessioner...'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and run setup.ps1 again.'
    }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        if (Test-Path -LiteralPath '.venv') {
            # An interrupted setup leaves .venv with only uv's marker files. Remove exactly that;
            # anything else in the folder is left for the person to look at.
            $sessionerLeftover = @(Get-ChildItem -LiteralPath '.venv' -Recurse -Force -File |
                Where-Object { $_.Name -notin @('CACHEDIR.TAG', 'pyvenv.cfg', '.gitignore', '.lock') })
            if ($sessionerLeftover.Count -gt 0) {
                throw 'The .venv folder is incomplete but contains files. Delete the .venv folder, then run setup.ps1 again.'
            }
            Remove-Item -LiteralPath '.venv' -Recurse -Force
        }
        & uv venv --python 3.13 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment setup failed.' }
    }
    & uv pip install --python '.venv\Scripts\python.exe' --link-mode copy --quiet -e '.'
    if ($LASTEXITCODE -ne 0) { throw 'Sessioner installation failed. Check the message above and run setup.ps1 again.' }
    Write-Host ''
    Write-Host 'Sessioner is installed.'
    if (-not $NoDesktopShortcut) {
        $sessionerPythonWindowless = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
        if (-not (Test-Path -LiteralPath $sessionerPythonWindowless -PathType Leaf)) {
            throw 'The Python environment is missing pythonw.exe. Recreate .venv and run setup.ps1 again.'
        }
        $sessionerDesktopDirectory = [Environment]::GetFolderPath('Desktop')
        if (-not [string]::IsNullOrWhiteSpace($sessionerDesktopDirectory)) {
            $sessionerShortcutPath = Join-Path $sessionerDesktopDirectory 'Sessioner.lnk'
            $sessionerShortcutShell = New-Object -ComObject WScript.Shell
            try {
                $sessionerShortcut = $sessionerShortcutShell.CreateShortcut($sessionerShortcutPath)
                $sessionerShortcut.TargetPath = $sessionerPythonWindowless
                $sessionerShortcut.Arguments = '-m sessioner.desktop'
                $sessionerShortcut.WorkingDirectory = $PSScriptRoot
                $sessionerShortcut.WindowStyle = 7
                $sessionerShortcut.Description = 'Open Sessioner dashboard and Windows tray'
                $sessionerShortcut.Save()
            } finally {
                if ($null -ne $sessionerShortcut) {
                    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sessionerShortcut)
                }
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sessionerShortcutShell)
            }
            Write-Host 'Open Sessioner from the desktop shortcut. The enabled watcher runs with the tray.'
        }
    }
    Write-Host 'Open dashboard and tray: .\desktop.ps1'
    Write-Host 'Get started: .\sessioner.ps1 setup'
    Write-Host 'Check status: .\sessioner.ps1 status'
} finally {
    Pop-Location
}
