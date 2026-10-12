#!/usr/bin/env bash
# Gate for the native Rust desktop: tests, linux build, windows (gnu) cross build. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/../hydra-desktop-rs"
export PATH="$HOME/.cargo/bin:$PATH"
cargo test --quiet
cargo build --release --quiet
if rustup target list --installed 2>/dev/null | grep -q x86_64-pc-windows-gnu; then
  cargo build --release --quiet --target x86_64-pc-windows-gnu
  test -f target/x86_64-pc-windows-gnu/release/Hydra.exe
  echo "windows exe: $(du -h target/x86_64-pc-windows-gnu/release/Hydra.exe | cut -f1)"
fi
cd ..
python3 scripts/check_no_console.py --src hydra-desktop-rs/src --runtime "$PWD/hydra-desktop-rs/target/release/Hydra" \
  $( [ -f hydra-desktop-rs/target/x86_64-pc-windows-gnu/release/Hydra.exe ] && echo --exe hydra-desktop-rs/target/x86_64-pc-windows-gnu/release/Hydra.exe )
scripts/mutate_checks.sh
echo "check_rust: ok"
