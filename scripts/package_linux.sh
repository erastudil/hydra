#!/usr/bin/env bash
# Build the Linux release folder dist/Hydra-linux: Hydra, runtime/ (standalone CPython, runtime/python3), app/ (hydra_cli).
# Same layout as the Windows folder, so the clean-machine test of release bar item 9 can run on the box.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
PBS=${PBS:-20261009}; PYVER=${PYVER:-3.12.15}
OUT=${OUT:-dist/Hydra-linux}; CACHE=${CACHE:-/tmp/hydra-pkg-cache}
mkdir -p "$CACHE"
(cd hydra-desktop-rs && cargo build --release -q)
rm -rf "$OUT" && mkdir -p "$OUT/app"
cp hydra-desktop-rs/target/release/Hydra "$OUT/"
TGZ="$CACHE/cpython-$PYVER-$PBS-linux.tar.gz"
[ -f "$TGZ" ] || curl -fsSL -o "$TGZ" "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS/cpython-$PYVER%2B$PBS-x86_64-unknown-linux-gnu-install_only.tar.gz"
tar -xzf "$TGZ" -C "$OUT" && mv "$OUT/python" "$OUT/runtime"
ln -s bin/python3 "$OUT/runtime/python3"
"$OUT/runtime/python3" -m pip install --quiet --no-warn-script-location --target "$OUT/runtime/lib/python3.12/site-packages" prompt_toolkit wcwidth pillow
cp -r hydra_cli "$OUT/app/hydra_cli"
find "$OUT/app" -name __pycache__ -prune -exec rm -rf {} +
du -sh "$OUT"; echo "package: $OUT"
