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

Hydra is a command line tool that sends one prompt to a frontier model, a free cloud model, a local model server, or a small swarm. The Python package and the Node package share one alias catalog and use only the standard libraries of their runtimes.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](pyproject.toml)
[![Node](https://img.shields.io/badge/node-18+-success.svg)](package.json)

## Install

Python package name: `hydra-ai-cli`. Node package name: `hydra-agent-cli`. The command name on both is `hydra`.

From the Git repository:

```bash
pip install "git+https://github.com/erastudil/hydra.git"
```

From the v1.1.0 GitHub release:

```bash
pip install https://github.com/erastudil/hydra/releases/download/v1.1.0/hydra_ai_cli-1.1.0-py3-none-any.whl
npm install -g https://github.com/erastudil/hydra/releases/download/v1.1.0/hydra-agent-cli-1.1.0.tgz
```

The release page also carries the source archive and `SHA256SUMS`.

A checkout you can edit:

```bash
git clone https://github.com/erastudil/hydra.git
cd hydra
pip install -e .
```

Linux and macOS download the Python package tree into `~/.hydra` and put a `hydra` shim on `~/.local/bin`:

```bash
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
```

Windows does the same under `%USERPROFILE%\.hydra\bin`:

```powershell
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

The shim runs the vendored Python package when `python3` or `python` is present, and otherwise runs `bin/hydra.js`. The installer stops when neither runtime exists.

`npx` needs the release tarball, because the public name `hydra-cli` belongs to a different package:

```bash
npx --package https://github.com/erastudil/hydra/releases/download/v1.1.0/hydra-agent-cli-1.1.0.tgz hydra sonnet 5.5 "Explain a ring buffer"
```

## Keys

The process environment wins. Hydra then reads `~/.hydra/.env`. A `.env` in the working directory may set ordinary settings such as `HYDRA_FREE_MODEL`. Keys, tokens, and host URLs in that project file stay unloaded until you set `HYDRA_TRUST_CWD_ENV=1`.

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export AI_GATEWAY_API_KEY="..."
# VERCEL_AI_GATEWAY_TOKEN is accepted when AI_GATEWAY_API_KEY is empty.
export CHEAPERINFERENCE_API_KEY="ci_live_..."
export RUNPOD_API_KEY="rpa_..."
export RUNPOD_ENDPOINT_ID="..." # or export RUNPOD_ENDPOINT_URL="..."
export MODAL_ENDPOINT_URL="https://<app>.modal.run/v1"
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ACCOUNT_ID="..."
```

Frontier calls try OpenRouter, Vercel AI Gateway, CheaperInference, RunPod, and Modal. A failure before any text is printed tries the next configured provider. After text has been printed, Hydra stops, reports that the stream was truncated, and exits 1.

`hydra free` uses Cloudflare Workers AI when both Cloudflare variables are set. Otherwise it uses the OpenRouter free model. One of those credentials is required. `hydra local` never calls a cloud provider.

## Prompts

```bash
hydra sonnet 5.5 "Implement an LRU cache in Rust"
hydra opus 5.5 high "State the Raft invariants"
hydra sol 6.1 pro "Audit this function for races"
hydra llama 4 scout "Explain memory ordering"
hydra free "Explain TCP window scaling"
hydra local "Write a JSONL parser"
```

`opus 5.5 high` selects `anthropic/claude-opus-5.5` and sets reasoning effort to `high`. `sol 6.1 pro` selects `openai/gpt-6.1-sol`, sets effort to `high`, and sets reasoning mode to `pro`. Those are request fields. They are not separate model ids.

A `--` after the alias keeps the following words in the prompt:

```bash
hydra opus 5.5 -- high ground rules
```

Temperature is left off the request unless you pass `--temperature`. `anthropic/claude-opus-5.5` and `anthropic/claude-opus-5.5-fast` reject that field, and Hydra refuses the flag before the request.

A pipe is the prompt when you pass no prompt words. A lone `-` reads the pipe and keeps the other words as the instruction. A prompt that is already present leaves stdin unread, so a parent process that never closes stdin does not stall.

```bash
git diff | hydra sonnet 5.5
git diff | hydra sonnet 5.5 - "Audit for security issues"
```

`hydra --list-models` prints the catalog. An id that is not an alias is sent through as written. On a Vercel host, `x-ai/` becomes `spacexai/`, `meta-llama/` becomes `meta/` with a trailing `-instruct` removed, and `qwen/` becomes `alibaba/`. On OpenRouter those three rewrites run in reverse.

The quiet period on a socket is 180 seconds, or 600 seconds when effort is `high`, `xhigh`, or `max`, or when a reasoning mode is set.

## Swarm

`hydra swarm` runs the architect, coder, and auditor at the same time. The synthesizer runs once, after at least two of those heads return text. `--heads architect,auditor` still ends with that one synthesis when a specialist succeeds. `--json` prints every head, including the synthesizer, and each object has `status` of `ok` or `failed`. A failed head or a failed synthesis exits 1.

| Head | Model | Request |
| --- | --- | --- |
| Architect | `anthropic/claude-opus-5.5` | effort `high` |
| Coder | `anthropic/claude-sonnet-5.5` | |
| Auditor | `openai/gpt-6.1-sol` | effort `high`, mode `pro` |
| Synthesizer | `google/gemini-3.8-flash` | runs after the specialists |

`--model` replaces the catalog model for every head and drops the catalog effort and mode.

## Call it from code

Python:

```python
from hydra_cli import complete

print(complete("sonnet 5.5", "Explain memory barriers"))
```

`complete(alias, prompt, system_prompt=None)` returns the assistant string. It loads `~/.hydra/.env`, resolves the alias, and tries each configured frontier provider.

Node, with stdin closed so the child cannot wait on the parent:

```javascript
const { execFileSync } = require('node:child_process');

function callHydra(alias, prompt) {
  return execFileSync('hydra', [alias, prompt, '--no-stream'], {
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}
```

More recipes are in [GUIDE.md](GUIDE.md). `hydra setup` prints the short form.

## Settings

| Variable | Role |
| --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter credential. Frontier calls try this first. |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway credential. |
| `VERCEL_AI_GATEWAY_TOKEN` | Used when `AI_GATEWAY_API_KEY` is empty. |
| `AI_GATEWAY_API_BASE` | Gateway origin. Default `https://ai-gateway.vercel.sh/v1`. |
| `CHEAPERINFERENCE_API_KEY` | CheaperInference credential (discounted models, GLM series). |
| `CHEAPERINFERENCE_API_BASE` | CheaperInference origin. Default `https://api.cheaperinference.com/v1`. |
| `RUNPOD_API_KEY` | RunPod API key for serverless endpoints and pod inference. |
| `RUNPOD_ENDPOINT_ID` | RunPod serverless endpoint id (e.g. vLLM worker). |
| `RUNPOD_ENDPOINT_URL` | Custom OpenAI-compatible RunPod endpoint URL. |
| `MODAL_ENDPOINT_URL` | Modal serverless OpenAI-compatible vLLM endpoint URL. |
| `MODAL_API_KEY` | Optional bearer token for authenticated Modal deployments. |
| `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` | Together these select Workers AI for `hydra free`. |
| `OLLAMA_HOST`, `LLAMACPP_HOST`, `LOCAL_AI_BASE` | Used as given. Unset defaults probe `127.0.0.1` ports 11434, then 8080, then 8000. |
| `HYDRA_FREE_MODEL` | OpenRouter free model. Default `qwen/qwen3.8-27b:free`. |
| `HYDRA_LOCAL_MODEL` | Local model name. Default `qwen2.5-coder:latest`. |
| `HYDRA_TRUST_CWD_ENV` | Set to `1` to load secrets and host URLs from the working directory `.env`. |

## Tests

```bash
python -m pytest tests/
npm test
```

## License

Apache-2.0. Source and issues: https://github.com/erastudil/hydra
