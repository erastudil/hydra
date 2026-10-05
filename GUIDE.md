# Hydra Setup & Integration Guide

Complete reference for installing, configuring, and plugging **Hydra** into your terminal workflows, shell scripts, Python/Node applications, and autonomous multi-agent systems.

---

## Table of Contents
1. [Installation](#1-installation)
2. [Credentials & Provider Setup](#2-credentials--provider-setup)
3. [Interactive Shell & Pipe Workflows](#3-interactive-shell--pipe-workflows)
4. [Plugging Hydra into Applications (Python & Node)](#4-plugging-hydra-into-applications)
5. [Plugging Hydra into Autonomous Agents & Swarms](#5-plugging-hydra-into-autonomous-agents)
6. [Supported Models & Aliases](#6-supported-models--aliases)

---

## 1. Installation

Hydra requires **zero external dependencies** in both Python and Node.js. Choose the distribution channel best suited to your environment:

### Option A: Python (`pip`)
```bash
pip install hydra-ai-cli
# Or from local source:
pip install -e .
```

### Option B: Node.js / npm (`npx` or global)
```bash
# Instant zero-install execution:
npx hydra-cli sonnet 5.5 "Explain memory ordering"

# Global install:
npm install -g hydra-agent-cli
```

### Option C: Standalone One-Line Installers
```bash
# Linux / macOS (curl)
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash

# Windows (PowerShell)
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

---

## 2. Credentials & Provider Setup

Hydra resolves keys automatically from environment variables or a local `.env` file in your working directory.

### Provider Matrix

| Provider | Environment Variables | Usage & Billing |
| :--- | :--- | :--- |
| **Cloudflare Workers AI** | `CLOUDFLARE_API_TOKEN`<br>`CLOUDFLARE_ACCOUNT_ID` | Connects directly to Cloudflare edge models (Llama 3.3 70B, etc.). Use your existing Cloudflare paid plan or free quota. |
| **OpenRouter** | `OPENROUTER_API_KEY` | Unified billing across 200+ frontier models (Anthropic, OpenAI, Meta, Google, xAI, DeepSeek) + free tiers. |
| **Vercel AI Gateway** | `VERCEL_AI_GATEWAY_TOKEN` | Edge routing across frontier foundation models. |
| **Free Forge (No Keys)** | *(None required)* | Automatically routes to free endpoints (`meta-llama/llama-3.3-70b-instruct:free`, `gemini-2.0-flash-exp:free`, `deepseek-chat:free`). |
| **Offline Local Weights** | *(None required)* | Auto-detects local Ollama (`11434`), llama.cpp (`8080`), or EasyLM WebGPU (`8000`). |

### Quick Setup Example (`.env`)
```bash
# Place in ~/.hydra/.env or your project root
OPENROUTER_API_KEY="sk-or-v1-..."
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
```

---

## 3. Interactive Shell & Pipe Workflows

Hydra detects piped stdin non-blockingly using OS-level file descriptors (`PeekNamedPipe` on Windows, `select` on Unix).

### Piping Command Outputs Directly
```bash
# Audit a git commit or diff with Sonnet 5.5
git diff | hydra sonnet 5.5 "Audit for security issues and edge cases"

# Analyze log clusters without streaming into a variable
SUMMARY=$(cat /var/log/syslog | hydra free "Extract top 3 error clusters" --no-stream)

# Suggest a fix for compiler errors with Sol 6.1
cargo check 2>&1 | hydra sol 6.1 "Suggest exact minimal diff to fix errors"
```

### JSON Mode for Shell Scripting
Pass `--json` and `--no-stream` to retrieve machine-readable responses:
```bash
hydra sonnet 5.5 "Extract function names from file" --json --no-stream | jq '.content'
```

---

## 4. Plugging Hydra into Applications

### Python Integration

#### A. Direct Library Import (Zero Dependencies)
You can import Hydra directly in Python scripts and backend services:
```python
from hydra_cli.config import resolve_model
from hydra_cli.providers import fetch_chat_completion, get_frontier_providers

model_id = resolve_model("sonnet 5.5")
result = fetch_chat_completion(
    model=model_id,
    prompt="Explain RAFT leader election invariants.",
    system_prompt="You are a distributed systems architect.",
    providers=get_frontier_providers()
)

print(result.text)
print(f"Provider used: {result.provider}")
```

#### B. Subprocess Execution Pattern
For air-gapped or decoupled microservices:
```python
import subprocess

def query_hydra(alias: str, prompt: str) -> str:
    proc = subprocess.run(
        ["hydra", alias, prompt, "--no-stream"],
        capture_output=True,
        text=True,
        check=True
    )
    return proc.stdout.strip()

response = query_hydra("sonnet 5.5", "Refactor this SQL query for index optimization")
```

---

### Node.js / TypeScript Integration

Call Hydra synchronously or asynchronously in Node services:
```typescript
import { execSync } from "child_process";

export function callHydra(alias: string, prompt: string): string {
  const sanitized = prompt.replace(/"/g, '\\"');
  return execSync(`npx hydra-cli "${alias}" "${sanitized}" --no-stream`, {
    encoding: "utf-8",
    env: process.env,
  }).trim();
}

// Example usage
const analysis = callHydra("sonnet 5.5", "Analyze performance bottleneck in this event loop");
console.log(analysis);
```

---

## 5. Plugging Hydra into Autonomous Agents

Hydra is purpose-built to act as an external reasoning hand or fallback engine inside autonomous agents (e.g. LangChain, AutoGen, CrewAI, Antigravity, Aider).

### Agent Tool Schema (OpenAI / JSON Function Calling)
```json
{
  "name": "summon_hydra",
  "description": "Summon frontier models (Sonnet 5.5, Opus 5.5, Sol 6.1, Grok) or free/local offline models for code synthesis, architectural review, or invariant verification.",
  "parameters": {
    "type": "object",
    "properties": {
      "model_alias": {
        "type": "string",
        "description": "Model alias (e.g., 'sonnet 5.5', 'opus 5.5', 'sol 6.1', 'grok', 'free', 'local')"
      },
      "prompt": {
        "type": "string",
        "description": "The exact prompt or code snippet to analyze"
      }
    },
    "required": ["model_alias", "prompt"]
  }
}
```

### Multi-Agent Swarm Fan-Out
When tackling high-entropy challenges, run Hydra's native multi-headed swarm:
```bash
hydra swarm "Architect a low-latency distributed event log"
```

The swarm decomposes the goal and fires parallel threads:
- 🏛️ **Architect** (`claude-opus-5.5`): State invariants, boundaries, failure modes.
- ⚡ **Implementer** (`claude-sonnet-5.5`): Zero-dependency production-grade code.
- 🛡️ **Inspector** (`gpt-6.1-sol`): Security, race conditions, edge-case audit.
- 🔮 **Synthesizer** (`gemini-3.8-flash`): Prioritized execution roadmap.

---

## 6. Supported Models & Aliases

| Alias | Target Model ID | Role / Specialization |
| :--- | :--- | :--- |
| `sonnet 5.5` / `sonnet` | `anthropic/claude-sonnet-5.5` | Primary code synthesis & surgical refactoring |
| `opus 5.5` / `opus` | `anthropic/claude-opus-5.5` | Deep architectural invariants & system design |
| `sol 6.1` / `sol` | `openai/gpt-6.1-sol` | Formal reasoning, invariant verification & security |
| `gemini 3.8` / `gemini` | `google/gemini-3.8-flash` | High-throughput streaming, research & synthesis |
| `gemini 2.5` | `google/gemini-2.5-pro` | Million-token long-context synthesis |
| `grok 4.7` / `grok` | `x-ai/grok-4.7` | Uncensored technical audit & adversarial testing |
| `llama 4` | `meta-llama/llama-4-maverick` | Next-gen open foundation frontier reasoning |
| `qwen 3.8` / `qwen` | `qwen/qwen3.8-27b` | High-accuracy open reasoning & code generation |
| `qwen 3b` | `qwen/qwen-2.5-3b-instruct` | Compact, low-footprint local reasoning |
| `free` | `qwen/qwen3.8-27b:free` | Zero-cost public cloud inference |
| `local` | `qwen2.5-coder:latest` | 100% offline local inference (Ollama/llama.cpp/EasyLM) |

Run `hydra --list-models` or `hydra setup` in your terminal anytime to inspect active models and configurations.
