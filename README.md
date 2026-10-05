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

When you query an AI model from the terminal, you often wrestle with bespoke API clients, non-standard flags, fragmented SDKs, or high latencies.

Hydra routes your terminal prompt to the right engine instantly. One binary connects your shell to frontier models, zero-cost cloud routers, local weights running on your machine, or a parallel multi-agent swarm.

---

## Installation

Choose any of the three distribution channels.

### 1. Standalone Installer

**Linux & macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
```

**Windows PowerShell:**
```powershell
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

### 2. Python Package

Install from source or PyPI:
```bash
pip install hydra-ai-cli
```
Or from a local repository checkout:
```bash
pip install .
```

### 3. Node.js & npm

Install globally:
```bash
npm install -g hydra-agent-cli
```
Or run directly without installation via `npx`:
```bash
npx hydra-cli sonnet 3.7 "Explain memory ordering in Rust"
```

---

## Quickstart

Set your preferred API key:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
# or
export AI_GATEWAY_API_KEY="vercel_..."
```

Summon a frontier model:
```bash
hydra opus 5.5 "Explain zero-cost abstractions"
```

Stream a zero-cost response without consuming credits:
```bash
hydra free "List the three invariants of Raft consensus"
```

Run completely offline against local weights:
```bash
hydra local "Write a lock-free ring buffer in C"
```

Fan out a parallel swarm of specialized heads:
```bash
hydra swarm "Design and implement a deterministic state machine"
```

---

## Core Capabilities

### 1. Direct Model Summoning

Hydra resolves intuitive compound aliases into provider model identifiers and streams the response token-by-token:

```bash
hydra opus 5.5 "Analyze this architecture"
hydra sol 6.1 "Prove this logic invariant"
hydra sonnet 3.7 "Refactor this module"
hydra gemini 2.5 "Summarize this paper"
hydra qwen 3b "Write an assembly routine"
hydra grok "Trace edge cases"
hydra llama "Explain vector clocks"
```

Pass custom system instructions or generation parameters:
```bash
hydra opus 5.5 "Design a protocol" --system "Be rigorous and mathematical." --temperature 0.2
```

### 2. UNIX Pipe Integration

Hydra reads standard input automatically when piped from other commands:

```bash
# Analyze a source file
cat engine.rs | hydra sonnet 3.7 "Identify data races"

# Review git changes before committing
git diff | hydra sol 6.1 "Audit this patch for security flaws"

# Explain system log errors
journalctl -u nginx -n 50 | hydra opus 5.5 "Diagnose the 502 gateway error"
```

### 3. Zero-Cost Free Forge Routing

`hydra free` sends prompts to zero-cost cloud tiers without burning metered credits:

```bash
hydra free "Explain how TCP flow control works"
```

Hydra checks Cloudflare Workers AI credentials first (`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`). If not configured, it routes to OpenRouter free models (`meta-llama/llama-3.3-70b-instruct:free`, `google/gemini-2.0-flash-exp:free`, `deepseek/deepseek-chat:free`).

### 4. Local Inference Routing

`hydra local` connects to offline model runtimes on your hardware:

```bash
hydra local "Generate a unit test for this struct"
```

Hydra automatically detects running local runtimes in priority order:
1. `LOCAL_AI_BASE` (custom local endpoint)
2. Ollama (`http://localhost:11434/v1`)
3. llama.cpp server (`http://localhost:8080/v1`)
4. EasyLM WebGPU endpoint (`http://localhost:8000/v1`)

### 5. Multi-Agent Swarm Fan-Out

Cut off one head, and three arise. `hydra swarm` fans out your task across parallel agent heads concurrently:

```bash
hydra swarm "Implement a transactional key-value store with WAL"
```

The default swarm spawns:
- **Architect Head** (`anthropic/claude-opus-5.5`): Evaluates invariants, failure modes, and boundaries.
- **Implementer Head** (`anthropic/claude-3.7-sonnet`): Produces working code with zero unnecessary dependencies.
- **Inspector Head** (`openai/gpt-6.1-sol-pro`): Audits edge cases, race conditions, and attack vectors.
- **Synthesizer Head** (`google/gemini-2.5-pro`): Resolves tradeoffs and unifies the roadmap.

Customize active heads or output JSON:
```bash
hydra swarm "Verify Paxos correctness" --heads "architect,auditor" --json
```

---

## Supported Aliases

| Alias | Target Model Identifier | Typical Use |
|---|---|---|
| `opus 5.5` | `anthropic/claude-opus-5.5` | Deep reasoning and systems design |
| `sol 6.1` | `openai/gpt-6.1-sol-pro` | Formal logic and security audit |
| `sonnet 3.7` | `anthropic/claude-3.7-sonnet` | Code generation and refactoring |
| `gemini 2.5` | `google/gemini-2.5-pro` | High context analysis and synthesis |
| `gemini 3.5` | `google/gemini-2.5-flash` | Fast summarization and transformation |
| `qwen 3b` | `qwen/qwen-2.5-3b-instruct` | Lightweight embedded tasks |
| `qwen` | `qwen/qwen-2.5-coder-32b-instruct` | Open coding model |
| `grok` | `x-ai/grok-2-1212` | Uncensored technical review |
| `llama` | `meta-llama/llama-3.3-70b-instruct` | Open frontier reasoning |
| `deepseek` | `deepseek/deepseek-chat` | General technical assistance |
| `free` | Free Forge Tier | Zero-cost development |
| `local` | Ollama / llama.cpp / EasyLM | Offline air-gapped development |

You can also pass any unmapped model ID directly (for example `hydra mistralai/mistral-large "prompt"`).

---

## Configuration

Configure credentials via environment variables:

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API authentication token |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway authentication token |
| `AI_GATEWAY_API_BASE` | Custom Vercel Gateway base URL (default: `https://ai-gateway.vercel.sh/v1`) |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token for Workers AI |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account identifier |
| `OLLAMA_HOST` | Ollama server host (default: `http://localhost:11434`) |
| `LLAMACPP_HOST` | llama.cpp server host (default: `http://localhost:8080`) |
| `LOCAL_AI_BASE` | Custom local inference base URL |
| `HYDRA_SYSTEM_PROMPT` | Custom default system prompt |

---

## Architecture

Hydra relies on zero external runtime dependencies. The Python distribution uses standard library modules (`urllib.request`, `json`, `concurrent.futures`, `socket`). The Node.js distribution uses standard library packages (`https`, `http`, `net`, `process`).

```text
               ┌──────────────────────────────────────────────┐
               │                  Hydra CLI                   │
               └──────────────────────┬───────────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│   Frontier Hub   │        │    Free Forge    │        │  Local Engines   │
│  (OpenRouter /   │        │  (Cloudflare /   │        │ (Ollama /        │
│   Vercel AI)     │        │   OR Free Tier)  │        │  llama.cpp)      │
└─────────┬────────┘        └─────────┬────────┘        └─────────┬────────┘
          │                           │                           │
          └───────────────────────────┼───────────────────────────┘
                                      ▼
                        ┌──────────────────────────┐
                        │     Parallel Swarm       │
                        │ (Architect · Implementer │
                        │  · Inspector · Synth)    │
                        └──────────────────────────┘
```

Both implementations parse Server-Sent Events (SSE) chunks on the fly and write delta tokens directly to standard output.

---

## Development & Verification

Run the test suite:
```bash
python -m pytest tests/
```

Run CLI model registry inspection:
```bash
python bin/hydra --list-models
```

---

## License

[Apache-2.0](LICENSE) © 2026 erastudil
