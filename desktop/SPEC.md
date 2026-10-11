# hydra desktop specification

scope : contract for the native hydra desktop app. there is no server and no http route.

entry : `hydra_cli.gui.app.main(argv)` creates one QApplication and one MainWindow, returns the Qt exit code.
store : `hydra_cli.gui.store.Store` reads and writes `~/.hydra/desktop/state.json` atomically; projects own threads; threads own messages and diffs.
worker : `AgentWorker.run_turn(req)` runs in a QThread; req keys are prompt, alias, session_id, cwd, mode, approve.
signals : token, tool, diff, approval, finished(text, ok).
approval : with approve on, each tool call blocks the worker until `answer_approval` receives y, n, s or q.
cancel : `request_cancel` makes the next token, tool call or turn boundary raise `TurnCancelled`.
modes : recall is dynamic threading, compact is compaction; both come from `hydra_cli.context.CONTEXT_MODES`.
windowless : under pythonw or a windowed exe, stdout and stderr go to `~/.hydra/desktop.log`.
