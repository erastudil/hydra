$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

Write-Host "=== [1/4] hydra python verification suite (scripts/verify.py) ==="
python scripts/verify.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== [2/4] hydra-stdio cargo test ==="
cargo test --manifest-path hydra-stdio/Cargo.toml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== [3/4] hydra-tui-rs cargo test ==="
cargo test --manifest-path hydra-tui-rs/Cargo.toml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== [4/4] hydra-desktop-rs cargo test (lib + ui + concurrency + stdio_integration + syslibs_real + ui_real) ==="
cargo test --manifest-path hydra-desktop-rs/Cargo.toml --lib
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cargo test --manifest-path hydra-desktop-rs/Cargo.toml --test ui --test concurrency --test stdio_integration --test syslibs_real
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cargo test --manifest-path hydra-desktop-rs/Cargo.toml --test ui_real -- --test-threads=1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== HYDRA VERIFICATION PASSED (EXIT 0) ==="
