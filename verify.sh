#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "=== [1/4] hydra python verification suite (scripts/verify.py) ==="
python3 scripts/verify.py

echo "=== [2/4] hydra-stdio cargo test ==="
cargo test --manifest-path hydra-stdio/Cargo.toml

echo "=== [3/4] hydra-tui-rs cargo test ==="
cargo test --manifest-path hydra-tui-rs/Cargo.toml

echo "=== [4/4] hydra-desktop-rs cargo test (lib + ui + concurrency + stdio_integration + syslibs_real + ui_real) ==="
cargo test --manifest-path hydra-desktop-rs/Cargo.toml --lib
cargo test --manifest-path hydra-desktop-rs/Cargo.toml --test ui --test concurrency --test stdio_integration --test syslibs_real
cargo test --manifest-path hydra-desktop-rs/Cargo.toml --test ui_real -- --test-threads=1

echo "=== HYDRA VERIFICATION PASSED (EXIT 0) ==="
