"""Idle watchdog for the Hydra-managed local llama-server.

Owner ruling 2026-11-09: the local model loads when the owner starts
Hydra and unloads after about an hour idle; a standing resident server
is not a resting state. This process is that unload half. It is spawned
detached by :func:`hydra_cli.local_server.ensure_watchdog` whenever a
Hydra-managed server is started (or found already running), and it:

- follows the server recorded in ~/.hydra/local-server-state.json,
- polls the server's /slots endpoint: a slot mid-generation, or the
  slots' task counters advancing between polls, counts as activity,
- stops the server — through stop_managed_server, which re-verifies
  the recorded pid is the identifiable llama-server on the local
  endpoint before killing anything — once the idle window
  (local_server.idle_unload_seconds: settings
  ``local_idle_unload_seconds``, default 3600) passes with no activity,
- exits when the server dies by any other route, when the state file
  stops naming a Hydra-managed server, or when a newer watchdog
  supersedes it. It never touches a process it cannot identify as the
  recorded Hydra-managed local server.

Run as ``python -m hydra_cli.local_watchdog``; stdout/stderr land in
~/.hydra/logs/local-watchdog.log via the spawn redirect.
"""

from __future__ import annotations

import os
import time

from hydra_cli import local_server


def _log(message: str) -> None:
    try:
        print(f"local-watchdog[{os.getpid()}]: {message}", flush=True)
    except Exception:
        pass


def run() -> int:
    state = local_server.read_server_state()
    server_pid = state.get("pid")
    if state.get("managed_by") != "hydra" or not isinstance(server_pid, int):
        _log("no Hydra-managed server recorded; exiting")
        return 0
    # The lease starts when the watchdog takes the case: ensure_watchdog
    # is called from Hydra's start/serve paths, so a watchdog (re)spawn
    # is itself Hydra-start activity. Slot activity then renews it.
    last_activity = time.time()
    last_total = None
    _log(f"watching server pid {server_pid}; idle limit {local_server.idle_unload_seconds()}s")
    while True:
        limit = local_server.idle_unload_seconds()
        poll = 15.0 if limit >= 120 else max(2.0, limit / 8.0)
        time.sleep(poll)

        state = local_server.read_server_state()
        if state.get("managed_by") != "hydra":
            _log("server no longer recorded as Hydra-managed; exiting")
            return 0
        wd_pid = state.get("watchdog_pid")
        if isinstance(wd_pid, int) and wd_pid != os.getpid() and local_server._pid_alive(wd_pid):
            _log(f"superseded by watchdog pid {wd_pid}; exiting")
            return 0
        pid = state.get("pid")
        if not isinstance(pid, int) or pid != server_pid or not local_server._pid_alive(pid):
            _log("managed server is gone; exiting")
            return 0

        signature = local_server.slots_activity_signature(local_server.slot_info())
        now = time.time()
        if signature is not None:
            processing, total = signature
            if processing or (last_total is not None and total != last_total):
                last_activity = now
            last_total = total

        if now - last_activity >= limit:
            _log(f"no slot activity for {now - last_activity:.0f}s (limit {limit}s); unloading")
            result = local_server.stop_managed_server(reason="idle-unload")
            _log(f"stop result: {result}")
            return 0


if __name__ == "__main__":
    raise SystemExit(run())
