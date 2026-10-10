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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
    """Generate self-contained sovereign web desk HTML5 interface with multi-session tabs and diff viewer."""
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
  --diff-add: #064e3b;
  --diff-add-text: #a7f3d0;
  --diff-del: #7f1d1d;
  --diff-del-text: #fecaca;
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
.session-bar {
  background: #0f172a;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 4px 16px;
  gap: 6px;
  overflow-x: auto;
}
.session-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  cursor: pointer;
  white-space: nowrap;
}
.session-tab.active {
  background: var(--accent-dim);
  color: var(--accent);
  border-color: var(--accent);
  font-weight: 600;
}
.session-close {
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
}
.session-close:hover { color: var(--error); }
.add-session-btn {
  background: transparent;
  color: var(--accent);
  border: 1px dashed var(--accent);
  padding: 4px 8px;
  font-size: 11px;
  border-radius: 4px;
}
.workspace {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.sidebar {
  width: 200px;
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
  width: 480px;
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
  padding: 8px 4px;
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
.screen-container {
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #000;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  cursor: crosshair;
}
.screen-preview-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.screen-canvas-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
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
.diff-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-family: var(--font-mono);
  font-size: 12px;
}
.diff-card {
  background: #0f172a;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.diff-header {
  background: #1e293b;
  padding: 8px 12px;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: var(--accent);
}
.diff-body {
  padding: 8px 0;
  overflow-x: auto;
}
.diff-line {
  padding: 2px 12px;
  white-space: pre;
}
.diff-line.add { background: var(--diff-add); color: var(--diff-add-text); }
.diff-line.del { background: var(--diff-del); color: var(--diff-del-text); }
.diff-line.ctx { color: var(--text-secondary); }
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
    <div class="token-tally-widget" id="token-tally" title="Live token usage and cost accounting" style="display: flex; align-items: center; gap: 8px; font-size: 11px; background: rgba(31, 41, 55, 0.7); padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border);">
      <span style="color: var(--text-secondary);">Tokens: <strong id="tally-tokens" style="color: var(--text-primary); font-family: var(--font-mono);">0</strong></span>
      <span style="color: var(--border);">&bull;</span>
      <span style="color: var(--text-secondary);">Cost: <strong id="tally-cost" style="color: var(--accent); font-family: var(--font-mono);">$0.0000</strong></span>
    </div>
    <div class="status-pill">
      <div class="status-dot"></div>
      Gateway : 7777
    </div>
    <select id="model-select">
      <option value="sonnet 5.5">Claude Sonnet 5.5</option>
      <option value="claude-3-7-sonnet">Claude 3.7 Sonnet</option>
      <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
      <option value="qwen-3.8-27b:free">Qwen 3.8 27B (Free)</option>
      <option value="deepseek-chat:free">DeepSeek Chat (Free)</option>
      <option value="llama-3.3-70b-instruct:free">Llama 3.3 70B (Free)</option>
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
<div class="session-bar" id="session-bar">
  <div class="session-tab active" data-id="session-1" onclick="switchSession('session-1')">
    <span>Session 1</span>
    <span class="session-close" onclick="closeSession(event, 'session-1')">&times;</span>
  </div>
  <button class="add-session-btn" onclick="addNewSession()">+ New Session</button>
</div>
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
      <div class="drawer-tab" id="tab-diff" onclick="switchDrawerTab('diff')">Diffs & Code</div>
      <div class="drawer-tab" id="tab-agent" onclick="switchDrawerTab('agent')">Agent Runner</div>
    </div>
    <div class="drawer-body" id="drawer-screen-content">
      <div class="screen-container" id="screen-container" onmousemove="handleScreenHover(event)" onclick="handleScreenClick(event)">
        <img id="screen-img" class="screen-preview-img" src="/api/computer/screen" alt="Desktop Screen Preview" />
        <canvas id="screen-overlay-canvas" class="screen-canvas-overlay"></canvas>
      </div>
      <div style="display: flex; gap: 8px;">
        <button class="secondary" style="flex: 1;" onclick="refreshScreen()">Capture Screen</button>
        <button class="secondary" style="flex: 1;" onclick="toggleOverlayBounds()">Toggle Bounds</button>
        <button class="secondary" style="flex: 1;" onclick="testTypePrompt()">Type Text</button>
      </div>
      <div id="active-window-info" style="font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary);">
        Active Window: Detecting...
      </div>
    </div>
    <div class="drawer-body" id="drawer-term-content" style="display: none; height: 100%;">
      <div class="terminal-view" id="terminal-view">> Hydra Terminal Sandbox ready.\n> Port 7777 active.</div>
    </div>
    <div class="drawer-body" id="drawer-diff-content" style="display: none; height: 100%;">
      <div class="diff-container" id="diffs-container">
        <div class="diff-card">
          <div class="diff-header">
            <span>hydra_cli/computer_use.py</span>
            <span>MODIFIED</span>
          </div>
          <div class="diff-body">
            <div class="diff-line ctx">@@ -180,6 +180,18 @@</div>
            <div class="diff-line add">+    def fill_form(self, fields, form_selector=None, submit=False):</div>
            <div class="diff-line add">+    def scroll_until_visible(self, selector, max_scrolls=10):</div>
            <div class="diff-line add">+    def extract_table_data(self, selector='table'):</div>
            <div class="diff-line add">+    def safe_drag_and_drop(self, from_coord, to_coord):</div>
          </div>
        </div>
      </div>
    </div>
    <div class="drawer-body" id="drawer-agent-content" style="display: none; height: 100%; overflow-y: auto; flex-direction: column; gap: 10px;">
      <div class="agent-panel" style="display: flex; flex-direction: column; gap: 8px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600; font-size: 13px;">AUTONOMOUS AGENT RUNNER</span>
          <span id="agent-status-badge" class="badge" style="background: #374151;">IDLE</span>
        </div>
        <textarea id="agent-task-input" style="width: 100%; min-height: 60px; background: #0b0f17; border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary); padding: 8px; font-family: inherit; font-size: 12px; resize: vertical;" placeholder="Enter autonomous task (e.g. 'Navigate to data:... and extract table')..."></textarea>
        <div style="display: flex; gap: 6px; align-items: center;">
          <input type="number" id="agent-max-steps" value="30" min="1" max="100" style="width: 50px; background: #0b0f17; border: 1px solid var(--border); border-radius: 4px; color: var(--text-primary); padding: 4px 6px; font-size: 11px;" title="Max Steps" />
          <button class="primary" style="flex: 1; padding: 6px 10px; font-size: 12px;" onclick="startAgentTask()">Start Agent</button>
          <button class="secondary" style="padding: 6px 8px; font-size: 12px;" onclick="pauseAgentTask()" id="btn-agent-pause">Pause</button>
          <button class="secondary" style="padding: 6px 8px; font-size: 12px;" onclick="resumeAgentTask()" id="btn-agent-resume">Resume</button>
          <button class="secondary" style="padding: 6px 8px; font-size: 12px; color: var(--error);" onclick="abortAgentTask()" id="btn-agent-abort">Abort</button>
          <button class="secondary" style="padding: 6px 8px; font-size: 12px;" onclick="exportAgentTrace()" id="btn-agent-export">Export Trace</button>
        </div>
        <div style="background: #1f2937; border-radius: 4px; height: 6px; width: 100%; overflow: hidden; margin-top: 4px;">
          <div id="agent-progress-bar" style="background: var(--accent); width: 0%; height: 100%; transition: width 0.3s;"></div>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted);">
          <span id="agent-step-counter">Step: 0 / 30</span>
          <span id="agent-elapsed-timer">Elapsed: 0.0s</span>
        </div>
        <div style="font-weight: 600; font-size: 12px; margin-top: 4px; color: var(--text-secondary);">ACTION TRACE LOG</div>
        <div class="trace-log" id="agent-trace-log" style="display: flex; flex-direction: column; gap: 8px; max-height: 380px; overflow-y: auto;">
          <div style="font-size: 11px; color: var(--text-muted); font-style: italic;">No active agent execution trace. Enter task and start runner.</div>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
let currentNav = 'chat';
let activeDrawerTab = 'screen';
let screenWidth = 1920;
let screenHeight = 1080;
let overlayBoundsEnabled = true;

// Multi-session state store
const sessions = {
  'session-1': {
    name: 'Session 1',
    messages: [
      { role: 'assistant', header: 'HYDRA ORCHESTRATOR', text: 'Sovereign multi-headed desktop environment primed.' }
    ],
    model: 'sonnet 5.5',
    nav: 'chat'
  }
};
let activeSessionId = 'session-1';
let sessionCounter = 1;

function switchSession(sid) {
  if (!sessions[sid]) return;
  activeSessionId = sid;
  document.querySelectorAll('.session-tab').forEach(el => el.classList.remove('active'));
  const targetTab = document.querySelector(`.session-tab[data-id="${sid}"]`);
  if (targetTab) targetTab.classList.add('active');

  const sess = sessions[sid];
  document.getElementById('model-select').value = sess.model || 'sonnet 5.5';
  renderSessionMessages(sid);
}

function addNewSession() {
  sessionCounter++;
  const sid = `session-${sessionCounter}`;
  sessions[sid] = {
    name: `Session ${sessionCounter}`,
    messages: [
      { role: 'assistant', header: 'HYDRA ORCHESTRATOR', text: `Session ${sessionCounter} primed. Dispatch instruction or summon heads.` }
    ],
    model: document.getElementById('model-select').value,
    nav: currentNav
  };

  const bar = document.getElementById('session-bar');
  const addBtn = bar.querySelector('.add-session-btn');
  const tab = document.createElement('div');
  tab.className = 'session-tab';
  tab.dataset.id = sid;
  tab.onclick = () => switchSession(sid);
  tab.innerHTML = `<span>Session ${sessionCounter}</span><span class="session-close" onclick="closeSession(event, '${sid}')">&times;</span>`;
  bar.insertBefore(tab, addBtn);

  switchSession(sid);
}

function closeSession(e, sid) {
  e.stopPropagation();
  if (Object.keys(sessions).length <= 1) return;
  delete sessions[sid];
  const tab = document.querySelector(`.session-tab[data-id="${sid}"]`);
  if (tab) tab.remove();

  if (activeSessionId === sid) {
    const firstRemaining = Object.keys(sessions)[0];
    switchSession(firstRemaining);
  }
}

function renderSessionMessages(sid) {
  const pane = document.getElementById('chat-pane');
  pane.innerHTML = '';
  const sess = sessions[sid];
  if (!sess) return;

  sess.messages.forEach(msg => {
    const msgEl = document.createElement('div');
    msgEl.className = `message ${msg.role}`;
    msgEl.innerHTML = `<div class="msg-header">${escapeHtml(msg.header)}</div><div class="msg-bubble">${escapeHtml(msg.text)}</div>`;
    pane.appendChild(msgEl);
  });
  pane.scrollTop = pane.scrollHeight;
}

function switchNav(nav) {
  currentNav = nav;
  document.querySelectorAll('.sidebar .nav-item').forEach(el => el.classList.remove('active'));
  if (typeof event !== 'undefined' && event && event.target) {
    event.target.classList.add('active');
  }
  const termView = document.getElementById('terminal-view');
  if (termView) termView.innerText += `\n[MODE] Switched to mode: ${nav}`;
  if (nav === 'agent') {
    switchDrawerTab('agent');
  }
}

function switchDrawerTab(tab) {
  activeDrawerTab = tab;
  document.querySelectorAll('.drawer-tab').forEach(el => el.classList.remove('active'));
  ['screen', 'term', 'diff', 'agent'].forEach(t => {
    const el = document.getElementById('drawer-' + t + '-content');
    if (el) el.style.display = 'none';
  });

  const tabEl = document.getElementById('tab-' + tab);
  if (tabEl) tabEl.classList.add('active');
  const contentEl = document.getElementById('drawer-' + tab + '-content');
  if (contentEl) contentEl.style.display = 'flex';

  if (tab === 'diff') {
    fetchDiffs();
  } else if (tab === 'agent') {
    pollAgentStatus();
  }
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    dispatchPrompt();
  }
}

let currentHighlights = [];
let desktopWs = null;
let wsHeartbeatTimer = null;
let wsReconnectTimer = null;

function calculateViewportScale() {
  const img = document.getElementById('screen-img');
  const canvas = document.getElementById('screen-overlay-canvas');
  if (!canvas) return { scaleX: 1.0, scaleY: 1.0, box: { width: 800, height: 450 } };
  const box = (img && img.clientWidth) ? img.getBoundingClientRect() : canvas.getBoundingClientRect();
  const w = box.width || canvas.clientWidth || 800;
  const h = box.height || canvas.clientHeight || 450;
  if (canvas.width !== Math.round(w) || canvas.height !== Math.round(h)) {
    canvas.width = Math.round(w);
    canvas.height = Math.round(h);
  }
  const scaleX = w / (screenWidth || 1920);
  const scaleY = h / (screenHeight || 1080);
  return { scaleX, scaleY, box: { width: w, height: h } };
}

function renderOverlayHighlights(highlights) {
  if (highlights) currentHighlights = highlights;
  const canvas = document.getElementById('screen-overlay-canvas');
  if (!canvas) return;
  const { scaleX, scaleY } = calculateViewportScale();
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!currentHighlights || !currentHighlights.length) return;

  currentHighlights.forEach(h => {
    const sx = Math.round(h.x * scaleX);
    const sy = Math.round(h.y * scaleY);
    const sw = Math.round(h.width * scaleX);
    const sh = Math.round(h.height * scaleY);

    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;
    ctx.strokeRect(sx, sy, sw, sh);
    ctx.fillStyle = 'rgba(239, 68, 68, 0.2)';
    ctx.fillRect(sx, sy, sw, sh);
    ctx.fillStyle = '#fca5a5';
    ctx.font = '10px monospace';
    ctx.fillText(h.label || 'Target', sx + 2, Math.max(10, sy - 4));
  });
}

function updateCoord(event) { handleScreenHover(event); }

function handleScreenHover(e) {
  const { scaleX, scaleY, box } = calculateViewportScale();
  const relX = (e.clientX - box.left) / box.width;
  const relY = (e.clientY - box.top) / box.height;
  const targetX = Math.round(relX * screenWidth);
  const targetY = Math.round(relY * screenHeight);
  const readout = document.getElementById('coord-readout');
  if (readout) readout.innerText = `X: ${targetX} | Y: ${targetY}`;

  const canvas = document.getElementById('screen-overlay-canvas');
  if (canvas && overlayBoundsEnabled) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (currentHighlights && currentHighlights.length) {
      renderOverlayHighlights(currentHighlights);
    }

    const curX = e.clientX - box.left;
    const curY = e.clientY - box.top;
    ctx.strokeStyle = 'rgba(16, 185, 129, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(curX, 0);
    ctx.lineTo(curX, canvas.height);
    ctx.moveTo(0, curY);
    ctx.lineTo(canvas.width, curY);
    ctx.stroke();

    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;
    ctx.strokeRect(4, 4, canvas.width - 8, canvas.height - 8);
    ctx.fillStyle = 'rgba(16, 185, 129, 0.05)';
    ctx.fillRect(4, 4, canvas.width - 8, canvas.height - 8);
  }
}

window.addEventListener('resize', () => {
  calculateViewportScale();
  if (currentHighlights && currentHighlights.length) {
    renderOverlayHighlights(currentHighlights);
  }
});

function toggleOverlayBounds() {
  overlayBoundsEnabled = !overlayBoundsEnabled;
  const canvas = document.getElementById('screen-overlay-canvas');
  if (canvas && !overlayBoundsEnabled) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

async function handleScreenClick(e) {
  const box = e.currentTarget.getBoundingClientRect();
  const relX = (e.clientX - box.left) / box.width;
  const relY = (e.clientY - box.top) / box.height;
  const targetX = Math.round(relX * screenWidth);
  const targetY = Math.round(relY * screenHeight);
  
  const termView = document.getElementById('terminal-view');
  termView.innerText += `\n[MOUSE] Click coordinate: (${targetX}, ${targetY})`;
  
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
  document.getElementById('terminal-view').innerText += `\n[TYPE] Result: ${JSON.stringify(data)}`;
}

async function fetchDiffs() {
  try {
    const res = await fetch('/api/diffs');
    const data = await res.json();
    if (data.diffs && data.diffs.length > 0) {
      const container = document.getElementById('diffs-container');
      container.innerHTML = '';
      data.diffs.forEach(df => {
        const card = document.createElement('div');
        card.className = 'diff-card';
        let linesHtml = '';
        (df.lines || []).forEach(ln => {
          const cls = ln.startsWith('+') ? 'add' : (ln.startsWith('-') ? 'del' : 'ctx');
          linesHtml += `<div class="diff-line ${cls}">${escapeHtml(ln)}</div>`;
        });
        card.innerHTML = `<div class="diff-header"><span>${escapeHtml(df.path)}</span><span>${escapeHtml(df.type || 'MODIFIED')}</span></div><div class="diff-body">${linesHtml}</div>`;
        container.appendChild(card);
      });
    }
  } catch (e) {}
}

function clearMessages() {
  if (sessions[activeSessionId]) {
    sessions[activeSessionId].messages = [];
  }
  document.getElementById('chat-pane').innerHTML = '';
}

async function dispatchPrompt() {
  const input = document.getElementById('prompt-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  const model = document.getElementById('model-select').value;
  const sess = sessions[activeSessionId];
  if (sess) {
    sess.messages.push({ role: 'user', header: 'USER', text: text });
    sess.model = model;
  }

  const pane = document.getElementById('chat-pane');
  const userMsg = document.createElement('div');
  userMsg.className = 'message user';
  userMsg.innerHTML = `<div class="msg-header">USER</div><div class="msg-bubble">${escapeHtml(text)}</div>`;
  pane.appendChild(userMsg);

  const assistMsg = document.createElement('div');
  assistMsg.className = 'message assistant';
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
      const output = `Exit: ${data.exit_code}\n\n${data.stdout || data.stderr || '(no output)'}`;
      bubble.innerText = output;
      termView.innerText += `\n$ ${text}\n${data.stdout || ''}${data.stderr || ''}`;
      if (sess) sess.messages.push({ role: 'assistant', header: model.toUpperCase(), text: output });
      return;
    }

    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: model, prompt: text, mode: currentNav })
    });
    const data = await res.json();
    const reply = data.content || data.error || 'Done';
    bubble.innerText = reply;
    if (sess) sess.messages.push({ role: 'assistant', header: model.toUpperCase(), text: reply });
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

let agentPollTimer = null;

async function startAgentTask() {
  const taskInput = document.getElementById('agent-task-input');
  const promptInput = document.getElementById('prompt-input');
  const task = (taskInput && taskInput.value.trim()) || (promptInput && promptInput.value.trim()) || '';
  if (!task) return;
  const maxSteps = parseInt(document.getElementById('agent-max-steps').value || '30', 10);
  const model = document.getElementById('model-select').value || 'sonnet 5.5';

  const badge = document.getElementById('agent-status-badge');
  if (badge) { badge.innerText = 'STARTING'; badge.style.background = '#2563eb'; }

  try {
    const res = await fetch('/api/agent/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, model, max_steps: maxSteps })
    });
    const data = await res.json();
    if (data.isError) {
      if (badge) { badge.innerText = 'ERROR'; badge.style.background = 'var(--error)'; }
      alert('Agent failed to start: ' + data.error);
      return;
    }
    if (badge) { badge.innerText = 'RUNNING'; badge.style.background = 'var(--accent)'; }
    startAgentPolling();
  } catch (err) {
    if (badge) { badge.innerText = 'ERROR'; badge.style.background = 'var(--error)'; }
  }
}

async function pauseAgentTask() {
  await fetch('/api/agent/pause', { method: 'POST' });
  const badge = document.getElementById('agent-status-badge');
  if (badge) { badge.innerText = 'PAUSED'; badge.style.background = '#f59e0b'; }
}

async function resumeAgentTask() {
  await fetch('/api/agent/resume', { method: 'POST' });
  const badge = document.getElementById('agent-status-badge');
  if (badge) { badge.innerText = 'RUNNING'; badge.style.background = 'var(--accent)'; }
}

function exportAgentTrace() {
  window.open('/api/agent/export?download=true', '_blank');
}

async function abortAgentTask() {
  await fetch('/api/agent/abort', { method: 'POST' });
  const badge = document.getElementById('agent-status-badge');
  if (badge) { badge.innerText = 'ABORTED'; badge.style.background = 'var(--error)'; }
  stopAgentPolling();
}

function startAgentPolling() {
  if (agentPollTimer) clearInterval(agentPollTimer);
  agentPollTimer = setInterval(pollAgentStatus, 500);
}

function stopAgentPolling() {
  if (agentPollTimer) {
    clearInterval(agentPollTimer);
    agentPollTimer = null;
  }
}

async function pollAgentStatus() {
  try {
    const res = await fetch('/api/agent/status');
    const st = await res.json();
    renderAgentStatus(st);
    if (st.status === 'COMPLETED' || st.status === 'FAILED' || st.status === 'ABORTED') {
      stopAgentPolling();
    }
  } catch (e) {}
}

function renderAgentStatus(st) {
  const badge = document.getElementById('agent-status-badge');
  if (badge) {
    badge.innerText = st.status;
    if (st.status === 'RUNNING') badge.style.background = 'var(--accent)';
    else if (st.status === 'PAUSED') badge.style.background = '#f59e0b';
    else if (st.status === 'COMPLETED') badge.style.background = '#10b981';
    else if (st.status === 'FAILED' || st.status === 'ABORTED') badge.style.background = 'var(--error)';
  }

  const pBar = document.getElementById('agent-progress-bar');
  if (pBar && st.max_steps) {
    const pct = Math.min(100, Math.round((st.current_step / st.max_steps) * 100));
    pBar.style.width = `${pct}%`;
  }

  const stepCounter = document.getElementById('agent-step-counter');
  if (stepCounter) stepCounter.innerText = `Step: ${st.current_step} / ${st.max_steps}`;

  const timer = document.getElementById('agent-elapsed-timer');
  if (timer) timer.innerText = `Elapsed: ${st.elapsed_sec}s`;

  if (st.highlights) {
    renderOverlayHighlights(st.highlights);
  }

  const traceLog = document.getElementById('agent-trace-log');
  if (traceLog && st.actions && st.actions.length) {
    traceLog.innerHTML = '';
    st.actions.forEach(a => {
      const card = document.createElement('div');
      card.style.cssText = 'background: #111827; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-family: var(--font-mono); font-size: 11px;';
      const statusColor = a.status === 'success' ? '#10b981' : (a.status === 'error' ? '#ef4444' : '#9ca3af');
      card.innerHTML = `
        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
          <span style="font-weight: 600; color: var(--accent);">Step ${a.step}: ${escapeHtml(a.action)}</span>
          <span style="color: ${statusColor}; font-weight: 600;">${a.status.toUpperCase()} (${a.duration_sec}s)</span>
        </div>
        <div style="color: var(--text-secondary); margin-bottom: 4px;">Thought: ${escapeHtml(a.thought || '')}</div>
        <div style="color: var(--text-muted); font-size: 10px; word-break: break-all;">Args: ${escapeHtml(JSON.stringify(a.arguments || {}))}</div>
      `;
      traceLog.appendChild(card);
    });
    traceLog.scrollTop = traceLog.scrollHeight;
  }
}


function updateTokenTally(data) {
  if (!data) return;
  const tokensEl = document.getElementById('tally-tokens');
  const costEl = document.getElementById('tally-cost');
  if (tokensEl) tokensEl.innerText = (data.total_tokens || 0).toLocaleString();
  if (costEl) costEl.innerText = data.formatted_cost || `$${(data.total_cost_usd || 0).toFixed(4)}`;
}

async function fetchTokenUsage() {
  try {
    const res = await fetch('/api/usage');
    const data = await res.json();
    updateTokenTally(data);
  } catch (e) {}
}

function initWebSocket() {
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  const wsUrl = proto + location.host + '/ws/desktop';
  try {
    desktopWs = new WebSocket(wsUrl);

    desktopWs.onopen = () => {
      // Bi-directional 15s heartbeat
      if (wsHeartbeatTimer) clearInterval(wsHeartbeatTimer);
      wsHeartbeatTimer = setInterval(() => {
        if (desktopWs && desktopWs.readyState === WebSocket.OPEN) {
          desktopWs.send(JSON.stringify({ action: 'ping' }));
        }
      }, 15000);

      // Re-attach state upon connection
      desktopWs.send(JSON.stringify({ action: 'agent_status' }));
      desktopWs.send(JSON.stringify({ action: 'token_usage' }));
    };

    desktopWs.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.event === 'pong' || msg.event === 'heartbeat') {
          // Heartbeat acknowledged
        } else if (msg.event === 'agent_status') {
          if (msg.data) renderAgentStatus(msg.data);
        } else if (msg.event === 'step_complete' || msg.event === 'step_start' || msg.event === 'task_complete' || msg.event === 'task_start') {
          pollAgentStatus();
        } else if (msg.event === 'token_usage') {
          if (msg.data) updateTokenTally(msg.data);
        }
      } catch (e) {}
    };

    desktopWs.onclose = () => {
      if (wsHeartbeatTimer) clearInterval(wsHeartbeatTimer);
      wsReconnectTimer = setTimeout(initWebSocket, 3000);
    };

    desktopWs.onerror = () => {
      if (desktopWs) desktopWs.close();
    };
  } catch (e) {
    wsReconnectTimer = setTimeout(initWebSocket, 3000);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initWebSocket();
  fetchTokenUsage();
  calculateViewportScale();
});
setTimeout(() => {
  initWebSocket();
  fetchTokenUsage();
  calculateViewportScale();
}, 200);

</script>
</body>
</html>
"""


MODEL_PRICING: Dict[str, Dict[str, Any]] = {
    # Frontier tier ($ / 1M tokens)
    "opus 5.5": {"tier": "frontier", "prompt_rate": 15.00, "completion_rate": 75.00},
    "claude-3-opus": {"tier": "frontier", "prompt_rate": 15.00, "completion_rate": 75.00},
    "sonnet 5.5": {"tier": "frontier", "prompt_rate": 3.00, "completion_rate": 15.00},
    "claude-3.5-sonnet": {"tier": "frontier", "prompt_rate": 3.00, "completion_rate": 15.00},
    "claude-3-7-sonnet": {"tier": "frontier", "prompt_rate": 3.00, "completion_rate": 15.00},
    "gpt-6.1": {"tier": "frontier", "prompt_rate": 2.50, "completion_rate": 10.00},
    "sol 6.1 pro": {"tier": "frontier", "prompt_rate": 2.50, "completion_rate": 10.00},
    "gpt-4o": {"tier": "frontier", "prompt_rate": 2.50, "completion_rate": 10.00},

    # Standard tier ($ / 1M tokens)
    "gemini 3.8": {"tier": "standard", "prompt_rate": 0.50, "completion_rate": 1.50},
    "gemini-2.5-flash": {"tier": "standard", "prompt_rate": 0.15, "completion_rate": 0.60},
    "glm 5.3 flash": {"tier": "standard", "prompt_rate": 0.10, "completion_rate": 0.10},
    "deepseek 4.1 flash": {"tier": "standard", "prompt_rate": 0.14, "completion_rate": 0.28},
    "deepseek-chat": {"tier": "standard", "prompt_rate": 0.14, "completion_rate": 0.28},

    # Free tier ($ / 1M tokens)
    "free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "qwen": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "qwen-3.8-27b:free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "deepseek-chat:free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "llama-3.3-70b-instruct:free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},

    # Default rate
    "default": {"tier": "standard", "prompt_rate": 1.00, "completion_rate": 3.00},
}


class TokenAccountingManager:
    """Manages token accounting, cost calculations, and usage summaries across model tiers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._total_prompt_tokens: int = 0
            self._total_completion_tokens: int = 0
            self._total_cost_usd: float = 0.0
            self._records: List[Dict[str, Any]] = []
            self._tier_breakdown: Dict[str, Dict[str, Any]] = {
                "frontier": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
                "standard": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
                "free": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
            }

    @staticmethod
    def resolve_pricing(model: str) -> Dict[str, Any]:
        clean = (model or "").lower().strip()
        for k, v in MODEL_PRICING.items():
            if k == clean or k in clean:
                return v
        return MODEL_PRICING["default"]

    def record_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Dict[str, Any]:
        pt = max(0, int(prompt_tokens))
        ct = max(0, int(completion_tokens))
        tot = pt + ct
        pricing = self.resolve_pricing(model)
        tier = pricing["tier"]
        prompt_rate = pricing["prompt_rate"]
        completion_rate = pricing["completion_rate"]

        cost_prompt = (pt / 1_000_000.0) * prompt_rate
        cost_completion = (ct / 1_000_000.0) * completion_rate
        total_cost = cost_prompt + cost_completion

        entry = {
            "model": model,
            "tier": tier,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tot,
            "prompt_cost_usd": round(cost_prompt, 8),
            "completion_cost_usd": round(cost_completion, 8),
            "total_cost_usd": round(total_cost, 8),
            "timestamp": time.time(),
        }

        with self._lock:
            self._total_prompt_tokens += pt
            self._total_completion_tokens += ct
            self._total_cost_usd += total_cost
            self._records.append(entry)
            if tier in self._tier_breakdown:
                self._tier_breakdown[tier]["prompt_tokens"] += pt
                self._tier_breakdown[tier]["completion_tokens"] += ct
                self._tier_breakdown[tier]["cost_usd"] = round(
                    self._tier_breakdown[tier]["cost_usd"] + total_cost, 8
                )

        return entry

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
                "total_cost_usd": round(self._total_cost_usd, 8),
                "record_count": len(self._records),
                "tier_breakdown": {
                    k: dict(v) for k, v in self._tier_breakdown.items()
                },
            }


_GLOBAL_TOKEN_ACCOUNTING: Optional[TokenAccountingManager] = None


def get_token_accounting() -> TokenAccountingManager:
    global _GLOBAL_TOKEN_ACCOUNTING
    if _GLOBAL_TOKEN_ACCOUNTING is None:
        _GLOBAL_TOKEN_ACCOUNTING = TokenAccountingManager()
    return _GLOBAL_TOKEN_ACCOUNTING


def reset_token_accounting() -> None:
    global _GLOBAL_TOKEN_ACCOUNTING
    if _GLOBAL_TOKEN_ACCOUNTING is not None:
        _GLOBAL_TOKEN_ACCOUNTING.reset()
    else:
        _GLOBAL_TOKEN_ACCOUNTING = TokenAccountingManager()


class ResponsiveCoordinateScaler:
    """
    Computes responsive canvas scaling and coordinate transformations between
    physical screen pixels, dynamic viewport/canvas dimensions, and normalized [0, 1000] grid.
    """

    def __init__(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
        canvas_width: int = 960,
        canvas_height: int = 540,
    ) -> None:
        self.screen_width = max(1, int(screen_width))
        self.screen_height = max(1, int(screen_height))
        self.canvas_width = max(1, int(canvas_width))
        self.canvas_height = max(1, int(canvas_height))

    @property
    def scale_x(self) -> float:
        return self.canvas_width / self.screen_width

    @property
    def scale_y(self) -> float:
        return self.canvas_height / self.screen_height

    def update_canvas_dimensions(self, width: int, height: int) -> None:
        self.canvas_width = max(1, int(width))
        self.canvas_height = max(1, int(height))

    def update_screen_dimensions(self, width: int, height: int) -> None:
        self.screen_width = max(1, int(width))
        self.screen_height = max(1, int(height))

    def canvas_to_normalized(self, cx: float, cy: float) -> Tuple[float, float]:
        """Convert dynamic canvas pixel coordinate to normalized [0.0, 1000.0] grid."""
        nx = max(0.0, min(1000.0, round((cx / self.canvas_width) * 1000.0, 2)))
        ny = max(0.0, min(1000.0, round((cy / self.canvas_height) * 1000.0, 2)))
        return nx, ny

    def normalized_to_canvas(self, nx: float, ny: float) -> Tuple[int, int]:
        """Convert normalized [0.0, 1000.0] grid coordinate to canvas pixel coordinate."""
        cx = int(round((max(0.0, min(1000.0, nx)) / 1000.0) * self.canvas_width))
        cy = int(round((max(0.0, min(1000.0, ny)) / 1000.0) * self.canvas_height))
        return cx, cy

    def normalized_to_screen(self, nx: float, ny: float) -> Tuple[int, int]:
        """Convert normalized [0.0, 1000.0] coordinate to physical screen pixels."""
        sx = int(round((max(0.0, min(1000.0, nx)) / 1000.0) * self.screen_width))
        sy = int(round((max(0.0, min(1000.0, ny)) / 1000.0) * self.screen_height))
        return sx, sy

    def screen_to_normalized(self, sx: float, sy: float) -> Tuple[float, float]:
        """Convert physical screen pixels to normalized [0.0, 1000.0] coordinate."""
        nx = max(0.0, min(1000.0, round((sx / self.screen_width) * 1000.0, 2)))
        ny = max(0.0, min(1000.0, round((sy / self.screen_height) * 1000.0, 2)))
        return nx, ny

    def canvas_to_screen(self, cx: float, cy: float) -> Tuple[int, int]:
        """Convert canvas pixel coordinate directly to physical screen pixels."""
        nx, ny = self.canvas_to_normalized(cx, cy)
        return self.normalized_to_screen(nx, ny)

    def screen_to_canvas(self, sx: float, sy: float) -> Tuple[int, int]:
        """Convert physical screen pixels directly to canvas pixel coordinate."""
        nx, ny = self.screen_to_normalized(sx, sy)
        return self.normalized_to_canvas(nx, ny)



class TokenTracker:
    """Thread-safe token usage and cost accounting ledger for Hydra Desktop."""

    RATES: Dict[str, Dict[str, float]] = {
        "claude-3-7-sonnet": {"prompt": 3.00, "completion": 15.00},
        "sonnet 5.5": {"prompt": 3.00, "completion": 15.00},
        "opus 5.5": {"prompt": 15.00, "completion": 75.00},
        "sol 6.1 pro": {"prompt": 2.50, "completion": 10.00},
        "gpt-6.1": {"prompt": 2.50, "completion": 10.00},
        "gemini-2.5-flash": {"prompt": 0.15, "completion": 0.60},
        "gemini 3.8": {"prompt": 0.15, "completion": 0.60},
        "glm 5.3 flash": {"prompt": 0.10, "completion": 0.40},
        "deepseek 4.1 flash": {"prompt": 0.10, "completion": 0.40},
        "qwen-3.8-27b:free": {"prompt": 0.0, "completion": 0.0},
        "deepseek-chat:free": {"prompt": 0.0, "completion": 0.0},
        "llama-3.3-70b-instruct:free": {"prompt": 0.0, "completion": 0.0},
        "free": {"prompt": 0.0, "completion": 0.0},
        "local": {"prompt": 0.0, "completion": 0.0},
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.by_model: Dict[str, Dict[str, Any]] = {}

    def _get_rates_for_model(self, model: str) -> Dict[str, float]:
        m = model.lower()
        if ":free" in m or m == "free" or m == "local" or "@cf/" in m:
            return {"prompt": 0.0, "completion": 0.0}
        for key, r in self.RATES.items():
            if key in m:
                return r
        return {"prompt": 1.00, "completion": 3.00}

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int) -> Dict[str, Any]:
        with self._lock:
            pt = max(0, int(prompt_tokens))
            ct = max(0, int(completion_tokens))
            tot = pt + ct
            rates = self._get_rates_for_model(model)
            cost = (pt * rates["prompt"] + ct * rates["completion"]) / 1_000_000.0

            self.prompt_tokens += pt
            self.completion_tokens += ct
            self.total_tokens += tot
            self.total_cost_usd += cost

            m_key = model.lower()
            if m_key not in self.by_model:
                self.by_model[m_key] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
            self.by_model[m_key]["prompt_tokens"] += pt
            self.by_model[m_key]["completion_tokens"] += ct
            self.by_model[m_key]["total_tokens"] += tot
            self.by_model[m_key]["cost_usd"] += cost

            return self.get_usage()

    def get_usage(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "total_cost_usd": round(self.total_cost_usd, 6),
                "formatted_cost": f"${self.total_cost_usd:.4f}",
                "by_model": dict(self.by_model),
            }

    def reset(self) -> None:
        with self._lock:
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.total_tokens = 0
            self.total_cost_usd = 0.0
            self.by_model.clear()


_TOKEN_TRACKER: Optional[TokenTracker] = None


def get_token_tracker() -> TokenTracker:
    global _TOKEN_TRACKER
    if _TOKEN_TRACKER is None:
        _TOKEN_TRACKER = TokenTracker()
    return _TOKEN_TRACKER


FALLBACK_CHAINS: Dict[str, List[str]] = {
    "sonnet 5.5": ["gemini 3.8", "gpt-6.1", "qwen"],
    "opus 5.5": ["sonnet 5.5", "gpt-6.1", "gemini 3.8"],
    "gpt-6.1": ["sonnet 5.5", "gemini 3.8", "qwen"],
    "default": ["gemini 3.8", "free", "glm 5.3 flash"],
}


def resolve_and_complete_with_fallback(
    model: str,
    prompt: str,
    fallbacks: Optional[List[str]] = None,
    completer: Optional[Callable[[str, str], str]] = None,
) -> Dict[str, Any]:
    """
    Resolve model alias and execute completion with fallback resilience under 429 rate limits.
    """
    from hydra_cli.config import MODEL_MAP
    from hydra_cli import complete

    completer_fn = completer or complete
    chain = [model]
    if fallbacks:
        chain.extend(fallbacks)
    else:
        chain.extend(FALLBACK_CHAINS.get(model.lower(), FALLBACK_CHAINS["default"]))

    attempts = []
    last_err = None

    for m in chain:
        resolved_id = MODEL_MAP.get(m.lower(), m)
        try:
            ans = completer_fn(resolved_id, prompt)
            pt = len(prompt.split())
            ct = len(ans.split())
            token_rec = get_token_accounting().record_usage(m, pt, ct)
            return {
                "isError": False,
                "content": ans,
                "model_requested": model,
                "model_used": m,
                "resolved_model_id": resolved_id,
                "fallback_triggered": (m != model),
                "attempts": attempts,
                "usage": {
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": pt + ct,
                },
                "token_accounting": token_rec,
            }
        except Exception as exc:
            err_str = str(exc)
            attempts.append({"model": m, "error": err_str})
            last_err = exc
            is_rate_limit = "429" in err_str or "rate limit" in err_str.lower() or "quota" in err_str.lower()
            if not is_rate_limit and len(attempts) > 1:
                pass

    return {
        "isError": True,
        "error": f"All model providers in fallback chain failed: {last_err}",
        "model_requested": model,
        "attempts": attempts,
    }


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
                from hydra_cli import complete
                res = complete(model, prompt)
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
        res = await asyncio.to_thread(runner.run_command, cmd, cwd=cwd)
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
        body_params = dict(body)
        action = body_params.pop("action", "")
        if not action:
            return JSONResponse({"error": "No action provided"}, status_code=400)

        engine = get_computer_use_engine()
        res = await asyncio.to_thread(engine.dispatch, action, **body_params)
        return res

    @app.api_route("/api/computer/screen", methods=["GET", "POST"])
    async def computer_screen(req: Request, as_json: bool = False):
        if req.method == "POST":
            try:
                body = await req.json()
                if "as_json" in body:
                    as_json = bool(body["as_json"])
            except Exception:
                pass
        engine = get_computer_use_engine()
        res = await asyncio.to_thread(engine.screen.capture, as_base64=True)
        if as_json:
            return res
        b64 = res.get("base64", "")
        raw = base64.b64decode(b64) if b64 else b""
        return Response(content=raw, media_type="image/png")

    @app.post("/api/browser/action")
    async def browser_action(req: Request):
        body = await req.json()
        body_params = dict(body)
        action = body_params.pop("action", "browser_navigate")
        engine = get_computer_use_engine()
        res = await asyncio.to_thread(engine.dispatch, action, **body_params)
        return res

    @app.post("/api/agent/run")
    @app.post("/api/agent/start")
    async def agent_start(req: Request):
        from hydra_cli.agent_runner import get_agent_runner
        body = await req.json()
        task = body.get("task", "")
        model = body.get("model", "sonnet 5.5")
        steps = body.get("steps")
        max_steps = int(body.get("max_steps", 30))
        timeout_sec = float(body.get("timeout_sec", 300.0))
        async_run = bool(body.get("async", False))

        runner = get_agent_runner()
        if async_run:
            runner.run_task_async(task_description=task, steps=steps, max_steps=max_steps, timeout_sec=timeout_sec)
            return {"isError": False, "status": "running", "task": task, "async": True}
        res = await asyncio.to_thread(runner.run_task, task_description=task, steps=steps, max_steps=max_steps, timeout_sec=timeout_sec)
        return res

    @app.post("/api/agent/pause")
    async def agent_pause():
        from hydra_cli.agent_runner import get_agent_runner
        runner = get_agent_runner()
        return runner.pause()

    @app.post("/api/agent/resume")
    async def agent_resume():
        from hydra_cli.agent_runner import get_agent_runner
        runner = get_agent_runner()
        return runner.resume()

    @app.get("/api/agent/status")
    async def agent_status():
        from hydra_cli.agent_runner import get_agent_runner
        runner = get_agent_runner()
        return runner.get_status()

    @app.post("/api/agent/abort")
    async def agent_abort():
        from hydra_cli.agent_runner import get_agent_runner
        runner = get_agent_runner()
        return runner.abort()

    
    @app.get("/api/usage")
    async def get_usage_endpoint():
        return get_token_tracker().get_usage()

    @app.post("/api/usage/record")
    async def record_usage_endpoint(req: Request):
        body = await req.json()
        model = body.get("model", "sonnet 5.5")
        pt = int(body.get("prompt_tokens", 0))
        ct = int(body.get("completion_tokens", 0))
        return get_token_tracker().record_usage(model, pt, ct)

    @app.post("/api/usage/reset")
    async def reset_usage_endpoint():
        tracker = get_token_tracker()
        tracker.reset()
        return tracker.get_usage()

    @app.get("/api/agent/export")
    @app.get("/api/agent/session/export")
    @app.get("/api/session/export")
    async def session_export(download: bool = False):
        from hydra_cli.agent_runner import get_agent_runner
        runner = get_agent_runner()
        trace = runner.export_session_trace()
        if download:
            content = json.dumps(trace, indent=2)
            filename = f"agent-session-trace-{int(time.time())}.json"
            return Response(
                content,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return JSONResponse(trace)

    @app.post("/api/agent/session/replay")
    @app.post("/api/session/replay")
    async def session_replay(req: Request):
        from hydra_cli.agent_runner import AutonomousAgentRunner, validate_session_trace
        body = await req.json()
        valid, err = validate_session_trace(body)
        if not valid:
            return JSONResponse({"isError": True, "error": err}, status_code=400)
        reconstructed = AutonomousAgentRunner.reconstruct_from_trace(body)
        return {"isError": False, "status": "reconstructed", "reconstructed_state": reconstructed.get_status()}

    @app.post("/api/gateway/complete")
    async def gateway_complete(req: Request):
        body = await req.json()
        model = body.get("model", "sonnet 5.5")
        prompt = body.get("prompt", "")
        fallbacks = body.get("fallbacks")
        res = await asyncio.to_thread(resolve_and_complete_with_fallback, model, prompt, fallbacks)
        if not res.get("isError"):
            pt = max(1, len(prompt) // 4)
            ct = max(1, len(res.get("content", "")) // 4)
            get_token_tracker().record_usage(res.get("model_used", model), pt, ct)
        return res



    @app.get("/api/token/accounting")
    async def get_token_accounting_summary():
        ledger = get_token_accounting()
        return ledger.get_summary()

    @app.post("/api/token/accounting/record")
    async def record_token_accounting(req: Request):
        body = await req.json()
        ledger = get_token_accounting()
        model = body.get("model", "default")
        pt = int(body.get("prompt_tokens", 0))
        ct = int(body.get("completion_tokens", 0))
        res = ledger.record_usage(model, pt, ct)
        return {"isError": False, "recorded": res, "summary": ledger.get_summary()}

    @app.post("/api/token/accounting/reset")
    async def reset_token_accounting_endpoint():
        reset_token_accounting()
        return {"isError": False, "status": "reset", "summary": get_token_accounting().get_summary()}

    @app.post("/api/coordinates/scale")
    async def scale_coordinates(req: Request):
        body = await req.json()
        action = body.get("action", "canvas_to_screen")
        x = float(body.get("x", 0))
        y = float(body.get("y", 0))
        sw = int(body.get("screen_width", 1920))
        sh = int(body.get("screen_height", 1080))
        cw = int(body.get("canvas_width", 960))
        ch = int(body.get("canvas_height", 540))

        scaler = ResponsiveCoordinateScaler(
            screen_width=sw,
            screen_height=sh,
            canvas_width=cw,
            canvas_height=ch,
        )

        if action == "canvas_to_screen":
            out_x, out_y = scaler.canvas_to_screen(x, y)
            nx, ny = scaler.canvas_to_normalized(x, y)
        elif action == "screen_to_canvas":
            out_x, out_y = scaler.screen_to_canvas(x, y)
            nx, ny = scaler.screen_to_normalized(x, y)
        elif action == "canvas_to_normalized":
            out_x, out_y = scaler.canvas_to_normalized(x, y)
            nx, ny = out_x, out_y
        elif action == "normalized_to_screen":
            out_x, out_y = scaler.normalized_to_screen(x, y)
            nx, ny = x, y
        elif action == "normalized_to_canvas":
            out_x, out_y = scaler.normalized_to_canvas(x, y)
            nx, ny = x, y
        else:
            return JSONResponse({"isError": True, "error": f"Unknown action: {action}"}, status_code=400)

        return {
            "isError": False,
            "action": action,
            "x": out_x,
            "y": out_y,
            "normalized": [nx, ny],
            "scale_x": scaler.scale_x,
            "scale_y": scaler.scale_y,
            "screen_dimensions": [sw, sh],
            "canvas_dimensions": [cw, ch],
        }

    @app.get("/v1/models")
    async def v1_models():
        from hydra_cli.serve import get_registered_models
        return get_registered_models()

    @app.post("/v1/chat/completions")
    async def v1_chat_completions(req: Request):
        from hydra_cli import complete
        body = await req.json()
        model = body.get("model", "sonnet 5.5")
        messages = body.get("messages", [])
        stream = bool(body.get("stream", False))

        prompt = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                prompt = m.get("content", "")
                break
        if not prompt and messages:
            prompt = str(messages[-1].get("content", ""))

        if stream:
            async def event_generator():
                created = int(time.time())
                try:
                    ans = complete(model, prompt)
                except Exception as e:
                    ans = f"Error: {e}"
                chunk_obj = {
                    "id": f"chatcmpl-desktop-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": ans},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(chunk_obj)}\n\n"
                end_obj = {
                    "id": f"chatcmpl-desktop-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }],
                }
                yield f"data: {json.dumps(end_obj)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        created = int(time.time())
        try:
            ans = complete(model, prompt)
        except Exception as e:
            ans = f"Error: {e}"

        return {
            "id": f"chatcmpl-desktop-{int(time.time())}",
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": ans},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(ans.split()),
                "total_tokens": len(prompt.split()) + len(ans.split()),
            },
        }


    _diff_records: List[Dict[str, Any]] = [
        {
            "path": "hydra_cli/computer_use.py",
            "type": "MODIFIED",
            "lines": [
                "@@ -180,6 +180,24 @@",
                "+    def fill_form(self, fields, form_selector=None, submit=False):",
                "+    def scroll_until_visible(self, selector, max_scrolls=10):",
                "+    def extract_table_data(self, selector='table'):",
                "+    def safe_drag_and_drop(self, from_coord, to_coord):",
                "+    def find_window_by_title_pattern(self, pattern):",
                "+    def set_window_bounds(self, hwnd, x, y, width, height):",
            ],
        }
    ]


    @app.get("/api/diffs")
    async def get_diffs():
        return {"diffs": _diff_records, "count": len(_diff_records)}

    @app.post("/api/diffs/record")
    async def record_diff(req: Request):
        body = await req.json()
        _diff_records.append(body)
        if len(_diff_records) > 100:
            _diff_records.pop(0)
        return {"isError": False, "recorded": True, "count": len(_diff_records)}

    @app.websocket("/ws/desktop")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        unsub_agent = None
        try:
            loop = asyncio.get_running_loop()
            from hydra_cli.agent_runner import get_agent_runner
            runner = get_agent_runner()

            def _ws_agent_cb(ev):
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(ev)), loop)
                except Exception:
                    pass

            unsub_agent = runner.subscribe(_ws_agent_cb)

            while True:
                msg_raw = await ws.receive_text()
                try:
                    if msg_raw.strip().lower() == "ping":
                        await ws.send_text(json.dumps({"event": "pong", "time": time.time()}))
                        continue

                    payload = json.loads(msg_raw)
                    action = payload.get("action") or payload.get("type") or "ping"
                    if action == "ping":
                        resp = {"event": "pong", "time": time.time()}
                        if "id" in payload:
                            resp["id"] = payload["id"]
                        if "seq" in payload:
                            resp["seq"] = payload["seq"]
                        await ws.send_text(json.dumps(resp))
                    elif action == "heartbeat":
                        await ws.send_text(json.dumps({
                            "event": "heartbeat_ack",
                            "time": time.time(),
                            "status": "healthy",
                        }))
                    elif action in ("reconnect", "attach"):
                        await ws.send_text(json.dumps({
                            "event": "attached",
                            "data": runner.get_status(),
                            "running_task": runner.current_task,
                            "reconnected": True,
                        }))
                    elif action == "chat":
                        prompt = payload.get("prompt", "")
                        await ws.send_text(json.dumps({"event": "token", "chunk": f"Summoned response: {prompt}"}))
                        await ws.send_text(json.dumps({"event": "done"}))
                    elif action == "screen":
                        engine = get_computer_use_engine()
                        cap = engine.screen.capture(as_base64=True)
                        await ws.send_text(json.dumps({"event": "screen", "data": cap}))
                    elif action == "agent_abort":
                        res = runner.abort()
                        await ws.send_text(json.dumps({"event": "agent_aborted", "data": res}))
                    elif action == "agent_status":
                        await ws.send_text(json.dumps({"event": "agent_status", "data": runner.get_status()}))
                    elif action == "token_accounting":
                        ledger = get_token_accounting()
                        await ws.send_text(json.dumps({"event": "token_accounting", "data": ledger.get_summary()}))
                except Exception as inner_exc:
                    await ws.send_text(json.dumps({"event": "error", "message": str(inner_exc)}))
        except WebSocketDisconnect:
            pass
        finally:
            if unsub_agent:
                unsub_agent()

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
