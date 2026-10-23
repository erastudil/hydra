"""Local Bonsai server autostart + warm-up.

Hydra's local agent is a Prism Ternary-Bonsai GGUF served by the Prism
fork of llama.cpp (``llama-server``). The ternary Q2_0 weights produce
garbage on stock llama.cpp/Ollama builds, so Hydra must serve them with
the Prism fork binaries, on the llama.cpp endpoint Hydra already probes
(``LLAMACPP_HOST``, default http://127.0.0.1:8080).

``cli.main()`` calls :func:`autostart_local_async` on every launch, so
starting any Hydra front end (the Rust TUI's ``python -m hydra_cli
stdio`` backend, the terminal REPL, a one-shot command) also:

1. starts the Prism ``llama-server`` for the configured local model
   (``HYDRA_LOCAL_MODEL``, default the catalog's ``default_local_model``)
   if nothing is answering on the llama.cpp endpoint, and
2. warms it with one minimal real generation, so the first user prompt
   is not a cold model load.

Everything here is best-effort and failure-silent: a machine without the
binaries or the weights simply reports a status and Hydra runs exactly
as before. Set ``HYDRA_LOCAL_AUTOSTART=0`` to opt out.

Overrides (env):
  HYDRA_LOCAL_AUTOSTART  "0" disables autostart entirely
  HYDRA_LOCAL_MODEL      model id to serve (default: catalog default)
  HYDRA_MODELS_DIR       folder holding bonsai-2-<size>/ model folders
  HYDRA_LLAMA_SERVER     path to the Prism fork llama-server binary
  HYDRA_LOCAL_QUANT      GGUF quant variant to serve (default Q2_0)
  HYDRA_LOCAL_CTX        context size for the server (default 131072)
  HYDRA_LOCAL_WARM       "0" starts the server without the warm-up call
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_ATTEMPTED = False

_WARM_PROMPT = "Reply with exactly: HYDRA WARM OK"


def _log(message: str) -> None:
    """One status line to stderr (never the stdio protocol stream: the
    adapter moves real stdout aside, and stderr is /dev/null there)."""
    try:
        sys.stderr.write(f"local-server: {message}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _fetch_json(url: str, timeout: float = 2.0) -> Optional[Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _post_json(url: str, payload: Dict[str, Any], timeout: float) -> Optional[Any]:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _llamacpp_base() -> str:
    base = os.environ.get("LLAMACPP_HOST", "").strip() or "http://127.0.0.1:8080"
    if not base.startswith("http"):
        base = f"http://{base}"
    return base.rstrip("/")


def _served_model_ids(base: str) -> Optional[list]:
    """Model ids the llama.cpp endpoint currently serves, or None if it
    is not answering at all."""
    data = _fetch_json(f"{base}/v1/models", timeout=2.0)
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [str(e.get("id")) for e in data["data"] if isinstance(e, dict) and e.get("id")]
    return None


def _models_dir() -> Optional[str]:
    env = os.environ.get("HYDRA_MODELS_DIR", "").strip()
    if env:
        return env if os.path.isdir(env) else None
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Documents", "hydra-models"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "hydra-models"),
        os.path.join(home, "hydra-models"),
    ]
    for cand in candidates:
        cand = os.path.normpath(cand)
        if os.path.isdir(cand):
            return cand
    return None


def _server_binary() -> Optional[str]:
    env = os.environ.get("HYDRA_LLAMA_SERVER", "").strip()
    if env:
        return env if os.path.isfile(env) else None
    home = os.path.expanduser("~")
    candidates = [
        # Prism fork, build 10787 — the runtime the Bonsai fit test pinned.
        os.path.join(home, "Documents", "llama-prism", "llama-server.exe"),
        os.path.join(home, "Documents", "models", "bin", "prism-llama", "llama-server.exe"),
        os.path.join(home, "Documents", "llama-prism", "llama-server"),
        os.path.join(home, "Documents", "models", "bin", "prism-llama", "llama-server"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    import shutil

    return shutil.which("llama-server")


def _model_file(model_id: str, models_dir: str) -> Optional[str]:
    """Map a catalog model id (prism-ml/Ternary-Bonsai-2-9B-gguf) to its
    GGUF on disk: <models_dir>/bonsai-2-9b/Ternary-Bonsai-2-9B-Q2_0.gguf"""
    match = re.search(r"Ternary-Bonsai-2-(\d+B)-gguf", model_id or "")
    if not match:
        return None
    size = match.group(1)  # e.g. "9B"
    quant = os.environ.get("HYDRA_LOCAL_QUANT", "").strip() or "Q2_0"
    path = os.path.join(
        models_dir, f"bonsai-2-{size.lower()}", f"Ternary-Bonsai-2-{size}-{quant}.gguf"
    )
    return path if os.path.isfile(path) else None


def warm_model(base: str, model_id: str, timeout: float = 300.0) -> Dict[str, Any]:
    """One minimal real generation through the server's chat API. This
    is the warm-up: it exercises tokenizer, template and sampler so the
    first user prompt does not pay the cold path."""
    started = time.time()
    data = _post_json(
        f"{base}/v1/chat/completions",
        {
            "model": model_id,
            "messages": [{"role": "user", "content": _WARM_PROMPT}],
            "max_tokens": 96,
            "temperature": 0,
        },
        timeout=timeout,
    )
    elapsed = time.time() - started
    content = ""
    reasoning = ""
    if isinstance(data, dict):
        try:
            message = data["choices"][0]["message"]
            content = str(message.get("content") or "").strip()
            reasoning = str(message.get("reasoning_content") or "").strip()
        except Exception:
            content = ""
    return {
        "warmed": bool(content or reasoning),
        "seconds": round(elapsed, 2),
        "content": content,
        "reasoning": reasoning[:120],
    }


def ensure_local_server(model_id: Optional[str] = None, warm: bool = True) -> Dict[str, Any]:
    """Start the Prism llama-server for the local model if the llama.cpp
    endpoint is silent, then warm it. Idempotent; never raises."""
    status: Dict[str, Any] = {"base": _llamacpp_base()}
    try:
        base = status["base"]
        if model_id is None:
            from hydra_cli.config import DEFAULT_LOCAL_MODEL

            model_id = DEFAULT_LOCAL_MODEL
        status["model"] = model_id

        served = _served_model_ids(base)
        if served is not None:
            status["server"] = "already-running"
            status["served"] = served
            if model_id in served:
                # The model is already loaded and hot: it was exercised by
                # the warm generation when its server started. Re-warming
                # on every process launch fans probe generations onto one
                # slot under parallel spawns (the stdio gate runs hundreds
                # of concurrent backends), so report the state, don't probe.
                status["warm"] = {"warmed": True, "basis": "already-serving"}
            else:
                status["warm"] = {"warmed": False, "skipped": "different model is loaded"}
            return status

        models_dir = _models_dir()
        if not models_dir:
            status["server"] = "unavailable"
            status["reason"] = "no hydra-models directory found (set HYDRA_MODELS_DIR)"
            return status
        gguf = _model_file(model_id, models_dir)
        if not gguf:
            status["server"] = "unavailable"
            status["reason"] = f"model file for {model_id} not present under {models_dir}"
            return status
        binary = _server_binary()
        if not binary:
            status["server"] = "unavailable"
            status["reason"] = "Prism fork llama-server binary not found (set HYDRA_LLAMA_SERVER)"
            return status

        hostport = base.split("://", 1)[-1]
        host, _, port = hostport.partition(":")
        ctx = os.environ.get("HYDRA_LOCAL_CTX", "").strip() or "131072"
        log_dir = os.path.join(models_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"llama-server-{model_id.rsplit('/', 1)[-1]}.log")
        log_handle = open(log_path, "ab")
        args = [
            binary,
            "-m", gguf,
            "--host", host or "127.0.0.1",
            "--port", port or "8080",
            "--alias", model_id,
            "-c", ctx,
            "-ngl", "99",
        ]
        flags = 0
        if os.name == "nt":
            # CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB.
            # The breakaway matters: a parent inside a job object (an SSH
            # session, a CI runner) would otherwise take the server down
            # with it when the session ends. If the job forbids breakaway,
            # CreateProcess fails outright — retry without it.
            flags = 0x08000000 | 0x00000008 | 0x01000000
        try:
            proc = subprocess.Popen(
                args,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
        except OSError:
            if not flags:
                raise
            proc = subprocess.Popen(
                args,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=flags & ~0x01000000,
                close_fds=True,
            )
        status["server"] = "started"
        status["pid"] = proc.pid
        status["binary"] = binary
        status["gguf"] = gguf
        status["log"] = log_path

        deadline = time.time() + 240
        while time.time() < deadline:
            if proc.poll() is not None:
                status["server"] = "failed"
                status["reason"] = f"llama-server exited with code {proc.returncode}; see {log_path}"
                return status
            if _served_model_ids(base) is not None:
                break
            time.sleep(1.0)
        else:
            status["server"] = "failed"
            status["reason"] = f"llama-server did not answer within 240s; see {log_path}"
            return status

        if warm and os.environ.get("HYDRA_LOCAL_WARM", "").strip() != "0":
            status["warm"] = warm_model(base, model_id)
        else:
            status["warm"] = {"warmed": False, "skipped": True}
        return status
    except Exception as exc:  # autostart must never break the host app
        status["server"] = "error"
        status["reason"] = f"{type(exc).__name__}: {exc}"
        return status


def autostart_local_async() -> None:
    """Fire-and-forget startup hook for cli.main(): ensures the local
    Bonsai server is up and warm, in a daemon thread, once per process."""
    global _ATTEMPTED
    if os.environ.get("HYDRA_LOCAL_AUTOSTART", "").strip() == "0":
        return
    with _LOCK:
        if _ATTEMPTED:
            return
        _ATTEMPTED = True

    def _run() -> None:
        status = ensure_local_server()
        server = status.get("server")
        if server in ("started", "already-running"):
            warm = status.get("warm") or {}
            _log(
                f"{server}: {status.get('model')} at {status.get('base')} "
                f"(warm={warm.get('warmed')}, {warm.get('seconds', '?')}s)"
            )
        else:
            _log(f"{server}: {status.get('reason', 'local model not started')}")

    threading.Thread(target=_run, name="hydra-local-autostart", daemon=True).start()
