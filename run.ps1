param(
    [Parameter(ValueFromRemainingArguments = $true)]
    $ScriptArgs
)

$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
$scriptPath = Join-Path -Path $scriptDir -ChildPath 'FFmWiz.py'

if (-not (Test-Path -LiteralPath $scriptPath)) {
    Write-Host "Script file was not found: $scriptPath" -ForegroundColor Red
    exit 1
}

$forwarded = @()
if ($ScriptArgs) { $forwarded = @($ScriptArgs) }

$pythonExitCode = 9009
$pythonExe = $null
$pythonArgs = @()
function Test-FFmWizPython {
    param(
        [string]$Exe,
        [string[]]$Args
    )
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Exe @Args -c "import sys" > $null 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    if (Test-FFmWizPython -Exe 'py' -Args @('-3')) {
        $pythonExe = 'py'
        $pythonArgs = @('-3')
    }
}
if (-not $pythonExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Test-FFmWizPython -Exe 'python' -Args @()) {
        $pythonExe = 'python'
        $pythonArgs = @()
    }
}
if (-not $pythonExe -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
    if (Test-FFmWizPython -Exe 'python3' -Args @()) {
        $pythonExe = 'python3'
        $pythonArgs = @()
    }
}
if ($pythonExe) {
    & $pythonExe @pythonArgs $scriptPath @forwarded
    $pythonExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
} else {
    Write-Host 'Python was not found in PATH.' -ForegroundColor Red
}

exit $pythonExitCode
