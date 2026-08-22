[CmdletBinding()]
param(
    [string]$PythonPath = "python",
    [switch]$KeepWorkspace
)

$ErrorActionPreference = "Stop"
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$phaseRoot = Join-Path $tempRoot ("jarvis-clean-" + [guid]::NewGuid().ToString("N"))
$repoPath = Join-Path $phaseRoot "repo"
$venvPath = Join-Path $phaseRoot "venv"
$cleanPython = Join-Path $venvPath "Scripts\python.exe"

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Command,
        [Parameter(Mandatory)]
        [string]$Description
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

try {
    New-Item -ItemType Directory -Path $phaseRoot | Out-Null
    $commit = (& git -C $sourceRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $commit) {
        throw "Unable to resolve the source repository HEAD."
    }

    Invoke-Checked -Description "Repository clone" -Command {
        git clone --quiet --no-hardlinks --no-checkout $sourceRoot $repoPath
    }
    Invoke-Checked -Description "HEAD checkout" -Command {
        git -C $repoPath checkout --quiet --detach $commit
    }
    Invoke-Checked -Description "Virtual environment creation" -Command {
        & $PythonPath -m venv $venvPath
    }

    Push-Location -LiteralPath $repoPath
    try {
        Invoke-Checked -Description "Dependency installation" -Command {
            & $cleanPython -m pip install --disable-pip-version-check -e ".[dev,voice]"
        }
        Invoke-Checked -Description "Dependency consistency check" -Command {
            & $cleanPython -m pip check
        }
        Invoke-Checked -Description "Full test suite" -Command {
            & $cleanPython -m pytest -q
        }
    }
    finally {
        Pop-Location
    }

    Write-Output "Clean Windows verification passed for $commit."
}
finally {
    if ($KeepWorkspace) {
        Write-Output "Clean workspace retained at $phaseRoot."
    }
    elseif (Test-Path -LiteralPath $phaseRoot) {
        $resolvedPhaseRoot = [IO.Path]::GetFullPath($phaseRoot)
        $expectedPrefix = $tempRoot.TrimEnd("\") + "\jarvis-clean-"
        if (-not $resolvedPhaseRoot.StartsWith(
            $expectedPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove an unexpected clean-workspace path."
        }
        Remove-Item -LiteralPath $resolvedPhaseRoot -Recurse -Force
    }
}
