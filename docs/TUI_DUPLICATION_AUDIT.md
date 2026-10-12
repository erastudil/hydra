# Hydra TUI duplication audit, 2026-10-11

tree : one code tree only, C:\Users\jpm05\Documents\hydra (editable install, origin erastudil/hydra). no second copy of hydra_cli on disk or in site-packages.
fork : launchers only. Desktop had two TUI entries running two different programs.
sovereign : Hydra Sovereign Terminal.bat -> Documents\bin\hydra-terminal.bat -> hydra\launch\bin\hydra-terminal.bat -> python -m hydra_cli agent -> agent._create_tui -> hydra_cli/tui.py HydraTUI (composer input pane, wheel scroll, viewport pin).
logo shortcut : Hydra TUI.lnk -> python -m hydra_cli.cli tui -> router -> hydra_cli/repl.py classic readline REPL. no composer pane. wrong program.
desktop app : Hydra Desktop.lnk -> pythonw -m hydra_cli.desktop. crashed at start: pythonw has sys.stdout None, uvicorn log formatter calls sys.stdout.isatty(). no window, no error.
requested changes : the viewport/pin/spacer edits sit in main at bcdd284 inside tui.py, the file the sovereign terminal runs. nothing was left only in the parallel path. origin/main equals local main before this PR.
fix : Hydra TUI.lnk now targets launch\bin\hydra-terminal.bat. desktop.py guards None stdio under pythonw. verify.py gains desktop_app_pythonw_launch.
missing : Hydra Desktop is a local web server opened in the default browser tab. it is not a native window app. a Codex-replacement desktop app needs a native shell (pywebview, Tauri or Electron, or Edge --app window), a project/thread sidebar, a composer wired to agent_runner, diff and approval panes. none exist in the repo.

update : the browser app and its web server are retired. Hydra Desktop.lnk now launches the native PySide6 app (hydra_cli.gui).
