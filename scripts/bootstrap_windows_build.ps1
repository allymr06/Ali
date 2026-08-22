param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$BuildToolsRoot = "",
    [string]$PythonVersion = "3.12.10",
    [string]$InnoVersion = "7.0.2"
)

$ErrorActionPreference = "Stop"
if (-not $BuildToolsRoot) {
    $BuildToolsRoot = Join-Path $ProjectRoot "build-tools"
}
$toolsRoot = $BuildToolsRoot
$downloadRoot = Join-Path $toolsRoot "downloads"
$pythonRoot = Join-Path $toolsRoot "python312"
$venvRoot = Join-Path $toolsRoot "venv"
$innoRoot = Join-Path $toolsRoot "inno"
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null

function Assert-TrustedSignature {
    param([string]$Path, [string]$PublisherPattern)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid") {
        throw "Invalid Authenticode signature for ${Path}: $($signature.Status)"
    }
    if ($signature.SignerCertificate.Subject -notmatch $PublisherPattern) {
        throw "Unexpected publisher for ${Path}: $($signature.SignerCertificate.Subject)"
    }
}

function Invoke-TrustedDownload {
    param([string]$Uri, [string]$OutputPath)
    & "$env:SystemRoot\System32\curl.exe" --fail --location --silent `
        --show-error --proto "=https" --tlsv1.2 --output $OutputPath $Uri
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed with exit code ${LASTEXITCODE}: $Uri"
    }
}

$pythonInstaller = Join-Path $downloadRoot "python-$PythonVersion-amd64.exe"
if (-not (Test-Path -LiteralPath $pythonInstaller)) {
    Invoke-TrustedDownload `
        -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" `
        -OutputPath $pythonInstaller
}
Assert-TrustedSignature -Path $pythonInstaller -PublisherPattern "Python Software Foundation"
Unblock-File -LiteralPath $pythonInstaller

if (-not (Test-Path -LiteralPath (Join-Path $pythonRoot "python.exe"))) {
    $pythonProcess = Start-Process -FilePath $pythonInstaller -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "TargetDir=`"$pythonRoot`"",
        "Include_pip=1",
        "Include_tcltk=1",
        "Include_launcher=0",
        "PrependPath=0",
        "Shortcuts=0",
        "Include_test=0"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($pythonProcess.ExitCode -ne 0) {
        throw "Python installer failed with exit code $($pythonProcess.ExitCode)"
    }
}

$python = Join-Path $pythonRoot "python.exe"
if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
    & $python -m venv $venvRoot
}
$buildPython = Join-Path $venvRoot "Scripts\python.exe"
& $buildPython -m pip install --disable-pip-version-check `
    --requirement (Join-Path $ProjectRoot "requirements-package.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Package dependency installation failed with exit code $LASTEXITCODE"
}

$innoInstaller = Join-Path $downloadRoot "innosetup-$InnoVersion-x64.exe"
if (-not (Test-Path -LiteralPath $innoInstaller)) {
    Invoke-TrustedDownload `
        -Uri "https://github.com/jrsoftware/issrc/releases/download/is-$($InnoVersion.Replace('.', '_'))/innosetup-$InnoVersion-x64.exe" `
        -OutputPath $innoInstaller
}
Assert-TrustedSignature -Path $innoInstaller -PublisherPattern "JR Software|Pyrsys"
Unblock-File -LiteralPath $innoInstaller

if (-not (Test-Path -LiteralPath (Join-Path $innoRoot "ISCC.exe"))) {
    $innoProcess = Start-Process -FilePath $innoInstaller -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/CURRENTUSER",
        "/DIR=`"$innoRoot`""
    ) -Wait -PassThru -WindowStyle Hidden
    if ($innoProcess.ExitCode -ne 0) {
        throw "Inno Setup installer failed with exit code $($innoProcess.ExitCode)"
    }
}

Write-Output "Build Python: $buildPython"
Write-Output "Inno compiler: $(Join-Path $innoRoot 'ISCC.exe')"
