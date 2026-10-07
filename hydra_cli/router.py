"""
Command line dispatch, argument parsing, pipe handling, and routing for Hydra CLI.
"""

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

from hydra_cli import __version__
from hydra_cli.config import (
    DEFAULT_ORCHESTRATOR_MODEL,
    DEFAULT_AGENT_MODEL,
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_FREE_MODEL,
    DEFAULT_HF_MODEL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    FREE_MODELS,
    MODEL_MAP,
    build_cached_system_prompt,
    consume_alias,
    load_dotenv,
    resolve_route,
)
from hydra_cli.providers import (
    CredentialsMissingError,
    ProviderError,
    UsageError,
    _cloudflare_provider,
    _openrouter_free_provider,
    detect_local_endpoint,
    ensure_temperature,
    fetch_chat_completion,
    get_free_candidates,
    get_huggingface_provider,
    get_frontier_providers,
    get_hf_provider,
    get_huggingface_provider,
    providers_for_model,
    reasoning_fields,
    redact,
    stream_chat_completion,
)
from hydra_cli.swarm import execute_swarm

HELP_BANNER = f"""
            [1]        [2]        [3]        [4]        [5]        [6]        [7]
           HERMES       PI      ARCHITECT  SOVEREIGN   CODER     AUDITOR   SYNTHESIS
          (\\___/)    (\\___/)    (\\___/)    <(\\___/)>   (\\___/)    (\\___/)    (\\___/)
          /0   0\\    /o   o\\    /^   ^\\    {{ 0   0 }}   /^   ^\\    /o   o\\    /0   0\\
         ( ==Y== )  ( ==v== )  ( ==w== )  (  ==X==  ) ( ==w== )  ( ==v== )  ( ==Y== )
          )     (    )     (    )     (   / )     ( \\  )     (    )     (    )     (
         /       \\  /       \\  /       \\ ( /       \\ )/       \\  /       \\  /       \\
        /   | |   \\/   | |   \\/   | |   \\ V   | |   V /   | |   \\/   | |   \\/   | |   \\
       |    | |        | |        | |    |    | |   |   | |        | |        | |    |
       \\    \\ \\       / /        / /     |    | |   |    \\ \\        \\ \\       / /    /
        \\    \\ \\_____/ /        / /      \\    | |   /     \\ \\________\\ \\_____/ /    /
         \\    \\_______/        / /        \\___/ \\__/       \\_______/  \\_______/    /
          \\                   / /          |       |        \\                     /
           '.               .' /           |  VII  |         \\                  .'
             '.           .'  /            |       |          \\               .'
               '---------'   /             /_______\\           \\   '---------'
                            /             /         \\           \\
                           (             /   HYDRA   \\           )
                            '._________.'|   CORE    |'._________.'
                                         \\           /
                                          '---------'

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
    hydra hf "<prompt>"                  # Hugging Face Serverless / Inference API routing
    hydra local "<prompt>"               # Offline local inference (Ollama/llama.cpp/EasyLM)
    hydra swarm "<task>"                 # Multi-agent swarm fan-out (Architect, Coder, Auditor)
    hydra agent                          # Launch interactive coding agent REPL (Cursor & Antigravity style)
    hydra agent --steer                  # Interactive coding agent with in-flight tool confirmation
    hydra agent "<prompt>"               # Autonomous coding agent with native tools & MCP
    hydra agent --steer "<prompt>"       # Autonomous agent with in-flight steering
    hydra agent --free "<prompt>"        # Zero-cost agent loop routing Free Forge
    hydra agent --local "<prompt>"       # Offline local agent loop (Ollama/llama.cpp)
    hydra <alias> --mcp "<prompt>"       # Tool-augmented execution loop
    hydra serve [--port 7777]            # Sovereign OpenAI Gateway for Hermes and Pi
    hydra mcp list                       # List configured community MCP servers & tools
    hydra sandbox run "<cmd>"            # Isolated zero-trust command execution
    hydra voice benchmark                # Sub-500ms real-time voice latency budget trace
    hydra voice stream                   # Chunked streaming TTS & early audio playback
    hydra banner                         # Display 7-headed Sovereign Hydra in terminal green
    hydra setup                          # Interactive setup & app/agent integration guide
    hydra auth                           # Interactive credential onboarding wizard & provider status
    hydra auth status                    # Inspect configured model providers without leaking secrets
    cat file.txt | hydra <alias>         # The pipe is the prompt
    cat file.txt | hydra <alias> - "do"  # Pipe plus an instruction
    hydra <alias> -- <prompt>            # Keep prompt words that match an alias

POPULAR ALIASES:
    opus 5.5 high, sol 6.1 pro, sonnet 5.5, gemini 3.8, grok 4.7, llama 4 scout, hermes, pi

OPTIONS:
    --system <prompt>       Custom system prompt
    --model <id>            Explicit model override
    --effort <level>        Reasoning effort (low, medium, high, xhigh, max)
    --reasoning-mode <mode> Reasoning mode, such as pro
    --temperature <float>   Sampling temperature. Omitted unless you set it.
    --max-tokens <int>      Maximum generation tokens
    --no-stream             Disable real-time SSE streaming
    --json                  Output raw JSON
    --mcp                   Enable Model Context Protocol (MCP) tools
    --tier <tier>           Routing tier: free, local, paid, frontier
    --free                  Shortcut for --tier free
    --local                 Shortcut for --tier local
    --steer                 Enable interactive in-flight tool confirmation & steering
    -i, --interactive       Launch interactive coding agent REPL
    --agentic               Enable tool-augmented loop in swarm heads
    --heads <roles>         Comma-separated swarm heads (e.g. architect:hermes,coder:pi,auditor)
    --list-models           List all registered aliases and providers
    --auth                  Credential onboarding wizard
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

    hf_prov = get_huggingface_provider()
    print(f"\nHugging Face Inference Provider:")
    if hf_prov:
        print(f"  Endpoint: {hf_prov['url']} (Authenticated)")
        print(f"  Default Model: {DEFAULT_HF_MODEL}")
    else:
        print("  Endpoint: None configured (export HF_TOKEN in ~/.hydra/.env)")
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

  D. CheaperInference (Discounted frontier & open-weight routing, GLM models):
     export CHEAPERINFERENCE_API_KEY="ci_live_..."

  E. RunPod (Serverless vLLM endpoints and GPU pods):
     export RUNPOD_API_KEY="rpa_..."
     export RUNPOD_ENDPOINT_ID="your-endpoint-id" # or export RUNPOD_ENDPOINT_URL="..."

  F. Modal (Serverless vLLM / OpenAI endpoints):
     export MODAL_ENDPOINT_URL="https://<app>.modal.run/v1"
     export MODAL_API_KEY="your-key"

  G. Local mode needs no cloud key:
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
        from hydra_cli.ui import GREEN_MID, RESET, supports_color
        if supports_color():
            print(f"{GREEN_MID}{HELP_BANNER}{RESET}")
        else:
            print(HELP_BANNER)
        return 0
    if argv and argv[0] in ("-v", "--version", "version"):
        print(f"hydra {__version__}")
        return 0
    if argv and argv[0] in ("banner", "--banner"):
        from hydra_cli.ui import print_banner
        print_banner(detailed=True, version=__version__)
        return 0
    if argv and argv[0] in ("setup", "guide", "--setup", "--guide"):
        print_setup_guide()
        return 0
    if argv and argv[0] in ("--list-models", "list-models", "models"):
        print_registered_models()
        return 0
    if argv and argv[0] in ("auth", "--auth"):
        from hydra_cli.auth import execute_auth_command
        return execute_auth_command(argv[1:])
    if argv and argv[0] in ("mcp", "--mcp") and len(argv) > 1 and argv[1] in ("list", "test", "init", "default", "config"):
        return execute_mcp_command(argv[1:])
    if argv and argv[0] in ("sandbox", "--sandbox"):
        return execute_sandbox_command(argv[1:])
    if argv and argv[0] in ('voice', '--voice'):
        from hydra_cli.voice import execute_voice_command
        return execute_voice_command(argv[1:])
    if argv and argv[0] in ("serve", "--serve"):
        host = "127.0.0.1"
        port = 7777
        idx = 1
        while idx < len(argv):
            if argv[idx] == "--host" and idx + 1 < len(argv):
                host = argv[idx + 1]
                idx += 2
            elif argv[idx] == "--port" and idx + 1 < len(argv):
                try:
                    port = int(argv[idx + 1])
                except ValueError:
                    sys.stderr.write(f"[ERROR] --port expects an integer, got {argv[idx + 1]!r}.\n")
                    return 1
                idx += 2
            else:
                idx += 1
        from hydra_cli.serve import run_server
        run_server(host=host, port=port)
        return 0

    if not argv:
        piped_input = read_stdin_if_piped()
        if piped_input:
            return execute_summon(DEFAULT_ORCHESTRATOR_MODEL, piped_input, system_prompt=DEFAULT_SYSTEM_PROMPT)
        from hydra_cli.ui import GREEN_MID, RESET, supports_color
        if supports_color():
            print(f"{GREEN_MID}{HELP_BANNER}{RESET}")
        else:
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
    mcp_mode = False
    session_id = None
    swarm_heads = None
    fallback_cascade_enabled = True
    speculative_mode = False
    spec_k = 4
    draft_model_override = None
    target_model_override = None
    tier_override = None
    interactive_mode = False
    agentic_mode = False
    steer_mode = False

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
        elif arg == "--no-fallback":
            fallback_cascade_enabled = False
            idx += 1
        elif arg == "--json":
            json_mode = True
            stream = False
            idx += 1
        elif arg == "--mcp":
            mcp_mode = True
            idx += 1
        elif arg in ("--session", "--session-id") and idx + 1 < len(remaining):
            session_id = remaining[idx + 1]
            idx += 2
        elif arg in ("--speculative", "--spec"):
            speculative_mode = True
            idx += 1
        elif arg in ("--draft", "--draft-model") and idx + 1 < len(remaining):
            draft_model_override = remaining[idx + 1]
            idx += 2
        elif arg in ("--target", "--target-model") and idx + 1 < len(remaining):
            target_model_override = remaining[idx + 1]
            idx += 2
        elif arg in ("--k", "--spec-k") and idx + 1 < len(remaining):
            try:
                spec_k = int(remaining[idx + 1])
            except ValueError:
                sys.stderr.write(f"[ERROR] --k expects an integer, got {remaining[idx + 1]!r}.\n")
                return 1
            idx += 2
        elif arg == "--tier" and idx + 1 < len(remaining):
            tier_override = remaining[idx + 1].strip().lower()
            idx += 2
        elif arg == "--free":
            tier_override = "free"
            idx += 1
        elif arg == "--local":
            tier_override = "local"
            idx += 1
        elif arg in ("--interactive", "-i"):
            interactive_mode = True
            idx += 1
        elif arg == "--agentic":
            agentic_mode = True
            idx += 1
        elif arg == "--steer":
            steer_mode = True
            idx += 1
        else:
            prompt_tokens.append(arg)
            idx += 1

    cmd_lower = command_or_alias.lower().strip()
    effective_prompt = compose_prompt(prompt_tokens)

    if not effective_prompt:
        if cmd_lower in ("agent", "mcp", "orchestrator") or interactive_mode:
            target_alias = command_or_alias if cmd_lower not in ("agent", "mcp", "orchestrator") else (model_override or DEFAULT_ORCHESTRATOR_MODEL)
            return execute_agent_mode(
                alias=target_alias,
                prompt="",
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                session_id=session_id,
                tier=tier_override,
                interactive=True,
                steer_mode=steer_mode,
            )
        sys.stderr.write(f"[ERROR] No prompt or piped input provided for '{command_or_alias}'.\n")
        return 1

    # Route based on command/alias
    try:
        if cmd_lower in ("agent", "mcp", "orchestrator") or mcp_mode or interactive_mode:
            target_alias = command_or_alias if cmd_lower not in ("agent", "mcp", "orchestrator") else (model_override or DEFAULT_ORCHESTRATOR_MODEL)
            return execute_agent_mode(
                alias=target_alias,
                prompt=effective_prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                session_id=session_id,
                tier=tier_override,
                interactive=interactive_mode,
                steer_mode=steer_mode,
            )
        elif cmd_lower == "free":
            return execute_free(
                prompt=effective_prompt,
                system_prompt=system_prompt,
                model_override=model_override,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                json_mode=json_mode,
            )
        elif cmd_lower in ("hf", "huggingface"):
            return execute_hf(
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
        elif cmd_lower in ("speculative", "spec") or speculative_mode:
            resolved_target = target_model_override or (
                command_or_alias if cmd_lower not in ("speculative", "spec") else model_override
            )
            return execute_speculative_mode(
                prompt=effective_prompt,
                target_model=resolved_target,
                draft_model=draft_model_override,
                k=spec_k,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        elif cmd_lower == "voice":
            from hydra_cli.voice import execute_voice_command
            return execute_voice_command(remaining)
        elif cmd_lower == "swarm":
            return execute_swarm_mode(
                task=effective_prompt,
                heads=swarm_heads,
                custom_model=model_override,
                temperature=temperature,
                json_output=json_mode,
                stream_output=stream,
                max_tokens=max_tokens,
                tier=tier_override,
                agentic=agentic_mode,
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
                fallback_cascade=fallback_cascade_enabled,
            )
    except UsageError as usage:
        sys.stderr.write(f"\n[ERROR] {redact(usage)}\n")
        return 1
    except CredentialsMissingError as cme:
        sys.stderr.write(f"\n[CREDENTIALS ERROR]\n{redact(cme)}\n")
        return 1
    except ProviderError as pe:
        sys.stderr.write(f"\n[HYDRA ERROR] {redact(pe)}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\n[INTERRUPTED]\n")
        return 130
    except Exception as e:
        sys.stderr.write(f"\n[UNEXPECTED ERROR] {redact(e)}\n")
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
    fallback_cascade: bool = True,
) -> int:
    """Summon a frontier model.
    Tries each configured provider in health-prioritized order.
    On persistent provider failures, automatically cascades through:
    Cloudflare Workers AI -> OpenRouter Free Forge -> Local/Ollama (WO-05).
    """
    route = resolve_route(alias)
    if route.get("runner") == "alice" or alias.strip().lower() == "alice":
        from hydra_cli.alice_runner import alice_available, run_alice
        if alice_available():
            output = run_alice(
                prompt,
                model=model_override or route.get("model"),
                fallback_model=model_override or route.get("model"),
            )
            if output:
                if json_mode:
                    import json
                    print(json.dumps({
                        "model": "alice-cognitive-core",
                        "runner": "alice",
                        "content": output,
                    }, indent=2))
                else:
                    print(output)
                return 0
    model_id = model_override or route["model"]
    if model_override:
        route_effort = effort
        route_mode = reasoning_mode
    else:
        route_effort = effort if effort is not None else route.get("effort")
        route_mode = reasoning_mode if reasoning_mode is not None else route.get("reasoning_mode")
    reasoning = reasoning_fields(route_effort, route_mode)
    ensure_temperature(model_id, temperature)

    cascade_enabled = fallback_cascade and os.environ.get("HYDRA_NO_FALLBACK", "").strip() != "1"

    providers = []
    try:
        providers = providers_for_model(model_id)
    except CredentialsMissingError:
        if not cascade_enabled:
            raise

    if not providers and not cascade_enabled:
        raise CredentialsMissingError(
            f"No frontier credentials found to summon '{alias}' ({model_id}).\n"
            "Export OPENROUTER_API_KEY, AI_GATEWAY_API_KEY, or CHEAPERINFERENCE_API_KEY.\n"
            "Free-tier cloud models: hydra free \"<prompt>\"\n"
            "This machine only:        hydra local \"<prompt>\""
        )

    messages = [
        {"role": "system", "content": build_cached_system_prompt(system_prompt)},
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
                    sys.stdout.write(str(token))
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

    if cascade_enabled:
        # Automatic fallback routing cascade: Frontier -> Cloudflare -> OpenRouter -> Local/Ollama (WO-05)
        cascade_errors = []
        if last_error:
            cascade_errors.append(f"Frontier ({model_id}): {last_error}")
        elif not providers:
            cascade_errors.append(f"Frontier ({model_id}): no credentials configured")

        # Tier 2: Cloudflare Workers AI
        cf = _cloudflare_provider()
        if cf:
            sys.stderr.write(
                f"[HYDRA CASCADE] Frontier unavailable ({redact(last_error or 'no credentials')}). "
                f"Falling back to Cloudflare Workers AI ({DEFAULT_CLOUDFLARE_MODEL})...\n"
            )
            sys.stderr.flush()
            emitted = False
            try:
                if stream and not json_mode:
                    for token in stream_chat_completion(
                        url=cf["url"],
                        headers=cf["headers"],
                        model=DEFAULT_CLOUDFLARE_MODEL,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ):
                        emitted = True
                        sys.stdout.write(str(token))
                        sys.stdout.flush()
                    sys.stdout.write("\n")
                    return 0
                resp = fetch_chat_completion(
                    url=cf["url"],
                    headers=cf["headers"],
                    model=DEFAULT_CLOUDFLARE_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if json_mode:
                    import json
                    print(json.dumps({"model": DEFAULT_CLOUDFLARE_MODEL, "provider": cf["name"], "content": resp}, indent=2))
                else:
                    print(resp)
                return 0
            except UsageError:
                raise
            except Exception as exc:
                if emitted:
                    raise ProviderError(f"Stream from Cloudflare truncated after output started: {exc}") from exc
                cascade_errors.append(f"Cloudflare: {exc}")

        # Tier 3: OpenRouter Free Forge
        or_free = _openrouter_free_provider()
        if or_free:
            free_models = [DEFAULT_FREE_MODEL] + [m for m in FREE_MODELS if m != DEFAULT_FREE_MODEL]
            for fm in free_models:
                sys.stderr.write(
                    f"[HYDRA CASCADE] Falling back to OpenRouter Free Forge ({fm})...\n"
                )
                sys.stderr.flush()
                emitted = False
                try:
                    if stream and not json_mode:
                        for token in stream_chat_completion(
                            url=or_free["url"],
                            headers=or_free["headers"],
                            model=fm,
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ):
                            emitted = True
                            sys.stdout.write(str(token))
                            sys.stdout.flush()
                        sys.stdout.write("\n")
                        return 0
                    resp = fetch_chat_completion(
                        url=or_free["url"],
                        headers=or_free["headers"],
                        model=fm,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    if json_mode:
                        import json
                        print(json.dumps({"model": fm, "provider": or_free["name"], "content": resp}, indent=2))
                    else:
                        print(resp)
                    return 0
                except UsageError:
                    raise
                except Exception as exc:
                    if emitted:
                        raise ProviderError(f"Stream from OpenRouter truncated after output started: {exc}") from exc
                    cascade_errors.append(f"OpenRouter ({fm}): {exc}")

        # Tier 4: Local / Ollama
        try:
            local_url, local_name = detect_local_endpoint()
            sys.stderr.write(
                f"[HYDRA CASCADE] Cloud tiers unavailable. Falling back to local offline inference ({local_name})...\n"
            )
            sys.stderr.flush()
            emitted = False
            try:
                if stream and not json_mode:
                    for token in stream_chat_completion(
                        url=local_url,
                        headers={"Content-Type": "application/json"},
                        model=DEFAULT_LOCAL_MODEL,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ):
                        emitted = True
                        sys.stdout.write(str(token))
                        sys.stdout.flush()
                    sys.stdout.write("\n")
                    return 0
                resp = fetch_chat_completion(
                    url=local_url,
                    headers={"Content-Type": "application/json"},
                    model=DEFAULT_LOCAL_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if json_mode:
                    import json
                    print(json.dumps({"model": DEFAULT_LOCAL_MODEL, "provider": local_name, "content": resp}, indent=2))
                else:
                    print(resp)
                return 0
            except UsageError:
                raise
            except Exception as exc:
                if emitted:
                    raise ProviderError(f"Stream from {local_name} truncated after output started: {exc}") from exc
                cascade_errors.append(f"Local ({local_name}): {exc}")
        except Exception as exc:
            cascade_errors.append(f"Local endpoint detection: {exc}")

        raise ProviderError(
            redact(
                f"All fallback cascade tiers failed (Frontier -> Cloudflare -> OpenRouter -> Local). "
                f"Errors: {'; '.join(cascade_errors)}"
            )
        )

    if last_error:
        raise ProviderError(redact(f"All configured providers failed for '{model_id}'. Last error: {last_error}"))
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
    """Free Forge: Cloudflare Workers AI first, then OpenRouter free models.

    A later candidate is tried only before any text has been printed.
    """
    candidates = get_free_candidates(model_override)

    messages = [
        {"role": "system", "content": build_cached_system_prompt(system_prompt)},
        {"role": "user", "content": prompt},
    ]

    last_error: Optional[Exception] = None
    for index, (provider_info, model_id) in enumerate(candidates):
        if index > 0 and last_error is not None:
            sys.stderr.write(
                f"[HYDRA FREE] Previous route failed ({redact(last_error)}). "
                f"Trying {provider_info['name']} with {model_id}.\n"
            )
            sys.stderr.flush()
        emitted = False
        try:
            if stream and not json_mode:
                for token in stream_chat_completion(
                    url=provider_info["url"],
                    headers=provider_info["headers"],
                    model=model_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    emitted = True
                    sys.stdout.write(str(token))
                    sys.stdout.flush()
                sys.stdout.write("\n")
                return 0
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
        except UsageError:
            raise
        except Exception as exc:
            if emitted:
                raise ProviderError(
                    f"Stream from {provider_info['name']} truncated after output started: {exc}"
                ) from exc
            last_error = exc
            continue

    raise ProviderError(redact(f"Every Free Forge route failed. Last error: {last_error}"))


def execute_hf(
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    json_mode: bool = False,
) -> int:
    """Execute inference via Hugging Face Serverless Inference API / Router."""
    provider_info, default_model = get_hf_provider()
    model_id = model_override or default_model

    messages = [
        {"role": "system", "content": build_cached_system_prompt(system_prompt)},
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
            sys.stdout.write(str(token))
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
            print(json.dumps({"model": model_id, "provider": provider_info["name"], "endpoint": provider_info["url"], "content": resp}, indent=2))
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
        {"role": "system", "content": build_cached_system_prompt(system_prompt)},
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
                sys.stdout.write(str(token))
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
    max_tokens: Optional[int] = None,
    tier: Optional[str] = None,
    agentic: bool = False,
) -> int:
    """Dispatch the swarm. A failed head or a failed synthesis exits nonzero."""
    results = execute_swarm(
        task=task,
        heads=heads,
        custom_model=custom_model,
        temperature=temperature,
        json_output=json_output,
        stream_output=stream_output,
        max_tokens=max_tokens,
        tier=tier,
        agentic=agentic,
    )
    if any(result.error for result in results):
        return 1
    return 0


def execute_agent_mode(
    alias: str,
    prompt: str,
    system_prompt: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    session_id: Optional[str] = None,
    tier: Optional[str] = None,
    interactive: bool = False,
    steer_mode: bool = False,
) -> int:
    """Execute autonomous agent loop with discovered MCP tools and native coding tools."""
    from hydra_cli.agent import run_agent_loop, run_interactive_agent
    from hydra_cli.mcp_registry import McpRegistry

    if interactive or not prompt:
        return run_interactive_agent(
            alias=alias,
            tier=tier,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            session_id=session_id,
            steer_mode=steer_mode,
        )

    reg = McpRegistry(auto_load=True)
    try:
        tools = reg.get_openai_tools()
        for server, err in sorted(reg.errors.items()):
            first = redact(err).strip().splitlines()[0] if err.strip() else "unknown error"
            sys.stderr.write(f"[HYDRA AGENT] MCP server '{server}' unavailable: {first[:200]}\n")
        sys.stderr.flush()
        ans = run_agent_loop(
            alias=alias,
            prompt=prompt,
            system_prompt=system_prompt,
            registry=reg,
            temperature=temperature,
            max_tokens=max_tokens,
            session_id=session_id,
            tier=tier,
            steer_mode=steer_mode,
        )
        print(ans)
        return 0
    finally:
        reg.shutdown()


def execute_speculative_mode(
    prompt: str,
    target_model: Optional[str] = None,
    draft_model: Optional[str] = None,
    k: int = 4,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
) -> int:
    """Execute speculative decoding with fast draft model and target verifier (WO-02)."""
    from hydra_cli.speculative import SpeculativeEngine
    from hydra_cli.ui import GREEN_BRIGHT, GREEN_MID, RESET, supports_color

    engine = SpeculativeEngine(
        draft_model=draft_model,
        target_model=target_model,
        k=k,
    )
    result = engine.generate(
        prompt=prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens or 64,
        k=k,
    )

    if json_mode:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    color_on = supports_color()
    head_color = GREEN_BRIGHT if color_on else ""
    info_color = GREEN_MID if color_on else ""
    reset = RESET if color_on else ""

    print(
        f"{head_color}[HYDRA SPECULATIVE]{reset} "
        f"{info_color}(Draft: {result.draft_model or 'auto'} | Target: {result.target_model or 'auto'} | K={result.k}){reset}"
    )
    print(result.text)
    print(
        f"{info_color}[Telemetry: Acceptance={result.accepted_count}/{result.total_draft_tokens} "
        f"({round(result.acceptance_rate * 100, 1)}%) | Target Calls={result.target_calls} "
        f"| Fallback={result.fallback_used} | Latency={result.duration_sec}s]{reset}"
    )
    return 0


def execute_mcp_command(args: List[str]) -> int:
    """Handle hydra mcp subcommands: list, test, config, init."""
    from hydra_cli.mcp_registry import McpRegistry, DEFAULT_PACKAGE_CONFIG, get_default_home_config
    from hydra_cli.ui import GREEN_BOLD, GREEN_BRIGHT, RESET, supports_color

    subcmd = args[0] if args else "list"

    if subcmd == "list":
        reg = McpRegistry(auto_load=True)
        servers = reg._server_configs
        if not servers:
            print(f"No MCP servers registered in {reg.config_path or 'configuration'}.")
            return 0
        c_bold = GREEN_BOLD if supports_color() else ""
        c_tool = GREEN_BRIGHT if supports_color() else ""
        c_reset = RESET if supports_color() else ""
        print(f"\n{c_bold}--- Registered MCP Servers ({len(servers)}) ---{c_reset}")
        for name, cfg in sorted(servers.items()):
            cmd = cfg.get("command") or cfg.get("url", "")
            args_str = " ".join(cfg.get("args", []))
            desc = cfg.get("description", "")
            desc_str = f" - {desc}" if desc else ""
            print(f"  * {c_tool}{name:<14}{c_reset} : {cmd} {args_str}{desc_str}")
        print()
        return 0

    if subcmd == "config":
        reg = McpRegistry(auto_load=False)
        cfg_path = reg.discover_config_path()
        print(f"Active MCP configuration: {cfg_path}")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                print(f.read())
        else:
            print("(Config file not found. Run `hydra mcp init` to create it from default community template)")
        return 0

    if subcmd in ("init", "default"):
        home_cfg = get_default_home_config()
        if os.path.isfile(home_cfg) and "--force" not in args:
            print(f"Config already exists at {home_cfg}. Use --force to overwrite.")
            return 0
        if os.path.isfile(DEFAULT_PACKAGE_CONFIG):
            import shutil
            os.makedirs(os.path.dirname(home_cfg), exist_ok=True)
            shutil.copyfile(DEFAULT_PACKAGE_CONFIG, home_cfg)
            print(f"Initialized default community MCP servers configuration at {home_cfg}")
            return 0
        else:
            sys.stderr.write(f"[ERROR] Default template not found at {DEFAULT_PACKAGE_CONFIG}\n")
            return 1

    if subcmd == "test":
        if len(args) < 2:
            sys.stderr.write("Usage: hydra mcp test <server_name>\n")
            return 1
        target_name = args[1]
        reg = McpRegistry(auto_load=True)
        if target_name not in reg._server_configs:
            sys.stderr.write(f"[ERROR] MCP server '{target_name}' not configured.\n")
            return 1
        print(f"Testing MCP server '{target_name}'...")
        try:
            client = reg.get_client(target_name)
            tools = client.list_tools()
            print(f"[OK] Server '{target_name}' initialized successfully. {len(tools)} tools discovered:")
            for t in tools:
                print(f"  - {t.get('name')}: {t.get('description', '')}")
            reg.shutdown()
            return 0
        except Exception as e:
            sys.stderr.write(f"[ERROR] Failed testing server '{target_name}': {redact(e)}\n")
            reg.shutdown()
            return 1

    sys.stderr.write(f"Unknown mcp command: '{subcmd}'. Available: list, test, config, init\n")
    return 1


def execute_sandbox_command(args: List[str]) -> int:
    """Execute command inside zero-trust sandbox (WO-08)."""
    from hydra_cli.sandbox import SandboxConfig, SandboxRunner
    if not args:
        sys.stderr.write("USAGE: hydra sandbox <command> or hydra sandbox run <command>\n")
        return 1

    cmd_tokens = list(args)
    if cmd_tokens and cmd_tokens[0] == "run":
        cmd_tokens = cmd_tokens[1:]

    timeout = 30.0
    allow_network = False
    clean_tokens = []
    idx = 0
    while idx < len(cmd_tokens):
        t = cmd_tokens[idx]
        if t == "--timeout" and idx + 1 < len(cmd_tokens):
            try:
                timeout = float(cmd_tokens[idx + 1])
            except ValueError:
                pass
            idx += 2
        elif t == "--allow-network":
            allow_network = True
            idx += 1
        else:
            clean_tokens.append(t)
            idx += 1

    command = " ".join(clean_tokens).strip()
    if not command:
        sys.stderr.write("[ERROR] No command specified for sandbox execution.\n")
        return 1

    config = SandboxConfig(timeout_seconds=timeout, allow_network=allow_network)
    runner = SandboxRunner(config=config)
    res = runner.run_command(command)
    if res.stdout:
        sys.stdout.write(res.stdout)
        if not res.stdout.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    if res.stderr:
        sys.stderr.write(res.stderr)
        if not res.stderr.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()
    if res.violation:
        sys.stderr.write(f"[SANDBOX BLOCKED] {res.violation}\n")
        sys.stderr.flush()
    return res.exit_code
