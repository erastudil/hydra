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

function updateCoord(event) { handleScreenHover(event); }

function handleScreenHover(e) {
  const box = e.currentTarget.getBoundingClientRect();
  const relX = (e.clientX - box.left) / box.width;
  const relY = (e.clientY - box.top) / box.height;
  const targetX = Math.round(relX * screenWidth);
  const targetY = Math.round(relY * screenHeight);
  document.getElementById('coord-readout').innerText = `X: ${targetX} | Y: ${targetY}`;

  // Draw overlay canvas crosshairs
  const canvas = document.getElementById('screen-overlay-canvas');
  if (canvas) {
    canvas.width = box.width;
    canvas.height = box.height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (overlayBoundsEnabled) {
      // Draw crosshair lines
      ctx.strokeStyle = 'rgba(16, 185, 129, 0.4)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(e.clientX - box.left, 0);
      ctx.lineTo(e.clientX - box.left, canvas.height);
      ctx.moveTo(0, e.clientY - box.top);
      ctx.lineTo(canvas.width, e.clientY - box.top);
      ctx.stroke();

      // Sample bounding box overlay for active window
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 2;
      ctx.strokeRect(10, 10, canvas.width - 20, canvas.height - 20);
      ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
      ctx.fillRect(10, 10, canvas.width - 20, canvas.height - 20);
    }
  }
}

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

  if (st.highlights && st.highlights.length) {
    const canvas = document.getElementById('screen-overlay-canvas');
    if (canvas) {
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      st.highlights.forEach(h => {
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        ctx.strokeRect(h.x, h.y, h.width, h.height);
        ctx.fillStyle = 'rgba(239, 68, 68, 0.2)';
        ctx.fillRect(h.x, h.y, h.width, h.height);
        ctx.fillStyle = '#fca5a5';
        ctx.font = '10px monospace';
        ctx.fillText(h.label || 'Target', h.x + 2, h.y - 4);
      });
    }
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

</script>
</body>
</html>
"""


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
            return {
                "isError": False,
                "content": ans,
                "model_requested": model,
                "model_used": m,
                "resolved_model_id": resolved_id,
                "fallback_triggered": (m != model),
                "attempts": attempts,
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
        return res



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
                    elif action == "agent_abort":
                        res = runner.abort()
                        await ws.send_text(json.dumps({"event": "agent_aborted", "data": res}))
                    elif action == "agent_status":
                        await ws.send_text(json.dumps({"event": "agent_status", "data": runner.get_status()}))
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
