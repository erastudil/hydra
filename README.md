# Hydra

```text
  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
      Sovereign Multi-Headed AI Shell
```

> **Zero-dependency, multi-provider AI CLI utility.**
> Connect your shell to frontier models, zero-cost cloud routers, local weights, or parallel multi-agent swarms. Pipes anywhere.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-brightgreen.svg)](pyproject.toml)
[![Node](https://img.shields.io/badge/node-18+-success.svg)](package.json)
[![Tests](https://img.shields.io/badge/tests-27%2F27%20passing-brightgreen.svg)](tests/)

---

## Why Hydra?

Terminal AI utilities often suffer from high latency, bloated dependency trees, non-standard CLI flags, and provider lock-in.

Hydra solves this with **zero external dependencies** and an intelligent multi-provider gateway:

- **Unified Frontier Summoning**: Route prompts to Anthropic (Sonnet 5.5, Opus 5.5), OpenAI (Sol 6.1, Luna, GPT-5.5), Google (Gemini 3.8, Gemini 2.5), xAI (Grok 4.7), and Meta (Llama 4) with intuitive compound aliases.
- **Dual Gateway Support**: Seamlessly authenticates against either **Vercel AI Gateway** or **OpenRouter**, automatically adapting model namespaces on the fly (e.g. `x-ai/` vs `spacexai/`, `meta-llama/` vs `meta/`, `qwen/` vs `alibaba/`).
- **Free Forge**: `hydra free` cruises on zero-cost cloud tiers (Cloudflare Workers AI, OpenRouter free models) without burning metered API credits.
- **Local Air-Gapped Inference**: `hydra local` auto-detects offline runtimes on your machine (Ollama, llama.cpp, or EasyLM WebGPU).
- **Multi-Agent Swarm Fan-Out**: `hydra swarm` fans out your prompt across 4 parallel specialized heads (Architect, Implementer, Inspector, Synthesizer) concurrently.
- **First-Class UNIX Pipes**: Seamlessly ingest standard input from `cat`, `git diff`, `grep`, or build logs into any model.
- **Zero Runtime Dependencies**: Written purely with Python standard libraries (`urllib.request`, `concurrent.futures`) and Node.js standard libraries (`https`, `child_process`).

---

## Installation

Choose any of the three distribution channels.

### 1. Python Package (`pip`)

Install from source or PyPI:
```bash
pip install hydra-ai-cli
```

Or install in editable mode from a local checkout:
```bash
git clone https://github.com/erastudil/hydra.git
cd hydra
pip install -e .
```

### 2. Node.js / npm (`npx` or global)

Run instantly without installation via `npx`:
```bash
npx hydra-cli sonnet 5.5 "Explain lock-free ring buffers"
```

Or install globally:
```bash
npm install -g hydra-agent-cli
```

### 3. Standalone Installer (One-Liner)

**Linux & macOS (curl / bash):**
```bash
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

---

## Quickstart

### 1. Configure Credentials

Hydra reads keys from your shell environment or a `.env` file in your current directory:

```bash
# Option A: OpenRouter (Unified access to 200+ models + free tiers)
export OPENROUTER_API_KEY="sk-or-v1-..."

# Option B: Vercel AI Gateway (Managed enterprise gateway)
export AI_GATEWAY_API_KEY="vercel_..."

# Option C: Cloudflare Workers AI (Zero-cost edge tier)
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ACCOUNT_ID="..."
```

*(No credentials? You can still run `hydra free` on public endpoints or `hydra local` with Ollama/llama.cpp!)*

### 2. Summon Frontier Models

```bash
# Claude 5.5 Sonnet (Default coding & refactoring workhorse)
hydra sonnet 5.5 "Implement an LRU cache in Rust with O(1) operations"

# Claude 5.5 Opus (Deep architectural invariants)
hydra opus 5.5 "Formalize Raft consensus invariants under network partition"

# OpenAI GPT-6.1 Sol (Logic verification & security audit)
hydra sol 6.1 "Audit this smart contract for reentrancy vulnerabilities"

# Google Gemini 3.8 Flash (High-throughput streaming & synthesis)
hydra gemini 3.8 "Synthesize this research paper into three core takeaways"

# xAI Grok 4.7 (Uncensored technical review)
hydra grok 4.7 "Find the architectural anti-patterns in this microservice diagram"

# Meta Llama 4 Maverick (Next-gen open frontier)
hydra llama 4 "Explain memory consistency models"
```

### 3. Cruise Free & Local

```bash
# Zero-cost cloud routing (uses Cloudflare Workers AI or OpenRouter free models)
hydra free "Explain how TCP window scaling works"

# Offline local inference (auto-detects Ollama on 11434, llama.cpp on 8080, EasyLM on 8000)
hydra local "Write a Python script to parse JSON lines"
```

### 4. Fan Out Parallel Swarms

```bash
# Spawns Architect, Implementer, Inspector, and Synthesizer concurrently
hydra swarm "Architect and implement an event-driven task queue with persistent WAL"
```

---

## Core Capabilities

### 1. UNIX Pipes & Shell Composition

Hydra automatically reads standard input when piped from another command. Piped content is structured cleanly into context:

```bash
# Refactor staged git changes before committing
git diff --staged | hydra sonnet 5.5 "Refactor for zero unnecessary heap allocations"

# Diagnose crashing server logs
journalctl -u nginx -n 50 | hydra opus 5.5 "Identify the failure root cause"

# Review code files directly
cat memory.rs | hydra sol 6.1 "Audit for potential data races and UB"

# Capture clean output into environment variables
SUMMARY=$(cat build.log | hydra free "Extract top 3 compilation errors" --no-stream)
```

### 2. Multi-Agent Swarm Fan-Out

Cut off one head, and three arise. `hydra swarm` fans out your task across 4 specialized parallel heads:

```bash
hydra swarm "Build a zero-allocation byte parser in C"
```

| Head | Model | Role |
| :--- | :--- | :--- |
| 🏛️ **Architect** | `anthropic/claude-opus-5.5` | Evaluates requirements, data flows, invariants, and failure modes. |
| ⚡ **Implementer** | `anthropic/claude-sonnet-5.5` | Delivers production-grade, zero-dependency implementation code. |
| 🛡️ **Inspector** | `openai/gpt-6.1-sol` | Audits edge cases, race conditions, memory leaks, and security flaws. |
| 🔮 **Synthesizer** | `google/gemini-3.8-flash` | Unifies perspectives into a single prioritized execution roadmap. |

Pass `--json` for machine-readable JSON output suitable for autonomous agent pipelines:
```bash
hydra swarm "Verify Paxos state transition correctness" --heads "architect,auditor" --json
```

### 3. Dual Gateway Auto-Adaptation

Hydra automatically translates model namespaces between **Vercel AI Gateway** and **OpenRouter**:

| Family | Hydra Alias | Vercel Gateway ID | OpenRouter ID |
| :--- | :--- | :--- | :--- |
| **Claude Sonnet 5.5** | `sonnet 5.5`, `sonnet` | `anthropic/claude-sonnet-5.5` | `anthropic/claude-sonnet-5.5` |
| **Claude Opus 5.5** | `opus 5.5`, `opus` | `anthropic/claude-opus-5.5` | `anthropic/claude-opus-5.5` |
| **Claude Haiku 4.5** | `haiku 4.5`, `haiku` | `anthropic/claude-haiku-4.5` | `anthropic/claude-haiku-4.5` |
| **Claude Fable 5.1** | `fable 5.1`, `fable` | `anthropic/claude-fable-5.1` | `anthropic/claude-fable-5.1` |
| **GPT-6.1 Sol** | `sol 6.1`, `sol` | `openai/gpt-6.1-sol` | `openai/gpt-6.1-sol` |
| **GPT-6 Luna** | `luna` | `openai/gpt-6-luna` | `openai/gpt-6-luna` |
| **GPT-6 Astra** | `astra` | `openai/gpt-6-astra` | `openai/gpt-6-astra` |
| **GPT-5.5** | `gpt-5.5`, `gpt-5` | `openai/gpt-5.5` | `openai/gpt-5.5` |
| **OpenAI o3** | `o3` | `openai/o3` | `openai/o3` |
| **Gemini 3.8 Flash** | `gemini 3.8`, `gemini` | `google/gemini-3.8-flash` | `google/gemini-3.8-flash` |
| **Gemini 3.5 Flash** | `gemini 3.5` | `google/gemini-3.5-flash` | `google/gemini-3.5-flash` |
| **Gemini 2.5 Pro** | `gemini 2.5` | `google/gemini-2.5-pro` | `google/gemini-2.5-pro` |
| **Grok 4.7** | `grok 4.7`, `grok` | `spacexai/grok-4.7` | `x-ai/grok-4.7` |
| **Llama 4 Maverick** | `llama 4`, `llama 4 maverick` | `meta/llama-4-maverick` | `meta-llama/llama-4-maverick` |
| **Llama 3.3 70B** | `llama 3.3`, `llama` | `meta/llama-3.3-70b` | `meta-llama/llama-3.3-70b-instruct` |
| **Qwen 3.8 27B** | `qwen 3.8`, `qwen` | `alibaba/qwen3.8-27b` | `qwen/qwen3.8-27b` |
| **Qwen 3 Coder** | `qwen coder`, `qwen-coder` | `alibaba/qwen3-coder` | `qwen/qwen-2.5-coder-32b-instruct` |
| **Qwen 3B Instruct** | `qwen 3b` | — | `qwen/qwen-2.5-3b-instruct` |
| **DeepSeek V3** | `deepseek` | — | `deepseek/deepseek-chat` |

*(You can also pass any unmapped raw model ID directly, e.g. `hydra mistralai/mistral-large "prompt"`).*

---

## Application & Agent Integration

Hydra is engineered to be embedded directly into scripts, backends, CI/CD pipelines, and autonomous agent systems.

For full setup documentation, see [**`GUIDE.md`**](GUIDE.md) or run:
```bash
hydra setup
# or
hydra guide
```

### 1. Python Tool Integration (LangChain, AutoGen, CrewAI, Antigravity)

Zero external dependencies—call Hydra as an agent tool or standard subprocess:

```python
import subprocess

def summon_hydra(model_alias: str, prompt: str, piped_input: str = "") -> str:
    """Invoke Hydra with deterministic output and zero external dependencies."""
    proc = subprocess.run(
        ["hydra", model_alias, prompt, "--no-stream"],
        input=piped_input,
        text=True,
        capture_output=True,
        check=True
    )
    return proc.stdout.strip()

# Example: Run code verification in an agent loop
audit = summon_hydra(
    model_alias="sol 6.1",
    prompt="Verify mutex locking bounds and return invariant violations",
    piped_input=open("ring_buffer.c").read()
)
print(audit)
```

Direct Python SDK import:
```python
from hydra_cli.providers import fetch_chat_completion, get_frontier_providers
from hydra_cli.config import resolve_model

model_id = resolve_model("sonnet 5.5")
response = fetch_chat_completion(
    url=get_frontier_providers()[0]["url"],
    headers=get_frontier_providers()[0]["headers"],
    model=model_id,
    messages=[{"role": "user", "content": "Explain memory barriers"}]
)
print(response)
```

### 2. Node.js & TypeScript Integration

```typescript
import { execFileSync } from 'child_process';

export function callHydra(model: string, prompt: string, input: string = ''): string {
  return execFileSync('hydra', [model, prompt, '--no-stream'], {
    input,
    encoding: 'utf-8',
    env: process.env
  }).trim();
}

// Example usage:
const code = callHydra('sonnet 5.5', 'Implement a debounce hook in React');
console.log(code);
```

### 3. Agent Function Calling Schema

Add Hydra to your model's tool schema:

```json
{
  "name": "summon_hydra",
  "description": "Summon frontier models (Sonnet 5.5, Opus 5.5, Sol 6.1, Grok 4.7) or free/local offline models for code synthesis, architectural review, or invariant verification.",
  "parameters": {
    "type": "object",
    "properties": {
      "model_alias": {
        "type": "string",
        "description": "Model alias (e.g. 'sonnet 5.5', 'opus 5.5', 'sol 6.1', 'gemini 3.8', 'grok 4.7', 'free', 'local')"
      },
      "prompt": {
        "type": "string",
        "description": "The exact prompt or instruction"
      }
    },
    "required": ["model_alias", "prompt"]
  }
}
```

---

## Configuration Reference

Configure credentials via environment variables or a `.env` file:

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENROUTER_API_KEY` | *(None)* | OpenRouter API authentication key |
| `AI_GATEWAY_API_KEY` | *(None)* | Vercel AI Gateway authentication key |
| `AI_GATEWAY_API_BASE` | `https://ai-gateway.vercel.sh/v1` | Custom Vercel Gateway base URL |
| `CLOUDFLARE_API_TOKEN` | *(None)* | Cloudflare API token for Workers AI |
| `CLOUDFLARE_ACCOUNT_ID` | *(None)* | Cloudflare account identifier |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama local server URL |
| `LLAMACPP_HOST` | `http://localhost:8080` | llama.cpp local server URL |
| `LOCAL_AI_BASE` | `http://localhost:8000` | Custom local inference base URL (e.g. EasyLM WebGPU) |
| `HYDRA_SYSTEM_PROMPT` | *(Built-in)* | Custom default system prompt |
| `HYDRA_FREE_MODEL` | `qwen/qwen3.8-27b:free` | Default model for `hydra free` |
| `HYDRA_LOCAL_MODEL` | `qwen2.5-coder:latest` | Default model for `hydra local` |

---

## Architecture

Hydra is architected for sovereign, zero-friction developer agility:

```text
               ┌──────────────────────────────────────────────┐
               │                  Hydra CLI                   │
               │         (Python Standard / Node.js)          │
               └──────────────────────┬───────────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│   Frontier Hub   │        │    Free Forge    │        │  Local Engines   │
│  (Vercel Gateway │        │  (Cloudflare /   │        │ (Ollama /        │
│  · OpenRouter)   │        │   OR Free Tier)  │        │  llama.cpp)      │
└─────────┬────────┘        └─────────┬────────┘        └─────────┬────────┘
          │                           │                           │
          │             Auto-Namespace Translation                │
          │      (x-ai/ ⇆ spacexai/, meta-llama/ ⇆ meta/)         │
          │                           │                           │
          └───────────────────────────┼───────────────────────────┘
                                      ▼
                        ┌──────────────────────────┐
                        │     Parallel Swarm       │
                        │ (Architect · Implementer │
                        │  · Inspector · Synth)    │
                        └──────────────────────────┘
```

- **SSE Streaming**: Chunks are processed in flight with immediate standard output flushing.
- **Provider Fallback**: If OpenRouter returns an error or rate limit, Hydra seamlessly falls back to Vercel AI Gateway (and vice versa).
- **Graceful Port Probing**: Fast non-blocking socket checks (400ms timeout) determine whether local servers are listening before dispatching local prompts.

---

## Verification & Testing

Hydra includes a comprehensive test suite covering argument parsing, model alias resolution, provider routing, streaming SSE chunks, and parallel swarm fan-out.

Run the test suite:
```bash
python -m pytest tests/
```

Verify installed model aliases and endpoints:
```bash
hydra --list-models
```

---

## Contributing & License

Contributions are welcome! Submit PRs or open issues at [https://github.com/erastudil/hydra](https://github.com/erastudil/hydra).

Distributed under the [Apache-2.0 License](LICENSE). © 2026 erastudil
