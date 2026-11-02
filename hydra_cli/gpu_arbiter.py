"""GPU arbiter: the front desk steps aside, by design and on the record.

The laptop has one 8 GB GPU and the llama.cpp endpoint serves one local
model at a time. The front-desk model (Gemma E4B, the catalog's default
local model) holds that seat. When a heavy local job needs the GPU — a
Bonsai coding summon, an image generation — the desk steps aside: the
server is stopped, the job runs, and the desk is served again when the
job ends. Every transition is appended to ~/.hydra/logs/gpu-arbiter.jsonl
(event, model, reason, timestamp; no prompt content).

Manual override, in both interfaces:
- HYDRA_STEP_ASIDE=0 in the environment disables automatic step-aside.
- `/desk hold` (REPL) or the desk:hold desk action pins the desk in
  place until `/desk auto` releases it.

Three-tier context (owner design, 2026-11-01), and how it maps here:

- Tier 1, VRAM: the live thread's KV cache in the running server.
- Tier 2, RAM/disk cache: parked KV state. llama.cpp slot save/restore
  is the mechanism — Hydra-started servers run with
  ``--slot-save-path ~/.hydra/kv-cache``. `park_thread` writes a slot's
  KV to that folder; a copy in system RAM is the OS page cache's
  business, the file is the durable form. `restore_thread` loads it
  back into a slot.
- Tier 3, disk, CANONICAL: the append-only session records under
  ~/.hydra/sessions. Tiers 1-2 are caches of that record, never the
  only copy; any thread can be rebuilt from disk.

Honest limit, stated once and relied on everywhere: a saved KV cache is
only restorable into the same model + quant + context configuration.
Switching models means rebuild from the tier-3 record — which is why
the record is canonical. When a thread is bound for parking (desk state
``parked_thread``), step-aside parks its KV before stopping the desk
and restores it on return, when the returning server is the same model
the KV was saved from.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from hydra_cli import local_server


def _hydra_dir() -> Path:
    path = Path.home() / ".hydra"
    path.mkdir(parents=True, exist_ok=True)
    return path


def desk_state_path() -> Path:
    return _hydra_dir() / "desk-state.json"


def read_desk_state() -> Dict[str, Any]:
    try:
        data = json.loads(desk_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_desk_state(state: Dict[str, Any]) -> None:
    try:
        desk_state_path().write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def log_event(event: str, **fields: Any) -> None:
    try:
        log_dir = _hydra_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {"ts": int(time.time()), "event": event}
        line.update(fields)
        with open(log_dir / "gpu-arbiter.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception:
        pass
    try:
        sys.stderr.write(f"gpu-arbiter: {event} {fields}\n")
        sys.stderr.flush()
    except Exception:
        pass


def front_desk_model() -> str:
    from hydra_cli.config import DEFAULT_LOCAL_MODEL

    return DEFAULT_LOCAL_MODEL


def step_aside_enabled() -> bool:
    if os.environ.get("HYDRA_STEP_ASIDE", "").strip() == "0":
        return False
    return read_desk_state().get("override") != "hold"


def set_override(mode: str) -> Dict[str, Any]:
    """mode: 'hold' pins the desk; 'auto' re-enables step-aside."""
    state = read_desk_state()
    state["override"] = "hold" if mode == "hold" else "auto"
    _write_desk_state(state)
    log_event("override", override=state["override"])
    return desk_status()


def desk_status() -> Dict[str, Any]:
    state = read_desk_state()
    served = local_server.served_model_ids()
    server_state = local_server.read_server_state()
    return {
        "front_desk_model": front_desk_model(),
        "served": served,
        "server_pid": server_state.get("pid") or local_server.listener_pid(),
        "managed_by_hydra": server_state.get("managed_by") == "hydra",
        "aside": bool(state.get("aside")),
        "aside_reason": state.get("reason"),
        "override": state.get("override", "auto"),
        "step_aside_enabled": step_aside_enabled(),
        "parked_thread": state.get("parked_thread"),
    }


def _restore_target(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return state.get("restore") if isinstance(state.get("restore"), dict) else None


def step_aside(reason: str) -> Dict[str, Any]:
    """Unload whatever local model is serving, remembering how to bring
    the front desk back. No-op (logged) when nothing is serving."""
    state = read_desk_state()
    served = local_server.served_model_ids()
    if served is None:
        log_event("step_aside_noop", reason=reason, detail="no server answering")
        state["aside"] = True
        state["reason"] = reason
        _write_desk_state(state)
        return {"aside": True, "was_serving": None}
    current = served[0] if served else None
    parked = None
    bound = state.get("parked_thread")
    if bound and current == front_desk_model():
        parked = park_thread(str(bound))
    server_state = local_server.read_server_state()
    restore: Dict[str, Any] = {"model": current}
    if server_state.get("managed_by") != "hydra":
        pid = local_server.listener_pid()
        if pid:
            restore["cmdline"] = local_server.process_cmdline(pid)
    stopped = local_server.stop_local_server()
    state["aside"] = True
    state["reason"] = reason
    state["restore"] = restore
    state["parked_kv"] = parked
    state["since"] = int(time.time())
    _write_desk_state(state)
    log_event("step_aside", reason=reason, was_serving=current, stopped=stopped.get("stopped"),
              parked_kv=(parked or {}).get("file") if parked else None)
    return {"aside": True, "was_serving": current, "stopped": stopped, "parked_kv": parked}


def return_desk() -> Dict[str, Any]:
    """Bring the front desk back after a step-aside."""
    state = read_desk_state()
    restore = _restore_target(state) or {"model": front_desk_model()}
    model = restore.get("model") or front_desk_model()
    result: Dict[str, Any] = {"model": model}
    served = local_server.served_model_ids()
    if served and model in served:
        result["server"] = "already-running"
    elif restore.get("cmdline"):
        # A foreign server Hydra did not start: relaunch its captured
        # command line verbatim, hidden, exactly as it was found.
        flags = 0x08000000 | 0x00000008 if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                restore["cmdline"], shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
            )
            result["server"] = "relaunched-foreign"
            result["pid"] = proc.pid
        except Exception as exc:
            result["server"] = "relaunch-failed"
            result["reason"] = f"{type(exc).__name__}: {exc}"
    else:
        result.update(local_server.ensure_local_server(model))
    # Wait until the restored server actually answers. Without this, the
    # port sits silent-but-coming-back and any concurrent Hydra launch's
    # autostart races in and starts the default model on top of the
    # restore (observed live 2026-11-01: a `hydra desk status` autostart
    # out-bound a relaunched Bonsai and the Bonsai died on the bind).
    wait_started = time.time()
    while time.time() - wait_started < 120:
        served_now = local_server.served_model_ids()
        if served_now and model in served_now:
            result["answered_after_s"] = round(time.time() - wait_started, 1)
            break
        time.sleep(1.0)
    else:
        result["wait"] = "restored server did not answer within 120s"
        log_event("return_unanswered", model=model)
    parked = state.get("parked_kv")
    if parked and isinstance(parked, dict) and parked.get("file"):
        if local_server.served_model_ids() and model == (parked.get("model") or model):
            result["kv_restore"] = restore_thread(str(parked["file"]))
    state["aside"] = False
    state["reason"] = None
    state["restore"] = None
    state["parked_kv"] = None
    _write_desk_state(state)
    log_event("return", model=model, server=result.get("server"))
    return result


@contextlib.contextmanager
def local_model_session(model_id: str, kind: str = "local summon") -> Iterator[Dict[str, Any]]:
    """Serve `model_id` on the local endpoint for the block, stepping the
    desk aside first when another model holds the seat, and returning the
    desk afterwards. With step-aside disabled or the seat already correct,
    the block runs against whatever is serving (and the caller's own
    model check reports any mismatch, as before)."""
    info: Dict[str, Any] = {"model": model_id, "switched": False}
    served = local_server.served_model_ids()
    if served is not None and model_id in served:
        info["basis"] = "already-serving"
        yield info
        return
    if not step_aside_enabled():
        info["basis"] = "step-aside disabled (override)"
        yield info
        return
    stepped = False
    if served is not None:
        step_aside(f"{kind}: {model_id} needs the GPU")
        stepped = True
    status = local_server.ensure_local_server(model_id)
    info["ensure"] = status
    info["switched"] = stepped or status.get("server") == "started"
    info["basis"] = "served by arbiter"
    try:
        yield info
    finally:
        if stepped:
            # The desk was displaced for this job: put it back.
            local_server.stop_local_server()
            return_desk()
        elif info["switched"]:
            # Cold seat: the job's model stays serving (it holds the seat
            # now); nothing was displaced, so nothing is restored.
            log_event("session_cold_start", model=model_id, kind=kind)


@contextlib.contextmanager
def gpu_job(kind: str) -> Iterator[Dict[str, Any]]:
    """Step the desk aside for a non-llama GPU job (image generation),
    and return it when the job ends."""
    info: Dict[str, Any] = {"kind": kind, "stepped": False}
    served = local_server.served_model_ids()
    if served is None or not step_aside_enabled():
        info["basis"] = "no desk to move" if served is None else "step-aside disabled (override)"
        yield info
        return
    step_aside(kind)
    info["stepped"] = True
    info["basis"] = "desk stepped aside"
    try:
        yield info
    finally:
        return_desk()


# ------------------------------------------------------------ context tiers

def park_thread(name: str, slot: int = 0) -> Dict[str, Any]:
    """Tier 2/3: save a slot's KV cache to the kv-cache folder and record
    its real size. The file is the parked state; its bytes are what RAM
    must hold to keep the thread hot-swappable."""
    safe = "".join(c for c in name if c.isalnum() or c in "-_.") or "thread"
    filename = f"{safe}.kv.bin"
    info = local_server.slot_info()
    slots = info if isinstance(info, list) else []
    slot_view = slots[slot] if slot < len(slots) else {}
    result = local_server.save_slot(slot, filename)
    path = Path(local_server.kv_cache_dir()) / filename
    size = path.stat().st_size if path.exists() else None
    record = {
        "file": filename,
        "path": str(path),
        "bytes": size,
        "model": (local_server.served_model_ids() or [None])[0],
        "slot": slot,
        "n_tokens": slot_view.get("n_ctx") if isinstance(slot_view, dict) else None,
        "server_response": result,
        "parked_at": int(time.time()),
    }
    log_event("park", file=filename, bytes=size, model=record["model"])
    return record


def restore_thread(filename: str, slot: int = 0) -> Dict[str, Any]:
    """Load a parked KV file back into a slot. Only valid into the same
    model + quant + context configuration that saved it; the server
    refuses otherwise and that refusal is surfaced, not hidden."""
    result = local_server.restore_slot(slot, filename)
    log_event("restore", file=filename, ok=result is not None)
    return {"file": filename, "slot": slot, "server_response": result, "restored": result is not None}


def bind_parked_thread(name: Optional[str]) -> Dict[str, Any]:
    """Bind (or unbind, with None) the thread step-aside auto-parks."""
    state = read_desk_state()
    state["parked_thread"] = name
    _write_desk_state(state)
    log_event("bind_park", thread=name)
    return desk_status()
