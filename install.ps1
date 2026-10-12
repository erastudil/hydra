# Hydra installer for Windows PowerShell.
# irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex

$ErrorActionPreference = 'Stop'

$python = Get-Command python -ErrorAction SilentlyContinue
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $python -and -not $node) {
    Write-Error '[ERROR] Hydra needs Python 3 or Node.js 18+.'
    exit 1
}

$Root = Join-Path $HOME '.hydra'
$Bin = Join-Path $Root 'bin'
$Repo = 'https://raw.githubusercontent.com/erastudil/hydra/main'
# Every runtime file. python scripts/verify.py fails if this list drifts.
$Files = @(
    'bin/hydra',
    'bin/hydra.js',
    'hydra_cli/__init__.py',
    'hydra_cli/__main__.py',
    'hydra_cli/_version.py',
    'hydra_cli/agent.py',
    'hydra_cli/agent_runner.py',
    'hydra_cli/agent_runners.py',
    'hydra_cli/alice_chat.py',
    'hydra_cli/alice_cloud_loop.py',
    'hydra_cli/alice_gate.py',
    'hydra_cli/alice_gcloud.py',
    'hydra_cli/alice_interpret.py',
    'hydra_cli/alice_knowledge.py',
    'hydra_cli/alice_retrieve.py',
    'hydra_cli/alice_runner.py',
    'hydra_cli/alice_senses.py',
    'hydra_cli/auth.py',
    'hydra_cli/browser.py',
    'hydra_cli/catalog.json',
    'hydra_cli/cli.py',
    'hydra_cli/computer_use.py',
    'hydra_cli/config.py',
    'hydra_cli/context.py',
    'hydra_cli/desktop_core.py',
    'hydra_cli/data/stacks_cards.jsonl',
    'hydra_cli/dictation.py',
    'hydra_cli/display.py',
    'hydra_cli/gui/__init__.py',
    'hydra_cli/gui/__main__.py',
    'hydra_cli/gui/app.py',
    'hydra_cli/gui/motion.py',
    'hydra_cli/gui/panes.py',
    'hydra_cli/gui/probe.py',
    'hydra_cli/gui/store.py',
    'hydra_cli/gui/theme.py',
    'hydra_cli/gui/transcript.py',
    'hydra_cli/gui/window.py',
    'hydra_cli/gui/worker.py',
    'hydra_cli/hands.py',
    'hydra_cli/invariants.json',
    'hydra_cli/invariants.py',
    'hydra_cli/mcp.py',
    'hydra_cli/mcp_registry.py',
    'hydra_cli/mcp_servers.default.json',
    'hydra_cli/memory.py',
    'hydra_cli/native_tools.py',
    'hydra_cli/providers.py',
    'hydra_cli/repl.py',
    'hydra_cli/router.py',
    'hydra_cli/sandbox.py',
    'hydra_cli/serve.py',
    'hydra_cli/session_logger.py',
    'hydra_cli/skills/alice.md',
    'hydra_cli/skills/backend.md',
    'hydra_cli/skills/code.md',
    'hydra_cli/skills/containers.md',
    'hydra_cli/skills/devops.md',
    'hydra_cli/skills/disambiguation.md',
    'hydra_cli/skills/discovery.md',
    'hydra_cli/skills/easylm.md',
    'hydra_cli/skills/frontend.md',
    'hydra_cli/skills/grammar.md',
    'hydra_cli/skills/hydra.md',
    'hydra_cli/skills/logic.md',
    'hydra_cli/skills/stack.md',
    'hydra_cli/speculative.py',
    'hydra_cli/swarm.py',
    'hydra_cli/tool_adapter.py',
    'hydra_cli/tui.py',
    'hydra_cli/ui.py',
    'hydra_cli/voice.py',
    'hydra_cli/win_console.py',
    'hydra_cli/workflow_jobs.py'
)

Write-Host "==> Installing Hydra into $Root"
foreach ($rel in $Files) {
    $dest = Join-Path $Root ($rel -replace '/', '\')
    $parent = Split-Path $dest -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Invoke-WebRequest -Uri "$Repo/$rel" -OutFile $dest
}

$CmdContent = @'
@echo off
setlocal EnableExtensions
where python >nul 2>nul
if errorlevel 1 goto try_node
python "%~dp0hydra" %*
exit /b %ERRORLEVEL%
:try_node
where node >nul 2>nul
if errorlevel 1 goto missing
node "%~dp0hydra.js" %*
exit /b %ERRORLEVEL%
:missing
echo [ERROR] Hydra needs Python 3 or Node.js 18+.
exit /b 1
'@
Set-Content -Path (Join-Path $Bin 'hydra.cmd') -Value $CmdContent -Encoding ASCII
# Same launcher as repo bin/hydra.bat — keep both names so desktop shortcuts work.
Set-Content -Path (Join-Path $Bin 'hydra.bat') -Value $CmdContent -Encoding ASCII

$PsShim = @'
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    & python "$PSScriptRoot\hydra" @args
    exit $LASTEXITCODE
}
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    & node "$PSScriptRoot\hydra.js" @args
    exit $LASTEXITCODE
}
Write-Error '[ERROR] Hydra needs Python 3 or Node.js 18+.'
exit 1
'@
Set-Content -Path (Join-Path $Bin 'hydra.ps1') -Value $PsShim -Encoding UTF8

$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($UserPath -notlike "*$Bin*") {
    Write-Host "==> Adding $Bin to User PATH"
    $NewPath = if ([string]::IsNullOrEmpty($UserPath)) { $Bin } else { "$UserPath;$Bin" }
    [Environment]::SetEnvironmentVariable('Path', $NewPath, 'User')
    $env:Path = "$env:Path;$Bin"
}

Write-Host "==> Hydra is installed."
Write-Host "    $Bin"
Write-Host "    Put keys in $Root\.env or the environment, then open a new terminal."
Write-Host "    hydra setup"
