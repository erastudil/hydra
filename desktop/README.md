# hydra desktop

scope : native PySide6 desktop app for hydra. no browser, no web view, no local server.

launch : `python -m hydra_cli.gui`, `hydra desktop`, or the Desktop shortcut made by `scripts/create_desktop_shortcut.py`.
exe : `python scripts/build_exe.py` writes `dist/Hydra/Hydra.exe` with PyInstaller, QtWebEngine excluded.
code : `hydra_cli/gui/` holds the window, panes, store, worker and theme.
agent : the composer runs `hydra_cli.agent.run_agent_loop` in a worker thread. tokens stream into the transcript through a 33 ms flush.
state : projects, threads, messages and diffs persist in `~/.hydra/desktop/state.json`.
panes : project sidebar, thread sidebar, transcript, composer, changes (diff), approval.
context : composer selector picks dynamic threading (`recall`) or compaction (`compact`).
stop : `hydra_cli.ui.request_cancel` raises `TurnCancelled` inside the running turn.
theme : `hydra_cli/gui/theme.py` owns palette, fonts and the stylesheet.
core : `hydra_cli/desktop_core.py` keeps token accounting, command palette actions and provider fallback.
modules : the other files in this package are standalone libraries with no UI of their own.
