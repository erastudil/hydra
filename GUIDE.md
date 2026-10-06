# Hydra setup and integration

This page is the longer install and embed guide for Hydra 1.1.0. The command is `hydra`. The Python distribution name is `hydra-ai-cli`. The Node distribution name is `hydra-agent-cli`.

## Install

Pick one path.

```bash
pip install "git+https://github.com/erastudil/hydra.git"
pip install https://github.com/erastudil/hydra/releases/download/v1.1.0/hydra_ai_cli-1.1.0-py3-none-any.whl
npm install -g https://github.com/erastudil/hydra/releases/download/v1.1.0/hydra-agent-cli-1.1.0.tgz
```

```bash
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
```

```powershell
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

The shell installers copy `hydra_cli` and `bin` into `~/.hydra`. The Linux shim is `~/.local/bin/hydra`. The Windows commands are `hydra.cmd` and `hydra.ps1` in `%USERPROFILE%\.hydra\bin`. Python runs the vendored tree. Node is the fallback and reads `hydra_cli/catalog.json` beside `bin`. The installer exits with an error when Python and Node are both missing.

Run a release build without a global install:

```bash
npx --package https://github.com/erastudil/hydra/releases/download/v1.1.0/hydra-agent-cli-1.1.0.tgz hydra --version
```

Check the `SHA256SUMS` file on the [v1.1.0 release](https://github.com/erastudil/hydra/releases/tag/v1.1.0) before you install a downloaded archive.

## Credentials

Hydra reads the process environment, then `~/.hydra/.env`. A project `.env` may set `HYDRA_FREE_MODEL`, `HYDRA_LOCAL_MODEL`, `HYDRA_CLOUDFLARE_MODEL`, and `HYDRA_SYSTEM_PROMPT`. A key, token, password, host, or base URL in that project file is skipped. Set `HYDRA_TRUST_CWD_ENV=1` when that checkout is yours and you want those values loaded.

```bash
# ~/.hydra/.env
OPENROUTER_API_KEY=sk-or-v1-...
AI_GATEWAY_API_KEY=...
CHEAPERINFERENCE_API_KEY=ci_live_...
RUNPOD_API_KEY=rpa_...
RUNPOD_ENDPOINT_ID=... # or RUNPOD_ENDPOINT_URL=...
MODAL_ENDPOINT_URL=https://<app>.modal.run/v1
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=...
```

`VERCEL_AI_GATEWAY_TOKEN` fills in when `AI_GATEWAY_API_KEY` is empty.

Frontier calls try OpenRouter, Vercel AI Gateway, CheaperInference, RunPod, and Modal. `hydra free` uses Cloudflare when both Cloudflare variables are set, and otherwise the OpenRouter model named by `HYDRA_FREE_MODEL`. `hydra local` uses `LOCAL_AI_BASE`, then `OLLAMA_HOST`, then `LLAMACPP_HOST` when those variables are set. When they are empty it probes `127.0.0.1` on ports 11434, 8080, and 8000. Local mode does not call the cloud.

## Shell

```bash
hydra opus 5.5 high "State the invariants"
hydra sol 6.1 pro "Look for races"
hydra opus 5.5 -- high ground rules
git diff | hydra sonnet 5.5
git diff | hydra sonnet 5.5 - "Audit for security issues"
hydra sonnet 5.5 "Name the functions" --json --no-stream
```

`opus 5.5 high` is `anthropic/claude-opus-5.5` with reasoning effort `high`. `sol 6.1 pro` is `openai/gpt-6.1-sol` with effort `high` and reasoning mode `pro`. `--temperature` is omitted from the JSON body until you pass it. Opus 5.5 refuses the field, and the CLI exits 1 with that reason before any request is sent.

Stdin is read only when the prompt words are absent, or when one of those words is `-`. Pass the prompt as an argument from a program, and close or ignore the child stdin.

A provider that fails before the first token is followed by the next configured provider. A stream that breaks after tokens were printed is reported as truncated and the process exits 1. The idle wait is 180 seconds, or 600 seconds for effort `high`, `xhigh`, or `max`, and for any reasoning mode.

## Python

```python
from hydra_cli import complete

text = complete("sonnet 5.5", "Explain memory barriers")
```

The low-level call takes a URL, headers, a model id, and a messages list, and it returns a string:

```python
from hydra_cli.config import resolve_route
from hydra_cli.providers import fetch_chat_completion, get_frontier_providers

route = resolve_route("sol 6.1 pro")
provider = get_frontier_providers()[0]
text = fetch_chat_completion(
    url=provider["url"],
    headers=provider["headers"],
    model=route["model"],
    messages=[{"role": "user", "content": "Name one invariant"}],
)
```

From a subprocess, pass the prompt as an argument. `capture_output=True` records stdout. Add `stdin=subprocess.DEVNULL` so an inherited pipe cannot block the child.

```python
import subprocess

def query_hydra(alias: str, prompt: str) -> str:
    proc = subprocess.run(
        ["hydra", alias, prompt, "--no-stream"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()
```

## Node

```javascript
const { execFileSync } = require('node:child_process');

function callHydra(alias, prompt) {
  return execFileSync('hydra', [alias, prompt, '--no-stream'], {
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}
```

`stdio: ['ignore', 'pipe', 'pipe']` closes stdin. The alias and the prompt are array elements, so the shell does not parse them.

## Swarm

```bash
hydra swarm "Design a write-ahead log"
hydra swarm "Review the log design" --heads architect,auditor --json
```

The architect, coder, and auditor run together. The synthesizer runs after their text exists and is not a fourth parallel head. `--json` includes that synthesis. Each record has `status` of `ok` or `failed`. The exit code is 1 when any requested head or the synthesis fails.

| Head | Model |
| --- | --- |
| Architect | `anthropic/claude-opus-5.5`, effort `high` |
| Coder | `anthropic/claude-sonnet-5.5` |
| Auditor | `openai/gpt-6.1-sol`, effort `high`, mode `pro` |
| Synthesizer | `google/gemini-3.8-flash` |

## Aliases

`hydra --list-models` prints the whole map from `hydra_cli/catalog.json`. Useful entries:

| Alias | Model id | Fields |
| --- | --- | --- |
| `opus 5.5 high` | `anthropic/claude-opus-5.5` | effort `high` |
| `opus 5.5 fast` | `anthropic/claude-opus-5.5-fast` | |
| `sol 6.1 pro` | `openai/gpt-6.1-sol` | effort `high`, mode `pro` |
| `sol 6.1 fast` | `openai/gpt-6.1-sol-fast` | |
| `llama 4 scout` | `meta-llama/llama-4-scout` | |
| `llama 4` | `meta-llama/llama-4-maverick` | |
| `qwen coder` | `alibaba/qwen3-coder` | |

## Sovereign Gateway (`hydra serve`)

`hydra serve` starts a local standard-library HTTP server that exposes an OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoint:

```bash
hydra serve --port 7777 --host 127.0.0.1
```

Configure external agents (like Hermes or Pi) to route through Hydra:

```bash
export OPENAI_BASE_URL="http://127.0.0.1:7777/v1"
export OPENAI_API_KEY="sovereign-hydra"
```

Hydra resolves aliases in requested `model` headers, balances and falls back across configured frontier providers, and passes through tools and function calls transparently.

## MCP Tool Integration & Autonomous Agent Loop

Hydra includes a zero-dependency Model Context Protocol (MCP) client communicating via JSON-RPC 2.0 over standard I/O pipes.

```bash
# Autonomous ReAct agent with all active MCP tools
hydra agent "Inspect the latest commits and run the unit tests"

# Tool-augmented single alias summoning
hydra sonnet 5.5 --mcp "Search workspace.db for user records"

# Manage MCP community servers
hydra mcp list
hydra mcp test filesystem
hydra mcp config
hydra mcp init --force
```

Configuration is read from `~/.hydra/mcp_servers.json` or `./.hydra/mcp_servers.json`. `hydra mcp init` installs the default servers: `filesystem`, `fetch`, `sqlite`, `git`, `github` (Docker), `brave-search`, `memory`, and `playwright`. Servers receive a minimal environment, never your provider API keys, unless the config names a variable in `env` or `env_passthrough`.

## External Agent Delegation (Hermes & Pi)

In `hydra swarm`, specialist heads can delegate execution directly to local external agent binaries:

```bash
# Delegate architect to hermes and coder to pi
hydra swarm "Refactor router and verify tests" --heads architect:hermes,coder:pi,auditor
```

When `:hermes` or `:pi` is specified, Hydra executes `hermes -z "<prompt>"` or `pi -z "<prompt>"`. If the external runner is not installed, Hydra falls back to the configured model route.

## Terminal Green Banner (`hydra banner`)

Display the sovereign 7-headed Hydra ASCII art in retro terminal green:

```bash
hydra banner
```

Source: https://github.com/erastudil/hydra

