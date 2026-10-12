#!/usr/bin/env bash
# Build the release folder dist/Hydra: Hydra.exe, runtime/ (embeddable CPython + wheels), app/ (hydra_cli), LICENSE.
# Cross-builds on Linux. The result is a single folder that runs with no system Python and no checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
PYVER=${PYVER:-3.12.7}
OUT=${OUT:-dist/Hydra}
CACHE=${CACHE:-/tmp/hydra-pkg-cache}
mkdir -p "$CACHE"
(cd hydra-desktop-rs && cargo build --release --target x86_64-pc-windows-gnu)
rm -rf "$OUT" && mkdir -p "$OUT/runtime" "$OUT/app"
cp hydra-desktop-rs/target/x86_64-pc-windows-gnu/release/Hydra.exe "$OUT/"
ZIP="$CACHE/python-$PYVER-embed-amd64.zip"
[ -f "$ZIP" ] || curl -fsSL -o "$ZIP" "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip"
unzip -q -o "$ZIP" -d "$OUT/runtime"
SP="$OUT/runtime/Lib/site-packages"
mkdir -p "$SP" "$CACHE/wheels"
PYMM=$(echo "$PYVER" | cut -d. -f1,2)
python3 -m pip download --quiet --dest "$CACHE/wheels" --platform win_amd64 --python-version "$PYMM" --only-binary=:all: prompt_toolkit wcwidth pillow
for w in "$CACHE"/wheels/*.whl; do unzip -q -o "$w" -d "$SP"; done
# the embeddable interpreter ignores PYTHONPATH when a ._pth file exists, so list the search path here
PTH=$(ls "$OUT"/runtime/python*._pth)
ZNAME=$(basename "$PTH" ._pth)
printf '%s.zip\n.\nLib\\site-packages\n..\\app\nimport site\n' "$ZNAME" > "$PTH"
cp -r hydra_cli "$OUT/app/hydra_cli"
find "$OUT/app" -name __pycache__ -prune -exec rm -rf {} +
cp LICENSE "$OUT/" 2>/dev/null || true
[ -f hydra-desktop-rs/assets/hydra_desktop.png ] && mkdir -p "$OUT/assets" && cp hydra-desktop-rs/assets/hydra_desktop.png "$OUT/assets/"
cat > "$OUT/README.txt" <<'R'
Hydra Desktop. Double click Hydra.exe. Everything it needs is in this folder (runtime\ and app\).
Saved sessions: %USERPROFILE%\.hydra\desktop. Logs: %USERPROFILE%\.hydra\desktop\logs.
R
du -sh "$OUT"
echo "package: $OUT"
