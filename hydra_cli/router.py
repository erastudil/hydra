"""
Command line dispatch, argument parsing, pipe handling, and routing for Hydra CLI.
"""

import argparse
import os
import sys
from typing import List, Optional, Tuple

from hydra_cli import __version__
from hydra_cli.config import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    FREE_MODELS,
    MODEL_MAP,
    consume_alias,
    load_dotenv,
    resolve_route,
)
from hydra_cli.providers import (
    CredentialsMissingError,
    ProviderError,
    UsageError,
    detect_local_endpoint,
    ensure_temperature,
    fetch_chat_completion,
    get_free_provider,
    get_frontier_providers,
    reasoning_fields,
    stream_chat_completion,
)
from hydra_cli.swarm import execute_swarm

HELP_BANNER = f"""
  ___ ___            .___              
 /   |   \\___.__.  __| _/___________   
/    ~    <   |  | / __ |\\_  __ \\__  \\  
\\    Y    /\\___  |/ /_/ | |  | \\// __ \\_
 \\___|_  / / ____|\\____ | |__|  (____  /
       \\/  \\/          \\/            \\/ 
      Sovereign Multi-Headed AI Shell · v{__version__}

USAGE:
    hydra <model-alias> "<prompt>"       # Direct frontier model summoning
    hydra free "<prompt>"                # Zero-cost Free Forge routing
    hydra local "<prompt>"               # Offline local inference (Ollama/llama.cpp/EasyLM)
    hydra swarm "<task>"                 # Multi-agent swarm fan-out (Architect, Coder, Auditor)
    hydra setup                          # Interactive setup & app/agent integration guide
    cat file.txt | hydra <alias>         # The pipe is the prompt
    cat file.txt | hydra <alias> - "do"  # Pipe plus an instruction
    hydra <alias> -- <prompt>            # Keep prompt words that match an alias

POPULAR ALIASES:
    opus 5.5 high, sol 6.1 pro, sonnet 5.5, gemini 3.8, grok 4.7, llama 4 scout

OPTIONS:
    --system <prompt>       Custom system prompt
    --model <id>            Explicit model override
    --effort <level>        Reasoning effort (low, medium, high, xhigh, max)
    --reasoning-mode <mode> Reasoning mode, such as pro
    --temperature <float>   Sampling temperature. Omitted unless you set it.
    --max-tokens <int>      Maximum generation tokens
    --no-stream             Disable real-time SSE streaming
    --json                  Output raw JSON
    --heads <roles>         Comma-separated swarm heads (e.g. architect,coder,auditor)
    --list-models           List all registered aliases and providers
    --guide, --setup        Show setup and integration guide
    -v, --version           Display version
    -h, --help              Show this help message
"""


def read_stdin_if_piped() -> Optional[str]:
    """Read piped or redirected stdin through EOF. A terminal is left alone."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        content = sys.stdin.read()
    except Exception:
        return None
    if not content:
        return None
    content = content.strip()
    return content or None


def compose_prompt(prompt_tokens: List[str]) -> str:
    """Build the prompt. Stdin is read only when the prompt is missing or contains '-'."""
    wants_stdin = (not prompt_tokens) or ("-" in prompt_tokens)
    piped = read_stdin_if_piped() if wants_stdin else None
    if "-" in prompt_tokens:
        instruction = " ".join(token for token in prompt_tokens if token != "-").strip()
        return format_combined_prompt(instruction, piped)
    if not prompt_tokens:
        return piped or ""
    return " ".join(prompt_tokens).strip()


def format_combined_prompt(user_prompt: str, piped_input: Optional[str]) -> str:
    """Combine user CLI prompt with piped input cleanly."""
    if not piped_input:
        return user_prompt
    if not user_prompt:
        return piped_input
    return f"[Piped Input]:\n{piped_input}\n\n[Instruction]:\n{user_prompt}"


def print_registered_models():
    """Print all configured models, aliases, free tiers, and endpoints."""
    print(f"\n--- Hydra Registered Models & Aliases (v{__version__}) ---")
    print("\nFrontier Aliases:")
    for alias, target in sorted(MODEL_MAP.items()):
        print(f"  {alias:<15} -> {target}")

    print("\nFree Forge Tier Models:")
    for model in FREE_MODELS:
        print(f"  * {model}")

    local_url, local_name = detect_local_endpoint()
    print(f"\nLocal Engine Detection:")
    print(f"  Detected: {local_name} at {local_url}")
    print()


def print_setup_guide():
    """Print a comprehensive setup and application/agent integration guide."""
    guide = f"""
================================================================================
  HYDRA SETUP & INTEGRATION GUIDE · v{__version__}
================================================================================

1. QUICK SETUP & CREDENTIALS
--------------------------------------------------------------------------------
Hydra reads the process environment first, then ~/.hydra/.env.
A project .env may set ordinary settings such as HYDRA_FREE_MODEL.
Keys, tokens, and host URLs in a project .env stay unloaded unless HYDRA_TRUST_CWD_ENV=1.
Supported providers:

  A. Cloudflare Workers AI (Zero cost or your existing paid plan):
     export CLOUDFLARE_API_TOKEN="your-token"
     export CLOUDFLARE_ACCOUNT_ID="your-account-id"

  B. OpenRouter (Access to 200+ models with unified billing or free tiers):
     export OPENROUTER_API_KEY="sk-or-v1-..."

  C. Vercel AI Gateway:
     export AI_GATEWAY_API_KEY="your-token"
     # VERCEL_AI_GATEWAY_TOKEN is accepted as an alias of the same key.

  D. Local mode needs no cloud key:
     • hydra local "<prompt>"  -> Ollama (11434), llama.cpp (8080), or EasyLM (8000)
     • hydra free "<prompt>"   -> Cloudflare or OpenRouter free-tier models. A key is required.

2. SHELL SCRIPTS & UNIX PIPES
--------------------------------------------------------------------------------
Pipe outputs directly from your shell into any model:

  # Review recent git diff with Sonnet 5.5
  git diff | hydra sonnet 5.5 - "Audit for security issues and edge cases"

  # The pipe alone is the prompt
  cat build.log | hydra free --no-stream

  # Words after -- stay in the prompt, even when they look like an alias
  hydra opus 5.5 -- high ground rules

3. INTEGRATING INTO PYTHON APPLICATIONS & AGENTS
--------------------------------------------------------------------------------
A. Direct Import:
   from hydra_cli import complete

   print(complete("sonnet 5.5", "Analyze memory ordering in lock-free rings"))

B. Subprocess / Agent Tool Pattern (LangChain, AutoGen, CrewAI, Antigravity):
   import subprocess

   def hydra_tool(model_alias: str, query: str) -> str:
       proc = subprocess.run(
           ["hydra", model_alias, query, "--no-stream"],
           capture_output=True,
           text=True,
           check=True
       )
       return proc.stdout.strip()

   # Or structured JSON:
   # proc = subprocess.run(["hydra", "sonnet 5.5", query, "--json", "--no-stream"], ...)

4. INTEGRATING INTO NODE.JS / TYPESCRIPT APPLICATIONS
--------------------------------------------------------------------------------
Run via global CLI or npx with zero npm install:

  import {{ execFileSync }} from 'node:child_process';

  function callHydra(alias, prompt) {{
    return execFileSync('hydra', [alias, prompt, '--no-stream'], {{
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }}).trim();
  }}

  const analysis = callHydra('sonnet 5.5', 'Analyze this payload');

5. MULTI-AGENT SWARMS
--------------------------------------------------------------------------------
Spawn specialist heads in parallel, then one synthesizer after they finish:

  hydra swarm "Architect a low-latency tick-by-tick orderbook"

Custom heads:
  hydra swarm "Design consensus loop" --heads architect,auditor

Docs & Source: https://github.com/erastudil/hydra
================================================================================
"""
    print(guide)


def _parse_number(flag: str, raw: str, integer: bool) -> Tuple[Optional[float], Optional[str]]:
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        kind = "integer" if integer else "number"
        return None, f"[ERROR] {flag} expects a {kind}, got {raw!r}.\n"
    return value, None


def route_command(argv: List[str]) -> int:
    """Parse command line arguments and execute the intended action."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()

    if argv and argv[0] in ("-h", "--help", "help"):
        print(HELP_BANNER)
        return 0
    if argv and argv[0] in ("-v", "--version", "version"):
        print(f"hydra {__version__}")
        return 0
    if argv and argv[0] in ("setup", "guide", "--setup", "--guide"):
        print_setup_guide()
        return 0
    if argv and argv[0] in ("--list-models", "list-models", "models"):
        print_registered_models()
        return 0

    if not argv:
        piped_input = read_stdin_if_piped()
        if piped_input:
            return execute_summon("sonnet 5.5", piped_input, system_prompt=DEFAULT_SYSTEM_PROMPT)
        print(HELP_BANNER)
        return 0

    command_or_alias, remaining = consume_alias(list(argv))

    prompt_tokens = []
    system_prompt = DEFAULT_SYSTEM_PROMPT
    model_override = None
    effort_override = None
    reasoning_mode_override = None
    temperature = None
    max_tokens = None
    stream = True
    json_mode = False
    swarm_heads = None

    idx = 0
    while idx < len(remaining):
        arg = remaining[idx]
        if arg == "--system" and idx + 1 < len(remaining):
            system_prompt = remaining[idx + 1]
            idx += 2
        elif arg == "--model" and idx + 1 < len(remaining):
            model_override = remaining[idx + 1]
            idx += 2
        elif arg == "--effort" and idx + 1 < len(remaining):
            effort_override = remaining[idx + 1].strip().lower()
            idx += 2
        elif arg == "--reasoning-mode" and idx + 1 < len(remaining):
            reasoning_mode_override = remaining[idx + 1].strip().lower()
            idx += 2
        elif arg == "--temperature" and idx + 1 < len(remaining):
            temperature, err = _parse_number("--temperature", remaining[idx + 1], False)
            if err:
                sys.stderr.write(err)
                return 1
            idx += 2
        elif arg == "--max-tokens" and idx + 1 < len(remaining):
            parsed, err = _parse_number("--max-tokens", remaining[idx + 1], True)
            if err:
                sys.stderr.write(err)
                return 1
            max_tokens = int(parsed)
            idx += 2
        elif arg == "--heads" and idx + 1 < len(remaining):
            swarm_heads = [h.strip() for h in remaining[idx + 1].split(",") if h.strip()]
            idx += 2
        elif arg == "--no-stream":
            stream = False
            idx += 1
        elif arg == "--json":
            json_mode = True
            stream = False
            idx += 1
        else:
            prompt_tokens.append(arg)
            idx += 1

    effective_prompt = compose_prompt(prompt_tokens)

    if not effective_prompt:
        sys.stderr.write(f"[ERROR] No prompt or piped input provided for '{command_or_alias}'.\n")
        return 1

    # Route based on command/alias
    cmd_lower = command_or_alias.lower().strip()

    try:
        if cmd_lower == "free":
            return execute_free(
                prompt=effective_prompt,
                system_prompt=system_prompt,
                model_override=model_override,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                json_mode=json_mode,
            )
        elif cmd_lower == "local":
            return execute_local(
                prompt=effective_prompt,
                system_prompt=system_prompt,
                model_override=model_override,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                json_mode=json_mode,
            )
        elif cmd_lower == "swarm":
            return execute_swarm_mode(
                task=effective_prompt,
                heads=swarm_heads,
                custom_model=model_override,
                temperature=temperature,
                json_output=json_mode,
                stream_output=stream,
            )
        else:
            return execute_summon(
                alias=command_or_alias,
                prompt=effective_prompt,
                system_prompt=system_prompt,
                model_override=model_override,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                json_mode=json_mode,
                effort=effort_override,
                reasoning_mode=reasoning_mode_override,
            )
    except UsageError as usage:
        sys.stderr.write(f"\n[ERROR] {usage}\n")
        return 1
    except CredentialsMissingError as cme:
        sys.stderr.write(f"\n[CREDENTIALS ERROR]\n{cme}\n")
        return 1
    except ProviderError as pe:
        sys.stderr.write(f"\n[HYDRA ERROR] {pe}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\n[INTERRUPTED]\n")
        return 130
    except Exception as e:
        sys.stderr.write(f"\n[UNEXPECTED ERROR] {e}\n")
        return 1


def execute_summon(
    alias: str,
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    json_mode: bool = False,
    effort: Optional[str] = None,
    reasoning_mode: Optional[str] = None,
) -> int:
    """Summon a frontier model. A later provider is tried only before any text is printed."""
    route = resolve_route(alias)
    model_id = model_override or route["model"]
    if model_override:
        route_effort = effort
        route_mode = reasoning_mode
    else:
        route_effort = effort if effort is not None else route.get("effort")
        route_mode = reasoning_mode if reasoning_mode is not None else route.get("reasoning_mode")
    reasoning = reasoning_fields(route_effort, route_mode)
    ensure_temperature(model_id, temperature)
    providers = get_frontier_providers()

    if not providers:
        raise CredentialsMissingError(
            f"No frontier credentials found to summon '{alias}' ({model_id}).\n"
            "Export OPENROUTER_API_KEY or AI_GATEWAY_API_KEY.\n"
            "Free-tier cloud models: hydra free \"<prompt>\"\n"
            "This machine only:        hydra local \"<prompt>\""
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    last_error = None
    for provider in providers:
        emitted = False
        try:
            if stream and not json_mode:
                for token in stream_chat_completion(
                    url=provider["url"],
                    headers=provider["headers"],
                    model=model_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning=reasoning,
                ):
                    emitted = True
                    sys.stdout.write(token)
                    sys.stdout.flush()
                sys.stdout.write("\n")
                return 0
            resp = fetch_chat_completion(
                url=provider["url"],
                headers=provider["headers"],
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning=reasoning,
            )
            if json_mode:
                import json
                print(json.dumps({
                    "model": model_id,
                    "provider": provider["name"],
                    "effort": route_effort,
                    "reasoning_mode": route_mode,
                    "content": resp,
                }, indent=2))
            else:
                print(resp)
            return 0
        except UsageError:
            raise
        except Exception as exc:
            if emitted:
                raise ProviderError(
                    f"Stream from {provider['name']} truncated after output started: {exc}"
                ) from exc
            last_error = exc
            continue

    if last_error:
        raise ProviderError(f"All configured providers failed for '{model_id}'. Last error: {last_error}")
    return 1


def execute_free(
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    json_mode: bool = False,
) -> int:
    """Execute inference via Free Forge (Cloudflare Workers AI or OpenRouter free models)."""
    provider_info, default_model = get_free_provider()
    model_id = model_override or default_model

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    if stream and not json_mode:
        for token in stream_chat_completion(
            url=provider_info["url"],
            headers=provider_info["headers"],
            model=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            sys.stdout.write(token)
            sys.stdout.flush()
        sys.stdout.write("\n")
    else:
        resp = fetch_chat_completion(
            url=provider_info["url"],
            headers=provider_info["headers"],
            model=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if json_mode:
            import json
            print(json.dumps({"model": model_id, "provider": provider_info["name"], "content": resp}, indent=2))
        else:
            print(resp)
    return 0


def execute_local(
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    json_mode: bool = False,
) -> int:
    """Execute inference against local engine (Ollama, llama.cpp, EasyLM)."""
    url, provider_name = detect_local_endpoint()
    model_id = model_override or DEFAULT_LOCAL_MODEL
    headers = {"Content-Type": "application/json"}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    try:
        if stream and not json_mode:
            for token in stream_chat_completion(
                url=url,
                headers=headers,
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                sys.stdout.write(token)
                sys.stdout.flush()
            sys.stdout.write("\n")
        else:
            resp = fetch_chat_completion(
                url=url,
                headers=headers,
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if json_mode:
                import json
                print(json.dumps({"model": model_id, "provider": provider_name, "endpoint": url, "content": resp}, indent=2))
            else:
                print(resp)
        return 0
    except ProviderError as pe:
        raise ProviderError(
            f"Local endpoint connection failed ({provider_name} at {url}): {pe}\n"
            f"Ensure your local model server is running (e.g. `ollama serve` or `llama-server`)."
        )


def execute_swarm_mode(
    task: str,
    heads: Optional[List[str]] = None,
    custom_model: Optional[str] = None,
    temperature: Optional[float] = None,
    json_output: bool = False,
    stream_output: bool = True,
) -> int:
    """Dispatch the swarm. A failed head or a failed synthesis exits nonzero."""
    results = execute_swarm(
        task=task,
        heads=heads,
        custom_model=custom_model,
        temperature=temperature,
        json_output=json_output,
        stream_output=stream_output,
    )
    if any(result.error for result in results):
        return 1
    return 0
