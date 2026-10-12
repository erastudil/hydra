# hydra desktop

scope : native Rust desktop app for hydra, `hydra-desktop-rs/`. no browser, no web view, no local server. the PySide6 app was removed.

launch : `hydra desktop`, `dist/Hydra/Hydra.exe`, or the Desktop shortcut made by `scripts/create_desktop_shortcut.py`.
build : `scripts/check_rust.sh` runs cargo test (unit plus an integration test that drives the real stdio adapter against a stub model endpoint), builds release, and cross builds the Windows GNU exe.
agent : the app spawns `python -m hydra_cli stdio` as a child and exchanges JSON lines (docs/STDIO_PROTOCOL.md). it finds the checkout and python itself; override with HYDRA_REPO and HYDRA_PYTHON.
state : sessions and messages persist in `~/.hydra/desktop/state.json` (HYDRA_STATE_DIR overrides). thread ids double as the agent ledger keys, so context survives restarts.
design : `assets/design/hydra-desk.tokens.json` is compiled into the app by build.rs.
core : `hydra_cli/desktop_core.py` keeps token accounting, command palette actions and provider fallback; `hydra_cli/desk_actions.py` adds the terminal, swarm, computer use, agent runner and jobs actions the panels call.
modules : the other files in this package are standalone libraries with no UI of their own.
