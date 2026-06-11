param(
    [Parameter(ValueFromRemainingArguments = $true)]
    $ScriptArgs
)

# Canonical PowerShell launcher for FFmWiz.
# - Resolves project root and FFmWiz.py relative to this script's location, so
#   the launcher works regardless of the caller's current working directory.
# - Prefers the Windows Python launcher (py -3), then python, then python3.
# - Forwards every argument unchanged to FFmWiz.py.
# - Returns the same exit code as the Python process.
# - Does not require admin rights and does not hard-code user-specific paths.

$ErrorActionPreference = 'Stop'

# Resolve project root relative to this launcher.
$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} elseif ($PSCommandPath) {
    Split-Path -Parent $PSCommandPath
} else {
    $null
}

if (-not $scriptDir -or -not (Test-Path -LiteralPath $scriptDir)) {
    Write-Host "run.ps1 could not determine its own directory. Re-run it as a file (not piped into PowerShell)." -ForegroundColor Red
    exit 1
}

$projectRoot = (Resolve-Path -LiteralPath $scriptDir).Path
$scriptPath = Join-Path -Path $projectRoot -ChildPath 'FFmWiz.py'

if (-not (Test-Path -LiteralPath $scriptPath)) {
    Write-Host "FFmWiz.py was not found next to run.ps1. Expected at: $scriptPath" -ForegroundColor Red
    Write-Host "Make sure run.ps1 sits in the FFmWiz repository root alongside FFmWiz.py." -ForegroundColor Red
    exit 1
}

# Forward every CLI argument unchanged. ValueFromRemainingArguments preserves
# user-supplied flags including paths with spaces.
$forwarded = @()
if ($ScriptArgs) { $forwarded = @($ScriptArgs) }

function Test-FFmWizPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,
        [string[]]$Args = @()
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

# Probe interpreters in priority order: py -3, python, python3.
$pythonExe = $null
$pythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    if (Test-FFmWizPython -Exe 'py' -Args @('-3')) {
        $pythonExe = 'py'
        $pythonArgs = @('-3')
    }
}
if (-not $pythonExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Test-FFmWizPython -Exe 'python') {
        $pythonExe = 'python'
        $pythonArgs = @()
    }
}
if (-not $pythonExe -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
    if (Test-FFmWizPython -Exe 'python3') {
        $pythonExe = 'python3'
        $pythonArgs = @()
    }
}

if (-not $pythonExe) {
    Write-Host "Python was not found in PATH. Install Python 3.10+ and reopen the terminal." -ForegroundColor Red
    Write-Host "Tried: py -3, python, python3." -ForegroundColor Red
    exit 9009
}

# Run FFmWiz.py and propagate its exit code unchanged.
& $pythonExe @pythonArgs $scriptPath @forwarded
$pythonExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
exit $pythonExitCode
