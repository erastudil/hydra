"""Alice mind transport for Hydra.

topic : client for the alice cognitive engine runtime at snowgate-alice/mind.

comment : three transports in priority order. ALICE_URL selects http against bin/serve.js. otherwise a persistent node subprocess runs bin/alice.js --stdio and exchanges one json frame per line. absent node or absent mind yields None so callers fall back to alice_core.js.

env : ALICE_URL, ALICE_TOKEN, ALICE_MIND_JS, ALICE_HOME, ALICE_ENGINE (mind | core), NODE_BIN.
"""

import atexit
import itertools
import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# reason : routes that carry a cited or proven answer; hosts may print them without a model.
FINAL_ROUTE_PREFIXES = ("CITED_", "FORMAL_")
FINAL_ROUTES = {"SOCIAL", "SELF_DESCRIPTION", "INVENTORY", "LEARNING_INGESTION", "IDLE"}


def is_final(result: Optional[Dict[str, Any]]) -> bool:
    """Return True when a mind result holds a cited, proven, or social answer that needs no model."""
    if not result:
        return False
    route = str(result.get("mindRoute") or result.get("route") or "")
    if result.get("escalate"):
        return False
    return route.startswith(FINAL_ROUTE_PREFIXES) or route in FINAL_ROUTES


def engine_mode() -> str:
    return (os.environ.get("ALICE_ENGINE") or "mind").strip().lower()


def find_alice_mind() -> Optional[str]:
    """Locate mind/bin/alice.js from env or known workspace locations."""
    env_path = os.environ.get("ALICE_MIND_JS", "").strip()
    if env_path and os.path.isfile(env_path):
        return os.path.abspath(env_path)
    here = os.path.dirname(os.path.abspath(__file__))
    homes = []
    env_home = os.environ.get("ALICE_HOME", "").strip()
    if env_home:
        homes.append(env_home)
    homes += [
        os.path.join(here, "..", "..", "snowgate-alice"),
        os.path.join(here, "..", "..", "..", "snowgate-alice"),
        os.path.expanduser(os.path.join("~", "Documents", "snowgate-alice")),
    ]
    for home in homes:
        candidate = os.path.abspath(os.path.join(home, "mind", "bin", "alice.js"))
        if os.path.isfile(candidate):
            return candidate
    return None


class _StdioClient:
    """Persistent jsonl subprocess; thread-safe; restarts once after an exit."""

    def __init__(self, node_bin: str, script: str):
        self.node_bin = node_bin
        self.script = script
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.frames: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.stats: Dict[str, Any] = {}

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.frames.put(json.loads(line))
            except ValueError:
                continue
        self.frames.put({"type": "exit"})

    def _start(self, timeout: float) -> bool:
        self.frames = queue.Queue()
        try:
            self.proc = subprocess.Popen(
                [self.node_bin, self.script, "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError:
            self.proc = None
            return False
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        try:
            frame = self.frames.get(timeout=timeout)
        except queue.Empty:
            self.close()
            return False
        if frame.get("type") != "ready":
            self.close()
            return False
        self.stats = frame.get("stats") or {}
        return True

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def request(self, payload: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        with self.lock:
            for attempt in range(2):
                if not self.alive() and not self._start(max(timeout, 60.0)):
                    return None
                rid = next(self.ids)
                try:
                    assert self.proc is not None and self.proc.stdin is not None
                    self.proc.stdin.write(json.dumps(dict(payload, id=rid)) + "\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    self.close()
                    continue
                while True:
                    try:
                        frame = self.frames.get(timeout=timeout)
                    except queue.Empty:
                        self.close()
                        return None
                    if frame.get("type") == "exit":
                        self.close()
                        break
                    if frame.get("id") == rid:
                        return None if frame.get("type") == "error" else frame
            return None

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


_client: Optional[_StdioClient] = None
_client_lock = threading.Lock()


def _stdio_client() -> Optional[_StdioClient]:
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        from hydra_cli.alice_runner import find_node_binary
        node_bin = find_node_binary()
        script = find_alice_mind()
        if not node_bin or not script:
            return None
        _client = _StdioClient(node_bin, script)
        atexit.register(_client.close)
        return _client


def _http(path: str, body: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
    base = os.environ.get("ALICE_URL", "").strip().rstrip("/")
    if not base:
        return None
    headers = {"content-type": "application/json"}
    token = os.environ.get("ALICE_TOKEN", "").strip()
    if token:
        headers["authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def mind_available() -> bool:
    if engine_mode() == "core":
        return False
    if os.environ.get("ALICE_URL", "").strip():
        return True
    from hydra_cli.alice_runner import find_node_binary
    return find_node_binary() is not None and find_alice_mind() is not None


def _legacy(res: Dict[str, Any]) -> Dict[str, Any]:
    """Map a native mind result onto the legacy AliceCore payload keys hydra already reads."""
    out = dict(res)
    out.setdefault("epistemicModality", res.get("modality", ""))
    return out


def ask(prompt: str, *, session: Optional[str] = None, history: Optional[List[Dict[str, str]]] = None, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """Ask the mind; returns the result with route, epistemicModality, answer, citations, escalate, or None."""
    if engine_mode() == "core":
        return None
    body: Dict[str, Any] = {"prompt": prompt}
    if session:
        body["session"] = session
    if history:
        body["history"] = history
    if os.environ.get("ALICE_URL", "").strip():
        res = _http("/v1/ask", body, timeout)
        return _legacy(res) if res and res.get("route") else None
    client = _stdio_client()
    if client is None:
        return None
    frame = client.request(dict(body, mode="ask"), timeout)
    return _legacy(frame) if frame and frame.get("route") else None


def ground(prompt: str, *, session: Optional[str] = None, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """Evidence pack for a model voice : systemPrompt, evidence, citations, references, confidence."""
    if engine_mode() == "core":
        return None
    body: Dict[str, Any] = {"prompt": prompt}
    if session:
        body["session"] = session
    if os.environ.get("ALICE_URL", "").strip():
        return _http("/v1/ground", body, timeout)
    client = _stdio_client()
    if client is None:
        return None
    return client.request(dict(body, mode="ground"), timeout)


def shutdown() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


ALIAS_NAMES = {"alice", "alice-mind"}

UNCITED_SYSTEM = (
    "You are the voice of Alice, a cognitive engine. Alice's curated stacks hold no reliable evidence for this question.\n"
    "Answer from general knowledge only when confident; state uncertainty plainly; never invent citations, quotes, or numbers.\n"
    "Begin with one sentence noting that the answer carries no stack citation."
)


def _text_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def gateway_preflight(messages: List[Dict[str, Any]], timeout: float = 30.0):
    """
    Return (final_result, messages). final_result holds a cited or proven mind answer that needs no model.
    Otherwise messages gain a leading system message carrying the mind's numbered evidence, or the uncited instruction on a gap.
    Unavailable mind returns (None, messages) unchanged.
    """
    last_user = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], dict) and messages[i].get("role") == "user":
            last_user = i
            break
    if last_user < 0:
        return None, messages
    prompt = _text_of(messages[last_user])
    history = [{"role": str(m.get("role")), "content": _text_of(m)} for m in messages[:last_user] if isinstance(m, dict)]
    res = ask(prompt, history=history, timeout=timeout)
    if res is None:
        return None, messages
    if is_final(res):
        return res, messages
    pack = ground(prompt, timeout=timeout) or {}
    grounded = bool(pack.get("evidence")) and float(pack.get("confidence") or 0) >= 0.33
    system = str(pack.get("systemPrompt") or "") if grounded else UNCITED_SYSTEM
    if grounded and pack.get("references"):
        system += "\n\nEnd the answer with a Sources block listing only the evidence numbers you cited:\n" + "\n".join(str(r) for r in pack["references"])
    return None, [{"role": "system", "content": system}] + list(messages)


def completion_payload(res: Dict[str, Any], model: str, created: int, cid: str) -> Dict[str, Any]:
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": str(res.get("answer") or "")}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "alice": {k: res.get(k) for k in ("route", "mindRoute", "modality", "confidence", "focus", "escalate", "citations")},
    }


def sse_frames(res: Dict[str, Any], model: str, created: int, cid: str) -> List[str]:
    def chunk(delta: Dict[str, Any], finish: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> str:
        body: Dict[str, Any] = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if extra:
            body.update(extra)
        return "data: " + json.dumps(body) + "\n\n"

    words = str(res.get("answer") or "").split(" ")
    frames = [chunk({"role": "assistant"})]
    for i in range(0, len(words), 8):
        piece = " ".join(words[i:i + 8])
        frames.append(chunk({"content": piece + (" " if i + 8 < len(words) else "")}))
    frames.append(chunk({}, "stop", {"alice": completion_payload(res, model, created, cid)["alice"]}))
    frames.append("data: [DONE]\n\n")
    return frames
