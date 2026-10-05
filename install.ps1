# ==============================================================================
# Hydra Installer for Windows PowerShell
# Usage: irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = 'Stop'

Write-Host "
  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
      Sovereign Multi-Headed AI Shell
" -ForegroundColor Cyan

$InstallDir = Join-Path $HOME ".hydra\bin"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

$RepoRaw = "https://raw.githubusercontent.com/erastudil/hydra/main"
$TargetJs = Join-Path $InstallDir "hydra.js"
$TargetPy = Join-Path $InstallDir "hydra"
$TargetCmd = Join-Path $InstallDir "hydra.cmd"
$TargetPs1 = Join-Path $InstallDir "hydra.ps1"

Write-Host "==> Downloading latest Hydra release..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "$RepoRaw/bin/hydra.js" -OutFile $TargetJs
Invoke-WebRequest -Uri "$RepoRaw/bin/hydra" -OutFile $TargetPy

# Create Windows CMD shim
$CmdContent = @"
@echo off
where node >nul 2>nul
if %ERRORLEVEL% equ 0 (
    node "%~dp0hydra.js" %*
    exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%~dp0hydra" %*
    exit /b %ERRORLEVEL%
)
echo [ERROR] Neither Node.js nor Python 3 was found on PATH.
exit /b 1
"@
Set-Content -Path $TargetCmd -Value $CmdContent -Encoding ASCII

# Create PowerShell script shim
$PsShimContent = @"
`$node = Get-Command node -ErrorAction SilentlyContinue
if (`$node) {
    & node "`$PSScriptRoot\hydra.js" `$args
    exit `$LASTEXITCODE
}
`$py = Get-Command python -ErrorAction SilentlyContinue
if (`$py) {
    & python "`$PSScriptRoot\hydra" `$args
    exit `$LASTEXITCODE
}
Write-Error "[ERROR] Neither Node.js nor Python 3 was found on PATH."
exit 1
"@
Set-Content -Path $TargetPs1 -Value $PsShimContent -Encoding UTF8

# Ensure user PATH includes $InstallDir
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$InstallDir*") {
    Write-Host "==> Adding $InstallDir to User PATH..." -ForegroundColor Yellow
    $NewPath = "$UserPath;$InstallDir"
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$env:Path;$InstallDir"
}

Write-Host "`n==> Hydra successfully installed to $InstallDir!" -ForegroundColor Green
Write-Host @"

Quickstart:
  `$env:OPENROUTER_API_KEY="your_api_key_here"
  hydra opus 5.5 "Explain zero-cost abstractions"
  hydra free "Explain consensus algorithms"
  hydra local "Write a fast LRU cache in Go"
  hydra swarm "Architect a high-throughput event pipeline"

Note: If 'hydra' is not recognized immediately in existing shells, restart your terminal.
"@ -ForegroundColor Cyan
