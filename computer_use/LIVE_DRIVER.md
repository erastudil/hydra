# live driver (computer use in the owner's real session)

`live_driver.py` runs Hydra's own `ComputerUseEngine`
(`hydra_cli.computer_use`: OSController input via user32, ScreenCaptureEngine
via ImageGrab) inside the user's interactive Windows session and executes
command files dropped by a remote operator over SSH. Input injected from
session 0 (a plain SSH shell) never reaches the user's desktop; this driver
is the bridge that puts the engine's hands in session 1, where the owner's
real Brave browser lives.

## run it

```
schtasks /Create /TN HydraLiveDriver /IT /F /SC ONCE /ST 23:59 ^
  /TR "\"C:\Python314\pythonw.exe\" \"C:\Users\jpm05\Documents\hydra\computer_use\live_driver.py\""
schtasks /Run /TN HydraLiveDriver
```

`C:\Python314\python.exe` is the interpreter with Pillow on this machine
(the browser-venv has none; capture fails honestly without it).

## ipc

Workdir `C:\Users\jpm05\hydra\live-driver` (override `HYDRA_LIVE_DRIVER_HOME`):

- `cmd\<id>.json` — `{"id", "action", "params"}`; actions: screenshot,
  click, move, key, chord, paste, type, focus, windows, active, launch,
  wait, quit.
- `done\<id>.json` — the result, written atomically.
- `shots\` — screenshots; `heartbeat.json` — pid, session id, screen size;
  `driver.log` — the action log.

`paste` sets the session clipboard (Win32 CF_UNICODETEXT) and sends Ctrl+V
through the engine. `type` uses VkKeyScanW so punctuation follows the real
keyboard layout.

## screen glow

`screen_glow.py` is the active-use border glow from the 2026-10-20
audit spec: full-screen, gradient, green, pulsing, shown ONLY while
computer use is executing. The driver launches it at startup and
refuses to run if it cannot start (the failure lands in `driver.log`
as a FATAL startup error -- an unglowed run would be a silent spec
violation). Stopping the driver stops the glow; the glow also watches
the driver pid and exits on its own if the driver dies.

- Rendering: a pure Win32 layered window with per-pixel alpha
  (UpdateLayeredWindow over a 32bpp DIB), click-through
  (WS_EX_TRANSPARENT), always-on-top, never focus-taking
  (WS_EX_NOACTIVATE). Per-pixel alpha is load-bearing: the falloff
  lives in the alpha channel, cos^2 over a 36 px frame, reaching zero
  with zero slope -- the glow fades to nothing with no internal edge.
  (Opaque color-keyed bands cannot do this; their last band is always
  a visible line. First build, replaced.)
- Peak opacity 0.8 at the screen edge; color gradient (64,255,120) ->
  (0,64,22); pulse 1.6 s period, intensity 0.62..1.0 (unchanged since
  the owner called it spot on).
- State: the driver maintains `glow_state.json` in the workdir --
  `executing` true around each command, `last_activity` stamped at
  each command end. The glow is visible while executing or within a
  2 s idle grace after the last activity, then hides.
- Session-level glow (owner ruling, 2026-10-27): per-command gating
  flickers when a driver task loop's think time (local model
  inference between commands) exceeds the 2 s grace. A task loop
  therefore asserts `session_state.json` in the workdir --
  `{"active": true, "loop_pid": ..., "updated": <ISO timestamp>}` --
  heartbeated at least once per second for as long as the loop is
  alive, including during inference, with a final `{"active": false}`
  write on exit. The driver passes the path to the glow via
  `--session` and deletes any stale session file at startup. While
  the session file is fresh (updated within 30 s) and active, the
  glow stays lit continuously (pulse unchanged); if heartbeats stop
  for ~30 s the glow clears, so a hung loop cannot leave it lit
  forever. Session-active governs visibility; the per-command
  executing/grace signal remains as a secondary input.
- Handshake: `glow_ready.json` (glow pid) once the window exists and
  a first frame has presented; `glow_error.txt` carries the traceback
  on setup failure.

## safety

- Engine return values are never proof (2026-10-20 audit: failure paths
  once simulated success). The operator verifies every step from saved
  screenshots, and a task counts as done only on external evidence: a
  file on disk, a screenshot read by the operator.
- Screenshot-verify the visible window before any click or keystroke.
  Never type into a document or text field that is not the task's own
  target (the Notepad incident rule).
- The driver never enters credentials. If a target flow is logged out,
  the operator stops and leaves the login to the owner.
