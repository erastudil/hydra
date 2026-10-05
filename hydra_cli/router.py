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
    is_compound_alias,
    resolve_model,
)
from hydra_cli.providers import (
    CredentialsMissingError,
    ProviderError,
    detect_local_endpoint,
    fetch_chat_completion,
    get_free_provider,
    get_frontier_providers,
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
    cat file.txt | hydra <alias>         # Interactive pipe input

POPULAR ALIASES:
    opus 5.5, sol 6.1, sonnet 3.7, gemini 2.5, gemini 3.5, qwen 3b, grok, llama

OPTIONS:
    --system <prompt>       Custom system prompt
    --model <id>            Explicit model override
    --temperature <float>   Sampling temperature (default: 0.7)
    --max-tokens <int>      Maximum generation tokens
    --no-stream             Disable real-time SSE streaming
    --json                  Output raw JSON
    --heads <roles>         Comma-separated swarm heads (e.g. architect,coder,auditor)
    --list-models           List all registered aliases and providers
    -v, --version           Display version
    -h, --help              Show this help message
"""


def read_stdin_if_piped() -> Optional[str]:
    """Read piped input from stdin if present without blocking."""
    try:
        if sys.stdin.isatty():
            return None

        # Windows non-blocking check
        if sys.platform == "win32":
            try:
                import ctypes
                import msvcrt
                handle = msvcrt.get_osfhandle(sys.stdin.fileno())
                avail = ctypes.c_ulong()
                if ctypes.windll.kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None) and avail.value > 0:
                    content = sys.stdin.read().strip()
                    return content if content else None
                return None
            except Exception:
                return None

        # Unix / Linux / macOS non-blocking check
        import select
        r, _, _ = select.select([sys.stdin], [], [], 0.0)
        if r:
            content = sys.stdin.read().strip()
            return content if content else None
    except Exception:
        pass
    return None


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


def route_command(argv: List[str]) -> int:
    """Parse command line arguments and execute the intended action."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    piped_input = read_stdin_if_piped()

    if not argv:
        if piped_input:
            # Default to sonnet 3.7 or free if only piped input is provided
            alias = "sonnet 3.7"
            prompt = piped_input
            return execute_summon(alias, prompt, system_prompt=DEFAULT_SYSTEM_PROMPT)
        print(HELP_BANNER)
        return 0

    if argv[0] in ("-h", "--help", "help"):
        print(HELP_BANNER)
        return 0

    if argv[0] in ("-v", "--version", "version"):
        print(f"hydra {__version__}")
        return 0

    if argv[0] in ("--list-models", "list-models", "models"):
        print_registered_models()
        return 0

    # Extract command/alias and potential prompt
    remaining = argv.copy()
    command_or_alias = remaining.pop(0)

    # Check for compound alias like 'opus 5.5', 'sol 6.1', 'gemini 2.5', 'qwen 3b'
    if remaining and is_compound_alias(command_or_alias, remaining[0]):
        second_token = remaining.pop(0)
        command_or_alias = f"{command_or_alias} {second_token}"

    # Extract flags vs prompt
    prompt_tokens = []
    system_prompt = DEFAULT_SYSTEM_PROMPT
    model_override = None
    temperature = 0.7
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
        elif arg == "--temperature" and idx + 1 < len(remaining):
            try:
                temperature = float(remaining[idx + 1])
            except ValueError:
                pass
            idx += 2
        elif arg == "--max-tokens" and idx + 1 < len(remaining):
            try:
                max_tokens = int(remaining[idx + 1])
            except ValueError:
                pass
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

    user_prompt = " ".join(prompt_tokens).strip()
    effective_prompt = format_combined_prompt(user_prompt, piped_input)

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
            )
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
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    json_mode: bool = False,
) -> int:
    """Summon a frontier model via OpenRouter or Vercel AI Gateway with fallback."""
    model_id = model_override or resolve_model(alias)
    providers = get_frontier_providers()

    if not providers:
        raise CredentialsMissingError(
            f"No frontier credentials found to summon '{alias}' ({model_id}).\n"
            "Please export OPENROUTER_API_KEY or AI_GATEWAY_API_KEY in your environment,\n"
            "or run zero-cost inference via: hydra free \"<prompt>\"\n"
            "or local inference via:        hydra local \"<prompt>\""
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    last_error = None
    for provider in providers:
        try:
            if stream and not json_mode:
                for token in stream_chat_completion(
                    url=provider["url"],
                    headers=provider["headers"],
                    model=model_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    sys.stdout.write(token)
                    sys.stdout.flush()
                sys.stdout.write("\n")
                return 0
            else:
                resp = fetch_chat_completion(
                    url=provider["url"],
                    headers=provider["headers"],
                    model=model_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if json_mode:
                    import json
                    print(json.dumps({"model": model_id, "provider": provider["name"], "content": resp}, indent=2))
                else:
                    print(resp)
                return 0
        except Exception as e:
            last_error = e
            # Try next provider if available
            continue

    if last_error:
        raise ProviderError(f"All configured providers failed for '{model_id}'. Last error: {last_error}")
    return 1


def execute_free(
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    temperature: float = 0.7,
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
    temperature: float = 0.7,
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
    temperature: float = 0.7,
    json_output: bool = False,
    stream_output: bool = True,
) -> int:
    """Dispatch parallel multi-agent swarm."""
    execute_swarm(
        task=task,
        heads=heads,
        custom_model=custom_model,
        temperature=temperature,
        json_output=json_output,
        stream_output=stream_output,
    )
    return 0
