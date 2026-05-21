$ErrorActionPreference = 'Stop'
$projectDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
$launcher = Join-Path -Path $projectDir -ChildPath 'run.ps1'
$commandDir = Join-Path -Path $projectDir -ChildPath 'Commands'
$requirements = Join-Path -Path $projectDir -ChildPath 'requirements.txt'

if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host "Launcher was not found: $launcher" -ForegroundColor Red
    exit 1
}

# Optional: install PySide6 so the new dedicated Cut Editor / Crop Preview
# GUIs can launch. Without PySide6, FFmWiz silently falls back to the
# legacy Tk preview windows so the CLI workflow keeps working.
$installPySide = $env:FFMWIZ_INSTALL_PYSIDE
if (-not $installPySide) {
    Write-Host ''
    $resp = Read-Host 'Install PySide6 now for the new GUI? [Y/n]'
    if ([string]::IsNullOrWhiteSpace($resp)) { $resp = 'Y' }
} else {
    $resp = $installPySide
}
if ($resp -match '^(?i:y|yes|1|true)$') {
    $py = $null
    if (Get-Command py -ErrorAction SilentlyContinue) { $py = @('py', '-3') }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = @('python') }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $py = @('python3') }
    if ($py) {
        $pythonExe = $py[0]
        $pythonArgs = @()
        if ($py.Length -gt 1) {
            $pythonArgs = $py[1..($py.Length - 1)]
        }
        Write-Host "Installing Python GUI dependencies via $($py -join ' ') -m pip ..." -ForegroundColor Cyan
        try {
            if (Test-Path -LiteralPath $requirements) {
                & $pythonExe @pythonArgs -m pip install --upgrade -r $requirements
            } else {
                & $pythonExe @pythonArgs -m pip install --upgrade PySide6==6.11.1
            }
        } catch {
            Write-Host "Python GUI dependency install failed: $_" -ForegroundColor Yellow
            Write-Host "FFmWiz will fall back to the legacy Tk GUI." -ForegroundColor DarkGray
        }
    } else {
        Write-Host 'Python was not found in PATH; skipping PySide6 install.' -ForegroundColor Yellow
    }
} else {
    Write-Host 'Skipping PySide6 install. FFmWiz will use the legacy Tk GUI.' -ForegroundColor DarkGray
}

New-Item -ItemType Directory -Path $commandDir -Force | Out-Null

$commandCmd = Join-Path -Path $commandDir -ChildPath 'FFmWiz.cmd'

$commandText = @'
@echo off
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"
where pwsh.exe >nul 2>nul
if %ERRORLEVEL%==0 (
    pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\run.ps1" %*
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\run.ps1" %*
)
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
'@
[System.IO.File]::WriteAllText($commandCmd, $commandText, [System.Text.UTF8Encoding]::new($false))

foreach ($oldShim in @('FFmWizard.cmd', 'FFmWizard.ps1', 'FFmwiz.cmd', 'FFmwiz.ps1')) {
    $oldPath = Join-Path -Path $commandDir -ChildPath $oldShim
    if (Test-Path -LiteralPath $oldPath) {
        Remove-Item -LiteralPath $oldPath -Force
    }
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathParts = @()
if ($userPath) {
    $pathParts = $userPath -split ';' | Where-Object { $_ }
}
$oldCommandDirs = @(
    ($commandDir -replace 'FFmWiz', 'FFmwiz'),
    ($commandDir -replace 'FFmWiz', 'FFmWizard')
)
$pathParts = $pathParts | Where-Object {
    $candidate = $_.TrimEnd('\')
    -not ($oldCommandDirs | Where-Object { [string]::Equals($candidate, $_.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) })
}
$alreadyInPath = $pathParts | Where-Object { [string]::Equals($_.TrimEnd('\'), $commandDir.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) }
if (-not $alreadyInPath) {
    $newUserPath = if ($pathParts) { ($pathParts + $commandDir) -join ';' } else { $commandDir }
    [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
    Write-Host "Added command directory to User PATH: $commandDir" -ForegroundColor Green
} else {
    $newUserPath = $pathParts -join ';'
    [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
    Write-Host "Command directory is already in User PATH: $commandDir" -ForegroundColor DarkGray
}

$documents = [Environment]::GetFolderPath('MyDocuments')
$profilePaths = @(
    (Join-Path $documents 'PowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path $documents 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1')
)

$begin = '# BEGIN FFmWiz command'
$end = '# END FFmWiz command'
$oldBlocks = @(
    @('# BEGIN FFmwiz command', '# END FFmwiz command'),
    @('# BEGIN FFmWizard command', '# END FFmWizard command')
)
$escapedLauncher = $launcher.Replace("'", "''")
$block = @"
$begin
function FFmWiz {
    `$launcher = '$escapedLauncher'
    `$pwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if (`$pwsh) {
        & `$pwsh.Source -NoLogo -NoProfile -ExecutionPolicy Bypass -File `$launcher @args
    } else {
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File `$launcher @args
    }
}
$end
"@

foreach ($profilePath in $profilePaths) {
    $profileDir = Split-Path -Parent $profilePath
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null

    $content = ''
    if (Test-Path -LiteralPath $profilePath) {
        $content = Get-Content -LiteralPath $profilePath -Raw
    }

    $pattern = [regex]::Escape($begin) + '(?s).*?' + [regex]::Escape($end)
    foreach ($oldBlock in $oldBlocks) {
        $oldPattern = [regex]::Escape($oldBlock[0]) + '(?s).*?' + [regex]::Escape($oldBlock[1])
        $content = [regex]::Replace($content, $oldPattern, '').TrimEnd()
    }
    if ($content -match $pattern) {
        $content = [regex]::Replace($content, $pattern, $block)
    } elseif ([string]::IsNullOrWhiteSpace($content)) {
        $content = $block + [Environment]::NewLine
    } else {
        $content = $content.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
    }

    Set-Content -LiteralPath $profilePath -Value $content -Encoding UTF8
    Write-Host "Updated PowerShell profile: $profilePath" -ForegroundColor Green
}

Write-Host ''
Write-Host 'Open a new terminal window, then run: FFmWiz' -ForegroundColor Cyan
