# Hydra

```text
            [1]        [2]        [3]        [4]        [5]        [6]        [7]
           HERMES       PI      ARCHITECT  SOVEREIGN   CODER     AUDITOR   SYNTHESIS
          (\___/)    (\___/)    (\___/)    <(\___/)>   (\___/)    (\___/)    (\___/)
          /0   0\    /o   o\    /^   ^\    { 0   0 }   /^   ^\    /o   o\    /0   0\
         ( ==Y== )  ( ==v== )  ( ==w== )  (  ==X==  ) ( ==w== )  ( ==v== )  ( ==Y== )
          )     (    )     (    )     (   / )     ( \  )     (    )     (    )     (
         /       \  /       \  /       \ ( /       \ )/       \  /       \  /       \
        /   | |   \/   | |   \/   | |   \ V   | |   V /   | |   \/   | |   \/   | |   \
       |    | |        | |        | |    |    | |   |   | |        | |        | |    |
       \    \ \       / /        / /     |    | |   |    \ \        \ \       / /    /
        \    \ \_____/ /        / /      \    | |   /     \ \________\ \_____/ /    /
         \    \_______/        / /        \___/ \__/       \_______/  \_______/    /
          \                   / /          |       |        \                     /
           '.               .' /           |  VII  |         \                  .'
             '.           .'  /            |       |          \               .'
               '---------'   /             /_______\           \   '---------'
                            /             /         \           \
                           (             /   HYDRA   \           )
                            '._________.'|   CORE    |'._________.'
                                         \           /
                                          '---------'

  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
      Sovereign Multi-Headed AI Shell · v1.2.0
```

Hydra is a sovereign multi-headed command-line AI engine and model router. It dispatches single prompts, autonomous agentic loops, and multi-agent swarms across frontier models, free cloud tiers, local inference backends, and public Model Context Protocol (MCP) servers.

The Python package and the Node package share one alias catalog and operate with **zero external dependencies**, strictly utilizing runtime standard libraries.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](pyproject.toml)
[![Node](https://img.shields.io/badge/node-18+-success.svg)](package.json)

## New in v1.2.0

- **Autonomous ReAct Agent Loop (`hydra agent`)**: Multi-turn tool execution loop grounded in real-time environment actions.
- **Model Context Protocol (MCP) Client (`hydra mcp`)**: Zero-dependency stdio JSON-RPC 2.0 client supporting Filesystem, Fetch, SQLite, Git, GitHub, Brave Search, PostgreSQL, and Memory.
- **Sovereign OpenAI Gateway Server (`hydra serve`)**: Host an OpenAI-compatible local endpoint (`http://127.0.0.1:7777/v1`) for external agents like Hermes and Pi with transparent alias resolution and tool passthrough.
- **External Agent Runners (Hermes & Pi)**: Swarm heads can delegate execution directly to `hermes` and `pi` CLI binaries (`--heads architect:hermes,coder:pi,auditor`).
- **Phosphor Green 7-Headed Hydra ASCII Banner (`hydra banner`)**: Retro terminal green rendering of the sovereign 7-headed Hydra.

## Install

Python package name: `hydra-ai-cli`. Node package name: `hydra-agent-cli`. The command name on both is `hydra`.

From the Git repository:

```bash
pip install "git+https://github.com/erastudil/hydra.git"
```

From release archives:

```bash
pip install https://github.com/erastudil/hydra/releases/download/v1.2.0/hydra_ai_cli-1.2.0-py3-none-any.whl
npm install -g https://github.com/erastudil/hydra/releases/download/v1.2.0/hydra-agent-cli-1.2.0.tgz
```

Direct POSIX / Windows curl/irm installer:

```bash
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
```

```powershell
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

## Quick Start & Usage

```bash
# Frontier model summoning
hydra sonnet 5.5 "Explain cache coherence"
hydra opus 5.5 high "Verify distributed state machine safety"
hydra sol 6.1 pro "Perform security audit on auth token verification"

# Autonomous ReAct agent with MCP tools
hydra agent "Read pyproject.toml and list the entry points"
hydra sonnet 5.5 --mcp "Search repository for sqlite queries and summarize"

# Public MCP server management
hydra mcp list                      # List registered MCP tools
hydra mcp test filesystem           # Test connection to filesystem MCP server
hydra mcp config                    # View active ~/.hydra/mcp_servers.json

# Sovereign Gateway for Hermes, Pi, and external tools
hydra serve --port 7777             # Serves /v1/models and /v1/chat/completions

# Multi-agent swarm (with optional Hermes/Pi runner delegation)
hydra swarm "Architect and test a zero-copy ring buffer"
hydra swarm "Refactor parser" --heads architect:hermes,coder:pi,auditor

# Free Forge & Offline Local Inference
hydra free "Summarize this diff"     # Zero-cost Cloudflare / OpenRouter free models
hydra local "Generate unit test"     # Offline Ollama / llama.cpp / EasyLM

# Display terminal green 7-headed Hydra
hydra banner
```

## Model Context Protocol (MCP) Integration

Hydra v1.2.0 integrates community Model Context Protocol (MCP) servers using a native Python standard library JSON-RPC 2.0 stdio client.

Registered servers in `~/.hydra/mcp_servers.json` (or `./.hydra/mcp_servers.json`):

| Server | Command / Transport | Capabilities |
| --- | --- | --- |
| `filesystem` | `npx -y @modelcontextprotocol/server-filesystem` | Read, write, list files and directories |
| `fetch` | `npx -y @modelcontextprotocol/server-fetch` | HTTP retrieval and web markdown extraction |
| `sqlite` | `uvx mcp-server-sqlite --db-path ./workspace.db` | Schema inspection and SQL queries |
| `git` | `uvx mcp-server-git` | Git status, diff, log, and commits |
| `github` | `npx -y @modelcontextprotocol/server-github` | Issues, PRs, and repository management |
| `brave-search` | `npx -y @modelcontextprotocol/server-brave-search` | Live web search via Brave Search API |
| `memory` | `npx -y @modelcontextprotocol/server-memory` | Knowledge graph entity persistence |
| `postgres` | `npx -y @modelcontextprotocol/server-postgres` | PostgreSQL connection and query execution |
| `puppeteer` | `npx -y @modelcontextprotocol/server-puppeteer` | Headless browser automation and screenshots |

Environment variables in configuration (`${VAR_NAME}`) are automatically expanded from `~/.hydra/.env` and the process environment.

## Sovereign Gateway Server (`hydra serve`)

`hydra serve` starts a zero-dependency HTTP server on `127.0.0.1:7777` providing an OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoint.

This allows external coding agents such as **Hermes** and **Pi** to point directly to Hydra:

```bash
# In Hermes or another OpenAI-compatible agent
export OPENAI_BASE_URL="http://127.0.0.1:7777/v1"
export OPENAI_API_KEY="sovereign-hydra"
```

Hydra automatically resolves aliases (`opus 5.5`, `qwen coder`, `sol 6.1 pro`), handles provider failover, and passes through tools and function calls transparently.

## The Swarm Heads

`hydra swarm` runs specialist heads in parallel. The synthesizer runs after specialist outputs exist:

| Head | Model / Runner | Mandate |
| --- | --- | --- |
| **Hermes / Architect** | `hermes-agent` / `anthropic/claude-opus-5.5` | Invariant modeling, system architecture |
| **Pi / Coder** | `pi-coder` / `anthropic/claude-sonnet-5.5` | Production implementation, unit testing |
| **Auditor** | `openai/gpt-6.1-sol` (mode: `pro`) | Static security audit, edge cases, invariants |
| **Synthesizer** | `google/gemini-3.8-flash` | Synthesizes verified blueprint and roadmap |

## Call it from code

Python:

```python
from hydra_cli import complete, run_agent_loop, McpRegistry

# Direct prompt completion
print(complete("sonnet 5.5", "Explain memory barriers"))

# Autonomous tool-augmented agent
registry = McpRegistry()
result = run_agent_loop("sonnet 5.5", "Analyze repository structure", registry=registry)
registry.shutdown()
print(result)
```

Node.js:

```javascript
const { execFileSync } = require('node:child_process');

function callHydra(alias, prompt) {
  return execFileSync('hydra', [alias, prompt, '--no-stream'], {
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}
```

## Settings & Credentials

Credentials live in `~/.hydra/.env` or system environment:

| Variable | Role |
| --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter API key. Frontier calls try this first. |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway API key. |
| `VERCEL_AI_GATEWAY_TOKEN` | Fallback token for Vercel AI Gateway. |
| `CHEAPERINFERENCE_API_KEY` | CheaperInference key for high-throughput GLM/Kolibri models. |
| `RUNPOD_API_KEY` | RunPod API key for serverless endpoints and pod workers. |
| `MODAL_ENDPOINT_URL` | Modal vLLM endpoint URL for private open-weights deployments. |
| `CLOUDFLARE_API_TOKEN` & `CLOUDFLARE_ACCOUNT_ID` | Workers AI credentials for `hydra free`. |
| `OLLAMA_HOST`, `LLAMACPP_HOST`, `LOCAL_AI_BASE` | Local inference engine host endpoints. |
| `GITHUB_TOKEN` | Token for GitHub MCP server integration. |
| `BRAVE_API_KEY` | Key for Brave Search MCP server integration. |

## Tests

```bash
python -m pytest tests/
npm test
```

## License

Apache-2.0. Source and issues: https://github.com/erastudil/hydra
