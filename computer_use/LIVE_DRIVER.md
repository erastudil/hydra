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
