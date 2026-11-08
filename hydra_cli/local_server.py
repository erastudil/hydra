"""Local model server autostart + warm-up.

Two local models are servable, each with the binary its weights require:

- The front desk, Gemma 4 E4B (the catalog's ``default_local_model``),
  served by STOCK llama.cpp with its mmproj projector, at 128K context
  with q8_0 KV caches (the server-v4 configuration the driver bench
  validated). When W2's trained Hydra adapter GGUF is present it is
  loaded with ``--lora``; until then the catalog alias runs the base
  weights and the stdio/tool format shim carries the turn envelope.
- The Bonsai coders, Prism Ternary-Bonsai GGUFs served by the Prism fork
  of llama.cpp. The ternary Q2_0 weights produce garbage on stock
  llama.cpp/Ollama builds, so Hydra must serve them with the Prism fork
  binaries.

Both serve on the llama.cpp endpoint Hydra already probes
(``LLAMACPP_HOST``, default http://127.0.0.1:8080) — one model at a
time, which is why gpu_arbiter exists: stepping between them is a
designed, logged operation, not an accident. Servers Hydra starts are
recorded in ~/.hydra/local-server-state.json, and Hydra-started servers
run with ``--slot-save-path ~/.hydra/kv-cache`` so thread KV state can
be parked to disk (tier 2/3 of the context design; see gpu_arbiter).

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
_ENSURE_LOCK = threading.RLock()
_ATTEMPTED = False

DEFAULT_MIN_CTX = 131072
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


def state_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".hydra", "local-server-state.json")


def read_server_state() -> Dict[str, Any]:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_server_state(state: Dict[str, Any]) -> None:
    try:
        path = state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception:
        pass


def kv_cache_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".hydra", "kv-cache")
    os.makedirs(path, exist_ok=True)
    return path


def _stock_server_binary() -> Optional[str]:
    """Stock llama.cpp for the Gemma front desk (the Prism fork is only
    for the ternary Bonsai weights)."""
    env = os.environ.get("HYDRA_LLAMA_SERVER_STOCK", "").strip()
    if env:
        return env if os.path.isfile(env) else None
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "hydra", "llama.cpp", "llama-server.exe"),
        os.path.join(home, "hydra", "llama.cpp", "llama-server"),
        os.path.join(home, "Documents", "llama.cpp", "llama-server.exe"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def _gemma_adapter(models_dir: str) -> Optional[str]:
    """The trained Hydra adapter GGUF for Gemma E4B, when it exists.

    TODO(hydra-pass W2): the E4B Hydra adapter is trained on Modal from
    ~/workspace/hydra-driver-bench/adapter-dataset/. When the artifact
    lands, either drop the GGUF in <models_dir>/gemma-4-e4b/ with
    'adapter' or 'hydra' in its file name, point HYDRA_GEMMA_E4B_LORA at
    it, or publish it and set the gemma-e4b catalog alias's
    hydra_adapter field. Until then this returns None and the alias runs
    base weights through the format shim (stdio_adapter/tool_adapter).
    """
    env = os.environ.get("HYDRA_GEMMA_E4B_LORA", "").strip()
    if env:
        return env if os.path.isfile(env) else None
    folder = os.path.join(models_dir, "gemma-4-e4b")
    try:
        for name in sorted(os.listdir(folder)):
            low = name.lower()
            if low.endswith(".gguf") and ("adapter" in low or "hydra" in low):
                return os.path.join(folder, name)
    except Exception:
        pass
    return None


def _servable(model_id: str, models_dir: str) -> Optional[Dict[str, Any]]:
    """Launch spec for a model id, or None when Hydra cannot serve it."""
    if re.search(r"Ternary-Bonsai-2-\d+B-gguf", model_id or ""):
        gguf = _model_file(model_id, models_dir)
        binary = _server_binary()
        if not gguf or not binary:
            return None
        return {"binary": binary, "gguf": gguf, "extra_args": [], "kind": "bonsai-prism"}
    gemma = {
        "gemma-e4b": ("gemma-4-e4b", "gemma-4-E4B-it-Q4_K_M.gguf", "mmproj-F16.gguf"),
        "gemma-e2b": ("gemma-4-e2b", "gemma-4-E2B-it-Q4_0.gguf", "mmproj-gemma-4-E2B-it-Q8_0.gguf"),
    }.get(model_id)
    if gemma:
        folder, gguf_name, mmproj_name = gemma
        gguf = os.path.join(models_dir, folder, gguf_name)
        binary = _stock_server_binary()
        if not binary or not os.path.isfile(gguf):
            return None
        extra = [
            "--flash-attn", "on",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "--image-max-tokens", "1024",
            "--slot-save-path", kv_cache_dir(),
        ]
        mmproj = os.path.join(models_dir, folder, mmproj_name)
        if os.path.isfile(mmproj):
            extra += ["--mmproj", mmproj]
        adapter = _gemma_adapter(models_dir) if model_id == "gemma-e4b" else None
        if adapter:
            extra += ["--lora", adapter]
        return {"binary": binary, "gguf": gguf, "extra_args": extra, "kind": "gemma-stock", "adapter": adapter}
    return None


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


def required_context(model_id: Optional[str] = None) -> int:
    """Required minimum context window for the managed local server."""
    env = os.environ.get("HYDRA_LOCAL_CTX", "").strip()
    if env:
        try:
            val = int(env)
            if val > 0:
                return val
        except ValueError:
            pass
    try:
        from hydra_cli import modes

        for entry in modes.routes("local"):
            if isinstance(entry, dict) and entry.get("model") == model_id:
                ctx = entry.get("context")
                if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0:
                    return ctx
    except Exception:
        pass
    return DEFAULT_MIN_CTX


def server_props(base: Optional[str] = None, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """Fetch GET <base>/props from the llama.cpp server."""
    target = (base or _llamacpp_base()).rstrip("/")
    data = _fetch_json(f"{target}/props", timeout=timeout)
    return data if isinstance(data, dict) else None


def extract_props_n_ctx(props: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract n_ctx from llama.cpp /props JSON response."""
    if not isinstance(props, dict):
        return None
    top_ctx = props.get("n_ctx")
    if isinstance(top_ctx, int) and not isinstance(top_ctx, bool):
        return top_ctx
    default_settings = props.get("default_generation_settings")
    if isinstance(default_settings, dict):
        nested_ctx = default_settings.get("n_ctx")
        if isinstance(nested_ctx, int) and not isinstance(nested_ctx, bool):
            return nested_ctx
        params = default_settings.get("params")
        if isinstance(params, dict):
            param_ctx = params.get("n_ctx")
            if isinstance(param_ctx, int) and not isinstance(param_ctx, bool):
                return param_ctx
    return None


def verify_server_context(base: Optional[str] = None, min_ctx: Optional[int] = None) -> Dict[str, Any]:
    """Verify the running llama.cpp server's actual n_ctx via GET /props."""
    target = (base or _llamacpp_base()).rstrip("/")
    expected = int(min_ctx) if min_ctx is not None else required_context()
    props = server_props(target)
    if props is None:
        return {
            "ok": False,
            "base": target,
            "n_ctx": None,
            "expected_n_ctx": expected,
            "reason": f"GET {target}/props did not return valid JSON",
        }
    actual = extract_props_n_ctx(props)
    if actual is None:
        return {
            "ok": False,
            "base": target,
            "n_ctx": None,
            "expected_n_ctx": expected,
            "reason": f"GET {target}/props missing n_ctx field",
        }
    if actual < expected:
        return {
            "ok": False,
            "base": target,
            "n_ctx": actual,
            "expected_n_ctx": expected,
            "reason": f"server n_ctx is {actual}, expected >= {expected}",
        }
    return {
        "ok": True,
        "base": target,
        "n_ctx": actual,
        "expected_n_ctx": expected,
        "model_alias": props.get("model_alias"),
        "total_slots": props.get("total_slots"),
    }


def ensure_local_server(
    model_id: Optional[str] = None,
    warm: bool = True,
    show_progress: bool = False,
    progress_stream: Optional[Any] = None,
) -> Dict[str, Any]:
    """Start the managed llama-server for the local model if the llama.cpp
    endpoint is silent, verify n_ctx via /props, then warm it.
    Idempotent; never raises."""
    stream = progress_stream if progress_stream is not None else sys.stderr

    def _emit_progress(msg: str) -> None:
        if not show_progress:
            return
        try:
            stream.write(f"[local] {msg}\n")
            stream.flush()
        except Exception:
            pass

    with _ENSURE_LOCK:
        status: Dict[str, Any] = {"base": _llamacpp_base()}
        try:
            base = status["base"]
            if model_id is None:
                env_model = os.environ.get("HYDRA_LOCAL_MODEL", "").strip()
                if env_model:
                    model_id = env_model
                else:
                    try:
                        from hydra_cli import modes

                        if modes.current_mode() == "local":
                            model_id = modes.default_alias("local")
                    except Exception:
                        pass
                if not model_id:
                    from hydra_cli.config import DEFAULT_LOCAL_MODEL

                    model_id = DEFAULT_LOCAL_MODEL
            status["model"] = model_id
            min_ctx = required_context(model_id)
            status["expected_n_ctx"] = min_ctx

            served = _served_model_ids(base)
            if served is not None:
                status["served"] = served
                if model_id in served:
                    ctx_check = verify_server_context(base, min_ctx=min_ctx)
                    status["props_check"] = ctx_check
                    status["n_ctx"] = ctx_check.get("n_ctx")
                    if not ctx_check["ok"]:
                        stop_local_server()
                        reason = f"context mismatch from {base}/props: {ctx_check['reason']} (server stopped)"
                        _emit_progress(f"ERROR: {reason}")
                        status["server"] = "failed"
                        status["reason"] = reason
                        return status

                    # Heal stale pid in local-server-state.json if needed
                    state = read_server_state()
                    live_pid = listener_pid()
                    if live_pid and state.get("pid") != live_pid:
                        updated = dict(state) if state else {"managed_by": "hydra"}
                        updated["pid"] = live_pid
                        updated["model"] = model_id
                        updated["base"] = base
                        updated["n_ctx"] = ctx_check["n_ctx"]
                        updated["warmed"] = True
                        _write_server_state(updated)
                        status["pid"] = live_pid
                    elif isinstance(state.get("pid"), int):
                        status["pid"] = state["pid"]

                    status["server"] = "already-running"
                    # The model is already loaded and hot: it was exercised by
                    # the warm generation when its server started. Re-warming
                    # on every process launch fans probe generations onto one
                    # slot under parallel spawns (the stdio gate runs hundreds
                    # of concurrent backends), so report the state, don't probe.
                    status["warm"] = {"warmed": True, "basis": "already-serving"}
                    return status
                else:
                    # A different model is currently loaded on the single local port;
                    # stop it so the requested model can be started cleanly.
                    _emit_progress(f"Stopping {served[0]} on {base} to serve {model_id}...")
                    stop_local_server()

            if os.environ.get("HYDRA_LOCAL_AUTOSTART", "").strip() == "0":
                reason = f"local server not running on {base} (HYDRA_LOCAL_AUTOSTART=0)"
                _emit_progress(f"ERROR: {reason}")
                status["server"] = "unavailable"
                status["reason"] = reason
                return status

            models_dir = _models_dir()
            spec = _servable(model_id, models_dir) if models_dir else None
            if not spec:
                # Fallback to recorded Hydra-managed server state if weights/binary exist on disk
                state = read_server_state()
                if (
                    state.get("managed_by") == "hydra"
                    and state.get("model") == model_id
                    and isinstance(state.get("binary"), str)
                    and os.path.isfile(state["binary"])
                    and isinstance(state.get("gguf"), str)
                    and os.path.isfile(state["gguf"])
                ):
                    spec = {
                        "binary": state["binary"],
                        "gguf": state["gguf"],
                        "extra_args": [
                            "--flash-attn", "on",
                            "--cache-type-k", "q8_0",
                            "--cache-type-v", "q8_0",
                            "--image-max-tokens", "1024",
                            "--slot-save-path", kv_cache_dir(),
                        ],
                        "adapter": state.get("adapter"),
                    }
            if not spec:
                if not models_dir:
                    reason = "no hydra-models directory found (set HYDRA_MODELS_DIR)"
                else:
                    reason = f"no servable binary/weights for {model_id} under {models_dir}"
                _emit_progress(f"ERROR: {reason}")
                status["server"] = "unavailable"
                status["reason"] = reason
                return status

            gguf = spec["gguf"]
            binary = spec["binary"]
            if spec.get("adapter"):
                status["adapter"] = spec["adapter"]

            hostport = base.split("://", 1)[-1]
            host, _, port = hostport.partition(":")
            ctx = str(min_ctx)
            log_root = models_dir or os.path.join(os.path.expanduser("~"), ".hydra")
            log_dir = os.path.join(log_root, "logs")
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
            ] + list(spec.get("extra_args") or [])

            cold_started_at = time.time()
            _emit_progress(
                f"Cold start: launching managed llama-server for {model_id} on {base} (context {ctx})..."
            )

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
            state_record: Dict[str, Any] = {
                "pid": proc.pid,
                "model": model_id,
                "binary": binary,
                "gguf": gguf,
                "cmdline": args,
                "base": base,
                "adapter": spec.get("adapter"),
                "started_at": int(time.time()),
                "managed_by": "hydra",
                "expected_n_ctx": min_ctx,
                "warmed": False,
            }
            _write_server_state(state_record)

            deadline = cold_started_at + 240
            last_report = cold_started_at
            reported_once = False
            served_after_start: Optional[list] = None
            while time.time() < deadline:
                if proc.poll() is not None:
                    reason = f"llama-server exited with code {proc.returncode}; see {log_path}"
                    _emit_progress(f"ERROR: {reason}")
                    status["server"] = "failed"
                    status["reason"] = reason
                    return status
                served_after_start = _served_model_ids(base)
                if served_after_start is not None:
                    break
                now = time.time()
                elapsed = now - cold_started_at
                if show_progress and ((not reported_once and elapsed >= 0.8) or (now - last_report >= 2.0)):
                    _emit_progress(f"Starting {model_id} (loading weights)... {elapsed:.1f}s elapsed")
                    last_report = now
                    reported_once = True
                time.sleep(0.4)
            else:
                reason = f"llama-server did not answer within 240s; see {log_path}"
                _emit_progress(f"ERROR: {reason}")
                status["server"] = "failed"
                status["reason"] = reason
                return status

            status["served"] = served_after_start
            if model_id not in (served_after_start or []):
                stop_local_server()
                reason = f"llama-server started on {base} but is serving {served_after_start}, expected {model_id}"
                _emit_progress(f"ERROR: {reason}")
                status["server"] = "failed"
                status["reason"] = reason
                return status

            ctx_check = verify_server_context(base, min_ctx=min_ctx)
            status["props_check"] = ctx_check
            status["n_ctx"] = ctx_check.get("n_ctx")
            if not ctx_check["ok"]:
                stop_local_server()
                reason = f"context mismatch from {base}/props: {ctx_check['reason']} (server stopped)"
                _emit_progress(f"ERROR: {reason}")
                status["server"] = "failed"
                status["reason"] = reason
                return status

            boot_elapsed = time.time() - cold_started_at
            _emit_progress(
                f"Server listening on {base} (pid {proc.pid}, model={model_id}, "
                f"n_ctx={ctx_check['n_ctx']} verified via /props in {boot_elapsed:.1f}s)."
            )

            if warm and os.environ.get("HYDRA_LOCAL_WARM", "").strip() != "0":
                warm_started = time.time()
                _emit_progress(
                    f"Warming {model_id} (initial generation)... {warm_started - cold_started_at:.1f}s elapsed"
                )
                warm_box: Dict[str, Any] = {}

                def _do_warm() -> None:
                    warm_box["res"] = warm_model(base, model_id)

                w_thread = threading.Thread(target=_do_warm, name="hydra-local-warm", daemon=True)
                w_thread.start()
                last_warm_report = time.time()
                while w_thread.is_alive():
                    w_thread.join(timeout=0.4)
                    now = time.time()
                    if w_thread.is_alive() and show_progress and (now - last_warm_report >= 2.0):
                        _emit_progress(
                            f"Warming {model_id} (initial generation)... {now - cold_started_at:.1f}s elapsed"
                        )
                        last_warm_report = now

                warm_res = warm_box.get("res") or {"warmed": False, "seconds": round(time.time() - warm_started, 2)}
                status["warm"] = warm_res
                if not warm_res.get("warmed"):
                    reason = f"warm-up generation failed for {model_id} on {base}"
                    _emit_progress(f"ERROR: {reason}")
                    status["server"] = "failed"
                    status["reason"] = reason
                    return status
                state_record["n_ctx"] = ctx_check["n_ctx"]
                state_record["warmed"] = True
                _write_server_state(state_record)
                total_elapsed = time.time() - cold_started_at
                _emit_progress(
                    f"Ready: {model_id} warm in {warm_res['seconds']:.1f}s "
                    f"(total cold start {total_elapsed:.1f}s, n_ctx={ctx_check['n_ctx']})."
                )
            else:
                state_record["n_ctx"] = ctx_check["n_ctx"]
                _write_server_state(state_record)
                status["warm"] = {"warmed": False, "skipped": True}
            return status
        except Exception as exc:  # autostart must never break the host app
            _emit_progress(f"ERROR: {type(exc).__name__}: {exc}")
            status["server"] = "error"
            status["reason"] = f"{type(exc).__name__}: {exc}"
            return status


def served_model_ids() -> Optional[list]:
    return _served_model_ids(_llamacpp_base())


def listener_pid() -> Optional[int]:
    """PID of the process listening on the llama.cpp port (Windows via
    Get-NetTCPConnection; the arbiter uses it to identify a server Hydra
    did not start)."""
    base = _llamacpp_base()
    hostport = base.split("://", 1)[-1]
    _, _, port = hostport.partition(":")
    port = port or "8080"
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
                 "-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000,
            )
            text = (out.stdout or "").strip()
            return int(text) if text.isdigit() else None
        except Exception:
            return None
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=10)
        for line in (out.stdout or "").splitlines():
            if f":{port} " in line:
                match = re.search(r"pid=(\d+)", line)
                if match:
                    return int(match.group(1))
    except Exception:
        pass
    return None


def process_cmdline(pid: int) -> Optional[str]:
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").CommandLine"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000,
            )
            text = (out.stdout or "").strip()
            return text or None
        except Exception:
            return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def _pid_alive(pid: Optional[int]) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _kill_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/F"],
                capture_output=True, text=True, timeout=20,
                creationflags=0x08000000,
            )
            return out.returncode == 0
        import signal

        os.kill(int(pid), signal.SIGTERM)
        return True
    except Exception:
        return False


def stop_local_server(timeout: float = 30.0) -> Dict[str, Any]:
    """Stop the llama.cpp server on the endpoint, whoever started it.
    Returns what was stopped. Used by gpu_arbiter's step-aside."""
    base = _llamacpp_base()
    state = read_server_state()
    recorded_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
    live_listener = listener_pid()
    pids_to_kill: list[int] = []
    if recorded_pid and _pid_alive(recorded_pid):
        pids_to_kill.append(recorded_pid)
    if live_listener and live_listener not in pids_to_kill:
        pids_to_kill.append(live_listener)

    pid = live_listener or (recorded_pid if _pid_alive(recorded_pid) else None)
    result: Dict[str, Any] = {"base": base, "pid": pid, "stopped": False}
    if not pids_to_kill and _served_model_ids(base) is None:
        if state and state.get("pid") is not None:
            preserved = dict(state)
            preserved["pid"] = None
            preserved["warmed"] = False
            preserved["stopped_at"] = int(time.time())
            _write_server_state(preserved)
        result["reason"] = "no listener found"
        return result

    killed_any = False
    for target_pid in pids_to_kill:
        if result.get("cmdline") is None:
            result["cmdline"] = process_cmdline(target_pid)
        if _kill_pid(target_pid):
            killed_any = True
    result["killed"] = killed_any

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _served_model_ids(base) is None:
            result["stopped"] = True
            break
        extra_pid = listener_pid()
        if extra_pid and extra_pid not in pids_to_kill:
            pids_to_kill.append(extra_pid)
            _kill_pid(extra_pid)
        time.sleep(0.5)
    if result["stopped"]:
        if state:
            preserved = dict(state)
            preserved["pid"] = None
            preserved["warmed"] = False
            preserved["stopped_at"] = int(time.time())
            _write_server_state(preserved)
        else:
            _write_server_state({})
    return result


def slot_info() -> Optional[Any]:
    return _fetch_json(f"{_llamacpp_base()}/slots", timeout=3.0)


def save_slot(slot: int, filename: str) -> Optional[Any]:
    """Park a slot's KV cache to the server's --slot-save-path directory
    (llama.cpp slot save/restore: the tier-2/3 context mechanism)."""
    return _post_json(
        f"{_llamacpp_base()}/slots/{int(slot)}?action=save",
        {"filename": filename},
        timeout=300.0,
    )


def restore_slot(slot: int, filename: str) -> Optional[Any]:
    return _post_json(
        f"{_llamacpp_base()}/slots/{int(slot)}?action=restore",
        {"filename": filename},
        timeout=300.0,
    )


def erase_slot(slot: int) -> Optional[Any]:
    return _post_json(
        f"{_llamacpp_base()}/slots/{int(slot)}?action=erase", {}, timeout=30.0
    )


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
