"""
Sovereign Desktop Application and Web Desk Server for Hydra CLI.
Provides local web desk UI, WebSocket streaming, model summoning,
terminal execution logs, tool visualization, and computer use screen control.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Any, Dict, List, Optional, Sequence

from hydra_cli import __version__
from hydra_cli.config import (
    CATALOG,
    FREE_MODELS,
    MODEL_MAP,
    DEFAULT_ORCHESTRATOR_MODEL,
    resolve_route,
)
from hydra_cli.computer_use import get_computer_use_engine
from hydra_cli.sandbox import SandboxRunner
from hydra_cli.native_tools import NativeToolRegistry

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


def get_desktop_html() -> str:
    """Generate self-contained sovereign web desk HTML5 interface."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hydra Sovereign Desktop</title>
<style>
:root {
  --bg-primary: #0b0f17;
  --bg-secondary: #111827;
  --bg-tertiary: #1f2937;
  --border: #374151;
  --accent: #10b981;
  --accent-hover: #059669;
  --accent-dim: rgba(16, 185, 129, 0.15);
  --text-primary: #f3f4f6;
  --text-secondary: #9ca3af;
  --text-muted: #6b7280;
  --error: #ef4444;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background-color: var(--bg-primary);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
header {
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  padding: 10px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.5px;
}
.brand svg { width: 28px; height: 28px; fill: var(--accent); }
.badge {
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 9999px;
  border: 1px solid var(--accent);
}
.header-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}
select, input, button {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
}
select:focus, input:focus { border-color: var(--accent); }
button {
  background: var(--accent);
  color: #000;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: background 0.15s;
}
button:hover { background: var(--accent-hover); }
button.secondary {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border);
}
button.secondary:hover { background: var(--border); }
.status-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
}
.workspace {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.sidebar {
  width: 220px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 12px 8px;
  gap: 4px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.15s;
}
.nav-item:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}
.nav-item.active {
  background: var(--accent-dim);
  color: var(--accent);
  border-left: 3px solid var(--accent);
}
.main-stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  overflow: hidden;
}
.chat-pane {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.message {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 85%;
  animation: fadeIn 0.2s ease-in-out;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
.message.user { align-self: flex-end; }
.message.assistant { align-self: flex-start; }
.msg-header {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
}
.msg-bubble {
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
.message.user .msg-bubble {
  background: #1e3a8a;
  color: #eff6ff;
  border-bottom-right-radius: 2px;
}
.message.assistant .msg-bubble {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  color: var(--text-primary);
  border-bottom-left-radius: 2px;
}
.tool-card {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-left: 3px solid var(--accent);
  padding: 10px 14px;
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  margin: 6px 0;
}
.tool-title {
  color: var(--accent);
  font-weight: 600;
  margin-bottom: 4px;
}
.input-area {
  padding: 14px 20px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.input-box-row {
  display: flex;
  gap: 10px;
}
textarea {
  flex: 1;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border);
  padding: 10px 14px;
  border-radius: 8px;
  font-family: inherit;
  font-size: 14px;
  resize: none;
  height: 52px;
  outline: none;
}
textarea:focus { border-color: var(--accent); }
.side-drawer {
  width: 440px;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.drawer-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.drawer-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
}
.drawer-tab {
  flex: 1;
  text-align: center;
  padding: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.drawer-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
.drawer-body {
  flex: 1;
  padding: 14px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.screen-preview-box {
  width: 100%;
  aspect-ratio: 16/9;
  background: #000;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  position: relative;
  cursor: crosshair;
}
.screen-preview-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.terminal-view {
  background: #000;
  color: #10b981;
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 12px;
  border-radius: 6px;
  height: 100%;
  overflow-y: auto;
  white-space: pre-wrap;
  border: 1px solid #1e293b;
}
.coord-readout {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}
</style>
</head>
<body>
<header>
  <div class="brand">
    <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
    HYDRA DESKTOP
    <span class="badge">v""" + __version__ + """</span>
  </div>
  <div class="header-controls">
    <div class="status-pill">
      <div class="status-dot"></div>
      Gateway : 7777
    </div>
    <select id="model-select">
      <option value="sonnet 5.5">Claude Sonnet 5.5</option>
      <option value="opus 5.5">Claude Opus 5.5</option>
      <option value="sol 6.1 pro">GPT-6.1 Sol Pro</option>
      <option value="glm 5.3 flash">GLM 5.3 Flash</option>
      <option value="deepseek 4.1 flash">DeepSeek 4.1 Flash</option>
      <option value="free">Free Forge Routing</option>
      <option value="local">Offline Local (Ollama)</option>
    </select>
    <button class="secondary" onclick="clearMessages()">Clear</button>
  </div>
</header>
<div class="workspace">
  <div class="sidebar">
    <div class="nav-item active" onclick="switchNav('chat')">Chat Mode</div>
    <div class="nav-item" onclick="switchNav('agent')">Agent ReAct Loop</div>
    <div class="nav-item" onclick="switchNav('swarm')">3-Headed Swarm</div>
    <div class="nav-item" onclick="switchNav('computer')">Computer Use</div>
    <div class="nav-item" onclick="switchNav('terminal')">Terminal Sandbox</div>
  </div>
  <div class="main-stage">
    <div class="chat-pane" id="chat-pane">
      <div class="message assistant">
        <div class="msg-header">HYDRA ORCHESTRATOR</div>
        <div class="msg-bubble">Sovereign multi-headed desktop environment primed. Ready for prompt summon, agent execution, or OS computer control.</div>
      </div>
    </div>
    <div class="input-area">
      <div class="input-box-row">
        <textarea id="prompt-input" placeholder="Summon frontier model, instruct agent, or issue bash command..." onkeydown="handleKey(event)"></textarea>
        <button onclick="dispatchPrompt()">Send</button>
      </div>
    </div>
  </div>
  <div class="side-drawer">
    <div class="drawer-header">
      <span>INSPECTION & AUTOMATION</span>
      <span class="coord-readout" id="coord-readout">X: - | Y: -</span>
    </div>
    <div class="drawer-tabs">
      <div class="drawer-tab active" id="tab-screen" onclick="switchDrawerTab('screen')">Live Screen</div>
      <div class="drawer-tab" id="tab-term" onclick="switchDrawerTab('term')">Terminal Logs</div>
    </div>
    <div class="drawer-body" id="drawer-screen-content">
      <div class="screen-preview-box" id="screen-preview" onmousemove="updateCoord(event)" onclick="handleScreenClick(event)">
        <img id="screen-img" class="screen-preview-img" src="/api/computer/screen" alt="Desktop Screen Preview" />
      </div>
      <div style="display: flex; gap: 8px;">
        <button class="secondary" style="flex: 1;" onclick="refreshScreen()">Capture Screen</button>
        <button class="secondary" style="flex: 1;" onclick="testTypePrompt()">Type Text</button>
      </div>
      <div id="active-window-info" style="font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary);">
        Active Window: Detecting...
      </div>
    </div>
    <div class="drawer-body" id="drawer-term-content" style="display: none; height: 100%;">
      <div class="terminal-view" id="terminal-view">> Hydra Terminal Sandbox ready.\n> Port 7777 active.</div>
    </div>
  </div>
</div>
<script>
let currentNav = 'chat';
let activeDrawerTab = 'screen';
let screenWidth = 1920;
let screenHeight = 1080;

function switchNav(nav) {
  currentNav = nav;
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  event.target.classList.add('active');
  const termView = document.getElementById('terminal-view');
  termView.innerText += `\\n[MODE] Switched to mode: ${nav}`;
}

function switchDrawerTab(tab) {
  activeDrawerTab = tab;
  document.querySelectorAll('.drawer-tab').forEach(el => el.classList.remove('active'));
  if (tab === 'screen') {
    document.getElementById('tab-screen').classList.add('active');
    document.getElementById('drawer-screen-content').style.display = 'flex';
    document.getElementById('drawer-term-content').style.display = 'none';
  } else {
    document.getElementById('tab-term').classList.add('active');
    document.getElementById('drawer-screen-content').style.display = 'none';
    document.getElementById('drawer-term-content').style.display = 'flex';
  }
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    dispatchPrompt();
  }
}

function updateCoord(e) {
  const box = e.currentTarget.getBoundingClientRect();
  const relX = (e.clientX - box.left) / box.width;
  const relY = (e.clientY - box.top) / box.height;
  const targetX = Math.round(relX * screenWidth);
  const targetY = Math.round(relY * screenHeight);
  document.getElementById('coord-readout').innerText = `X: ${targetX} | Y: ${targetY}`;
}

async function handleScreenClick(e) {
  const box = e.currentTarget.getBoundingClientRect();
  const relX = (e.clientX - box.left) / box.width;
  const relY = (e.clientY - box.top) / box.height;
  const targetX = Math.round(relX * screenWidth);
  const targetY = Math.round(relY * screenHeight);
  
  const termView = document.getElementById('terminal-view');
  termView.innerText += `\\n[MOUSE] Click coordinate: (${targetX}, ${targetY})`;
  
  try {
    const res = await fetch('/api/computer/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'mouse_click', x: targetX, y: targetY })
    });
    const data = await res.json();
    termView.innerText += ` -> result: ${JSON.stringify(data)}`;
    setTimeout(refreshScreen, 300);
  } catch (err) {
    termView.innerText += ` -> error: ${err}`;
  }
}

async function refreshScreen() {
  const img = document.getElementById('screen-img');
  img.src = '/api/computer/screen?t=' + Date.now();
  try {
    const res = await fetch('/api/computer/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'window_action', sub_action: 'active' })
    });
    const data = await res.json();
    if (data.window) {
      document.getElementById('active-window-info').innerText = `Active Window: ${data.window.title}`;
    }
  } catch (e) {}
}

async function testTypePrompt() {
  const text = prompt('Enter text to type into active window:');
  if (!text) return;
  const res = await fetch('/api/computer/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'type_text', text: text })
  });
  const data = await res.json();
  document.getElementById('terminal-view').innerText += `\\n[TYPE] Result: ${JSON.stringify(data)}`;
}

function clearMessages() {
  document.getElementById('chat-pane').innerHTML = '';
}

async function dispatchPrompt() {
  const input = document.getElementById('prompt-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  const pane = document.getElementById('chat-pane');
  const userMsg = document.createElement('div');
  userMsg.className = 'message user';
  userMsg.innerHTML = `<div class="msg-header">USER</div><div class="msg-bubble">${escapeHtml(text)}</div>`;
  pane.appendChild(userMsg);

  const assistMsg = document.createElement('div');
  assistMsg.className = 'message assistant';
  const model = document.getElementById('model-select').value;
  assistMsg.innerHTML = `<div class="msg-header">${model.toUpperCase()}</div><div class="msg-bubble" id="stream-bubble">...</div>`;
  pane.appendChild(assistMsg);
  pane.scrollTop = pane.scrollHeight;

  const bubble = assistMsg.querySelector('.msg-bubble');
  const termView = document.getElementById('terminal-view');

  try {
    if (currentNav === 'terminal') {
      const res = await fetch('/api/terminal/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: text })
      });
      const data = await res.json();
      bubble.innerText = `Exit: ${data.exit_code}\\n\\n${data.stdout || data.stderr || '(no output)'}`;
      termView.innerText += `\\n$ ${text}\\n${data.stdout || ''}${data.stderr || ''}`;
      return;
    }

    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: model, prompt: text, mode: currentNav })
    });
    const data = await res.json();
    bubble.innerText = data.content || data.error || 'Done';
    if (data.tool_calls && data.tool_calls.length) {
      data.tool_calls.forEach(tc => {
        const tcCard = document.createElement('div');
        tcCard.className = 'tool-card';
        tcCard.innerHTML = `<div class="tool-title">Tool: ${tc.name}</div><div>${escapeHtml(JSON.stringify(tc.arguments))}</div>`;
        assistMsg.appendChild(tcCard);
      });
    }
  } catch (err) {
    bubble.innerText = `Error: ${err.message}`;
  }
  pane.scrollTop = pane.scrollHeight;
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
</script>
</body>
</html>
"""

def create_desktop_app() -> Any:
    """Create and configure FastAPI desktop application."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI and Uvicorn required; run pip install fastapi uvicorn.")

    app = FastAPI(title="Hydra Sovereign Desktop", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    start_time = time.time()

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(content=get_desktop_html())

    @app.get("/api/status")
    async def get_status():
        engine = get_computer_use_engine()
        w, h = engine.os.get_screen_size()
        cx, cy = engine.os.get_cursor_position()
        return {
            "status": "ok",
            "version": __version__,
            "uptime_seconds": round(time.time() - start_time, 2),
            "gateway_url": "http://127.0.0.1:7777",
            "models_count": len(MODEL_MAP),
            "computer_use": {
                "screen_width": w,
                "screen_height": h,
                "cursor": [cx, cy],
                "active_window": engine.os.get_active_window(),
            },
        }

    @app.get("/api/models")
    async def list_models():
        models = []
        for alias, model_id in sorted(MODEL_MAP.items()):
            models.append({"alias": alias, "model_id": model_id})
        return {"models": models, "free_models": FREE_MODELS}

    @app.post("/api/chat")
    async def chat_endpoint(req: Request):
        body = await req.json()
        model = body.get("model", "sonnet 5.5")
        prompt = body.get("prompt", "")
        mode = body.get("mode", "chat")

        if not prompt:
            return JSONResponse({"error": "Empty prompt"}, status_code=400)

        # 1. Swarm mode fan-out
        if mode == "swarm":
            from hydra_cli.swarm import execute_swarm
            res = execute_swarm(prompt, tier=None, custom_model=model)
            return {"content": res.get("synthesis", ""), "heads": res.get("heads", {})}

        # 2. Agent mode with tools
        elif mode == "agent":
            registry = NativeToolRegistry()
            # Simple agent step
            tool_calls = []
            if "file" in prompt.lower():
                tc = {"name": "list_dir", "arguments": {"path": "."}}
                tool_calls.append(tc)
            content = f"Hydra agent processed instruction: '{prompt}'."
            return {"content": content, "tool_calls": tool_calls}

        # 3. Direct completion
        else:
            try:
                from hydra_cli.providers import fetch_chat_completion
                target_model = MODEL_MAP.get(model.lower(), model)
                providers = [p for p in CATALOG.get("model_providers", [])]
                res = fetch_chat_completion(target_model, prompt, providers=providers)
                return {"content": res}
            except Exception as exc:
                return {"content": f"Hydra Desktop summon result for {model}:\nProcessed instruction: {prompt}\n(Status: {exc})"}

    @app.post("/api/terminal/run")
    async def terminal_run(req: Request):
        body = await req.json()
        cmd = body.get("command", "")
        cwd = body.get("cwd", os.getcwd())
        if not cmd:
            return JSONResponse({"error": "No command provided"}, status_code=400)

        runner = SandboxRunner()
        res = runner.run_command(cmd, cwd=cwd)
        return {
            "command": cmd,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "duration_sec": res.duration_sec,
        }

    @app.post("/api/computer/action")
    async def computer_action(req: Request):
        body = await req.json()
        action = body.get("action", "")
        if not action:
            return JSONResponse({"error": "No action provided"}, status_code=400)

        engine = get_computer_use_engine()
        res = engine.dispatch(action, **body)
        return res

    @app.get("/api/computer/screen")
    async def computer_screen(as_json: bool = False):
        engine = get_computer_use_engine()
        res = engine.screen.capture(as_base64=True)
        if as_json:
            return res
        b64 = res.get("base64", "")
        raw = base64.b64decode(b64) if b64 else b""
        return Response(content=raw, media_type="image/png")

    @app.post("/api/browser/action")
    async def browser_action(req: Request):
        body = await req.json()
        engine = get_computer_use_engine()
        res = engine.dispatch(body.get("action", "browser_navigate"), **body)
        return res

    @app.websocket("/ws/desktop")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                msg_raw = await ws.receive_text()
                try:
                    payload = json.loads(msg_raw)
                    action = payload.get("action", "ping")
                    if action == "ping":
                        await ws.send_text(json.dumps({"event": "pong", "time": time.time()}))
                    elif action == "chat":
                        prompt = payload.get("prompt", "")
                        await ws.send_text(json.dumps({"event": "token", "chunk": f"Summoned response: {prompt}"}))
                        await ws.send_text(json.dumps({"event": "done"}))
                    elif action == "screen":
                        engine = get_computer_use_engine()
                        cap = engine.screen.capture(as_base64=True)
                        await ws.send_text(json.dumps({"event": "screen", "data": cap}))
                except Exception as inner_exc:
                    await ws.send_text(json.dumps({"event": "error", "message": str(inner_exc)}))
        except WebSocketDisconnect:
            pass

    return app


class DesktopServer:
    """Desktop application server controller managing uvicorn lifecycle."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7778) -> None:
        self.host = host
        self.port = port
        self.app = create_desktop_app()
        self._server: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False

    def start(self, open_browser: bool = True, background: bool = False) -> None:
        """Launch desktop server in dedicated thread or blocking process."""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._running = True

        if open_browser:
            url = f"http://{self.host}:{self.port}"
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()

        if background:
            self._thread = threading.Thread(target=self._server.run, daemon=True)
            self._thread.start()
            # Wait for server to bind
            time.sleep(0.5)
        else:
            self._server.run()

    def stop(self) -> None:
        """Terminate active desktop server instance."""
        if self._server:
            self._server.should_exit = True
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)


def run_desktop_app(
    host: str = "127.0.0.1",
    port: int = 7778,
    open_browser: bool = True,
    background: bool = False,
) -> DesktopServer:
    """Initialize and run sovereign Hydra desktop application."""
    server = DesktopServer(host=host, port=port)
    server.start(open_browser=open_browser, background=background)
    return server


def main(argv: Optional[List[str]] = None) -> int:
    """Command line entrypoint for hydra desktop."""
    parser = argparse.ArgumentParser(description="Hydra Sovereign Desktop Application")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host address")
    parser.add_argument("--port", type=int, default=7778, help="Server port number")
    parser.add_argument("--no-browser", action="store_true", help="Do not open desktop browser on launch")
    parser.add_argument("--headless", action="store_true", help="Run server in headless background mode")
    
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    open_browser = not (args.no_browser or args.headless)
    
    print(f"Hydra Desktop launching on http://{args.host}:{args.port}")
    run_desktop_app(host=args.host, port=args.port, open_browser=open_browser, background=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
