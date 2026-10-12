#!/usr/bin/env bash
# Mutation test for the no-console checks: each mutation breaks one property and the check must then FAIL.
set -uo pipefail
cd "$(dirname "$0")/.."
SRC=hydra-desktop-rs/src
ADAPTER=hydra_cli/stdio_adapter.py
EXE=${1:-hydra-desktop-rs/target/x86_64-pc-windows-gnu/release/Hydra.exe}
fail=0
run() { python3 scripts/check_no_console.py "$@" >/dev/null 2>&1; }

mutate() { # name file sed-expr
  local name=$1 file=$2 expr=$3
  cp "$file" "$file.mut.bak"
  sed -i -E "$expr" "$file"
  if cmp -s "$file" "$file.mut.bak"; then echo "MUTATION NOT APPLIED: $name"; fail=1; mv "$file.mut.bak" "$file"; return; fi
  if run --src "$SRC" --adapter "$ADAPTER"; then echo "SURVIVED (bad): $name"; fail=1; else echo "killed: $name"; fi
  mv "$file.mut.bak" "$file"
}

run --src "$SRC" --adapter "$ADAPTER" || { echo "baseline check fails; fix first"; exit 1; }
mutate "windows_subsystem removed" $SRC/main.rs 's/^#!\[windows_subsystem = "windows"\]//'
mutate "console subsystem" $SRC/main.rs 's/windows_subsystem = "windows"/windows_subsystem = "console"/'
mutate "flag value zeroed" $SRC/proc.rs 's/0x0800_0000/0x0000_0000/'
mutate "creation_flags dropped" $SRC/proc.rs 's/cmd\.creation_flags\(CREATE_NO_WINDOW\);/let _ = CREATE_NO_WINDOW;/'
mutate "adapter patch not installed" $ADAPTER 's/^    hide_child_consoles\(\)$/    pass/'
mutate "adapter flag not or-ed" $ADAPTER 's/\| CREATE_NO_WINDOW$/| 0/'
# a raw Command::new in a new file
printf 'fn f() { let _ = std::process::Command::new("git"); }\n' > $SRC/zz_mut.rs
if run --src "$SRC" --adapter "$ADAPTER"; then echo "SURVIVED (bad): raw Command::new"; fail=1; else echo "killed: raw Command::new"; fi
rm -f $SRC/zz_mut.rs
# exe with the subsystem byte flipped to console (3)
if [ -f "$EXE" ]; then
  python3 - "$EXE" /tmp/Hydra-console.exe <<'P'
import struct,sys
d=bytearray(open(sys.argv[1],'rb').read()); pe=struct.unpack_from('<I',d,0x3c)[0]; struct.pack_into('<H',d,pe+24+68,3); open(sys.argv[2],'wb').write(d)
P
  if run --exe /tmp/Hydra-console.exe --src "$SRC" --adapter "$ADAPTER"; then echo "SURVIVED (bad): console-subsystem exe"; fail=1; else echo "killed: console-subsystem exe"; fi
  run --exe "$EXE" --src "$SRC" --adapter "$ADAPTER" || { echo "real exe fails the check"; fail=1; }
fi
[ $fail = 0 ] && echo "mutation pass: all checks kill their mutations" || { echo "mutation pass FAILED"; exit 1; }
