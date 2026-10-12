#!/usr/bin/env bash
# Headless screenshots of the real native build against a stub provider and the real adapter. Nothing touches a real screen.
# usage: scripts/shots_rust.sh OUTDIR
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; OUT="${1:-/tmp/hydra-shots}"; mkdir -p "$OUT"
BIN="${HYDRA_BIN:-$ROOT/hydra-desktop-rs/target/release/Hydra}"; PORT=18999
python3 "$ROOT/scripts/qa_stub.py" $PORT >/dev/null 2>&1 & STUB=$!
trap 'kill $STUB 2>/dev/null || true' EXIT
sleep 1
shot() { # name WxH frames [ENV=VAL ...]
  local name=$1 size=$2 frames=$3; shift 3
  local home; home=$(mktemp -d); mkdir -p "$home/proj"
  ( cd "$home/proj" && env HOME="$home" HYDRA_HOME="$home/.hydra" HYDRA_STATE_DIR="$home/state" HYDRA_CWD="$home/proj" HYDRA_SIZE="$size" \
      HYDRA_REPO="$ROOT" HYDRA_MODEL=local LOCAL_AI_BASE="http://127.0.0.1:$PORT" HYDRA_SHOT="$OUT/$name.png" HYDRA_SHOT_FRAMES="$frames" "$@" \
      timeout 90 xvfb-run -a -s "-screen 0 1920x1080x24" "$BIN" >/dev/null 2>&1 ) || echo "shot $name: exit $?"
  rm -rf "$home"
}
shot approval_modal 1280x800 100000 HYDRA_SHOT_SECS=9 HYDRA_DEMO_PROMPT="WRITE the file"
shot settings_first_run 1280x800 40 HYDRA_OPEN=settings
shot diff_pane 1280x800 100000 HYDRA_SHOT_SECS=12 HYDRA_PERMISSION=bypass HYDRA_TAB=diff HYDRA_DEMO_PROMPT="BIGDIFF please"
shot mode_terminal 1280x800 40 HYDRA_NAV=terminal
shot mode_agent 1280x800 40 HYDRA_NAV=agent
shot mode_computer 1280x800 40 HYDRA_NAV=computer
shot mode_swarm 1280x800 40 HYDRA_NAV=swarm
shot cut_off_stream 1280x800 100000 HYDRA_SHOT_SECS=9 HYDRA_DEMO_PROMPT="CUT test"
shot narrow_820 820x560 60
shot narrow_900 900x700 60
shot rules_tab 1280x800 100000 HYDRA_SHOT_SECS=10 HYDRA_TAB=rules HYDRA_DEMO_PROMPT="WRITE the file" HYDRA_PERMISSION=accept_edits

shot tasks_running 1280x800 100000 HYDRA_SHOT_SECS=12 HYDRA_TAB=tasks HYDRA_PERMISSION=bypass HYDRA_DEMO_PROMPT="BGTASK start a server"
# every drawer tab at both release-bar sizes (pinned via HYDRA_TAB; a diff never yanks the drawer)
for sz in 1280x800 900x700; do
  for t in screen term diff agent files git approvals rules skills mcp tasks; do
    shot "tab_${t}_${sz}" $sz 100000 HYDRA_SHOT_SECS=9 HYDRA_TAB=$t HYDRA_DEMO_PROMPT="WRITE the file" HYDRA_PERMISSION=accept_edits
  done
done
ls -1 "$OUT"
