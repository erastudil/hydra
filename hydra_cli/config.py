"""
Configuration and model resolution for Hydra CLI.
Aliases live in catalog.json so the Python and Node runtimes share one map.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.json")


def hydra_home() -> str:
    """Directory for Hydra state: $HYDRA_HOME, else ~/.hydra."""
    custom = os.environ.get("HYDRA_HOME", "").strip()
    return os.path.expanduser(custom) if custom else os.path.join(os.path.expanduser("~"), ".hydra")


def load_catalog() -> Dict:
    with open(_CATALOG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


CATALOG = load_catalog()

DEFAULT_SYSTEM_PROMPT = os.environ.get(
    "HYDRA_SYSTEM_PROMPT",
    "You are a world-class sovereign systems engineer. Speak concisely, rigorously, and without corporate filler or disclaimers.",
)

IMMUTABLE_AGENT_INVARIANTS = os.environ.get(
    "HYDRA_IMMUTABLE_INVARIANTS",
    (
        "=== SYSTEM INVARIANTS (IMMUTABLE CACHE ZONE) ===\n"
        "1. Identity: Sovereign multi-headed AI engine and model router.\n"
        "2. Epistemology: Empirical verification over speculation. Exit code 0 is passing; unverified assertions carry zero truth value.\n"
        "3. Discipline: Direct predication, zero corporate filler, zero sycophancy, zero disclaimer theater.\n"
        "4. Security: Strict credential isolation, fail closed on ambiguous or destructive commands.\n"
        "================================================"
    ),
)


def build_cached_system_prompt(custom_prompt: Optional[str] = None) -> str:
    """Construct system prompt with immutable invariants isolated at the head.

    Isolating immutable invariants in the prefix maximizes provider prompt cache hits
    across Anthropic, Gemini, and OpenAI (WO-04).
    """
    header = IMMUTABLE_AGENT_INVARIANTS.strip()
    if not custom_prompt:
        return f"{header}\n\n{DEFAULT_SYSTEM_PROMPT.strip()}"
    custom_clean = custom_prompt.strip()
    if custom_clean == DEFAULT_SYSTEM_PROMPT.strip():
        return f"{header}\n\n{DEFAULT_SYSTEM_PROMPT.strip()}"
    if header in custom_clean:
        return custom_clean
    return f"{header}\n\n{custom_clean}"

MODEL_MAP: Dict[str, str] = {
    alias: spec["model"] for alias, spec in CATALOG["aliases"].items()
}

FREE_MODELS: List[str] = list(CATALOG["free_models"])

DEFAULT_FREE_MODEL = os.environ.get("HYDRA_FREE_MODEL", CATALOG["default_free_model"])
DEFAULT_CLOUDFLARE_MODEL = os.environ.get(
    "HYDRA_CLOUDFLARE_MODEL", CATALOG["default_cloudflare_model"]
)

DEFAULT_OLLAMA_ENDPOINT = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_LLAMACPP_ENDPOINT = os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_EASYLM_ENDPOINT = os.environ.get("LOCAL_AI_BASE", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_LOCAL_MODEL = os.environ.get("HYDRA_LOCAL_MODEL", CATALOG["default_local_model"])

DEFAULT_CHEAPERINFERENCE_BASE = "https://api.cheaperinference.com/v1"
DEFAULT_RUNPOD_BASE_TEMPLATE = "https://api.runpod.ai/v2/{endpoint_id}/openai/v1"
DEFAULT_HF_BASE = os.environ.get("HF_INFERENCE_BASE", "https://router.huggingface.co/v1").rstrip("/")
DEFAULT_HF_MODEL = os.environ.get("HYDRA_HF_MODEL", CATALOG.get("default_hf_model", "meta-llama/Llama-3.1-8B-Instruct"))
DEFAULT_ORCHESTRATOR_MODEL = os.environ.get(
    "HYDRA_ORCHESTRATOR_MODEL",
    os.environ.get(
        "HYDRA_DEFAULT_MODEL",
        CATALOG.get("default_orchestrator_model", "glm 5.3 flash"),
    ),
)
DEFAULT_AGENT_MODEL = os.environ.get(
    "HYDRA_AGENT_MODEL",
    CATALOG.get("default_agent_model", DEFAULT_ORCHESTRATOR_MODEL),
)

# Models that only some providers serve. Values are provider ids in preference order.
MODEL_PROVIDERS: Dict[str, List[str]] = {
    model: list(ids) for model, ids in (CATALOG.get("model_providers") or {}).items()
}

# Provider id -> the variable that enables it. Used in error messages only.
PROVIDER_KEY_NAMES: Dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "vercel": "AI_GATEWAY_API_KEY",
    "cheaperinference": "CHEAPERINFERENCE_API_KEY",
    "runpod": "RUNPOD_API_KEY",
    "modal": "MODAL_ENDPOINT_URL",
    "huggingface": "HF_TOKEN",
}

DEFAULT_CONTEXT_WINDOW = 131072
CONTEXT_PROFILES: Dict[str, int] = dict(CATALOG.get("context_profiles") or {"default": 131072, "128k": 131072})

# JSON and source tokenize denser than prose. Three characters per token keeps the
# live window inside the provider window after tool schemas are attached.
CHARS_PER_TOKEN = 3
OUTPUT_RESERVE_TOKENS = 8192


def get_context_window(model: Optional[str] = None) -> int:
    """Return context window budget for a model id or summon alias, defaulting to 128k."""
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    key = model.strip()
    spec = (CATALOG.get("aliases") or {}).get(key.lower())
    if isinstance(spec, dict) and spec.get("model"):
        key = spec["model"]
    return CONTEXT_PROFILES.get(key, CONTEXT_PROFILES.get("default", DEFAULT_CONTEXT_WINDOW))


def schema_chars(tools: Optional[List[Any]] = None) -> int:
    """Byte length of the tool schema array that rides beside the messages."""
    if not tools:
        return 0
    return len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"), default=str))


def input_char_budget(
    window_tokens: int,
    tools: Optional[List[Any]] = None,
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS,
) -> int:
    """Character budget for messages after tool schemas and reserved completion tokens."""
    reserved = schema_chars(tools) + max(0, int(output_reserve_tokens)) * CHARS_PER_TOKEN
    usable = int(window_tokens) * CHARS_PER_TOKEN - reserved
    return max(4096, usable)


SWARM_HEADS: Dict[str, Dict[str, str]] = {
    role: dict(spec) for role, spec in CATALOG["swarm"].items()
}


_SENSITIVE_SUFFIXES = (
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_HOST",
    "_BASE",
    "_URL",
    "_ENDPOINT",
    "_ENDPOINT_ID",
)
_SENSITIVE_EXACT = {"CLOUDFLARE_ACCOUNT_ID", "RUNPOD_ENDPOINT_ID"}


def _sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    if upper in _SENSITIVE_EXACT:
        return True
    return upper.endswith(_SENSITIVE_SUFFIXES)


sensitive_env_key = _sensitive_env_key


def _trust_cwd_env() -> bool:
    return os.environ.get("HYDRA_TRUST_CWD_ENV", "").strip().lower() in ("1", "true", "yes")


def _apply_env_file(path: str, trusted: bool) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        if "\n" in value or "\r" in value:
            continue
        if not trusted and _sensitive_env_key(key):
            continue
        os.environ[key] = value


def load_dotenv() -> None:
    """Load ~/.hydra/.env, then a project .env. The process environment wins.

    A project .env may set ordinary settings. Keys, tokens, and host URLs in
    that file stay unloaded unless HYDRA_TRUST_CWD_ENV=1.
    """
    home_file = os.path.join(os.path.expanduser("~"), ".hydra", ".env")
    cwd_file = os.path.join(os.getcwd(), ".env")
    _apply_env_file(home_file, trusted=True)
    _apply_env_file(cwd_file, trusted=_trust_cwd_env())


def model_rejects_temperature(model: str) -> bool:
    """True when the catalog says this model id refuses a temperature field."""
    blocked = CATALOG.get("no_temperature_models") or []
    return model in blocked


def model_providers(model: str) -> List[str]:
    """Provider ids allowed to serve this model id. Empty means any provider."""
    providers = MODEL_PROVIDERS.get(model)
    if providers is not None:
        return list(providers)
    cleaned = model.strip().lower()
    if cleaned in CATALOG.get("aliases", {}):
        target_model = CATALOG["aliases"][cleaned].get("model")
        if target_model and target_model in MODEL_PROVIDERS:
            return list(MODEL_PROVIDERS[target_model])
    return []


def resolve_route(alias: str) -> Dict[str, Any]:
    """Return the canonical model id, any reasoning effort or mode, and provider limits."""
    cleaned = alias.strip().lower()
    spec = CATALOG["aliases"].get(cleaned)
    if not spec:
        return {"model": alias, "effort": None, "reasoning_mode": None, "runner": None, "providers": model_providers(alias)}
    return {
        "model": spec["model"],
        "effort": spec.get("effort"),
        "reasoning_mode": spec.get("reasoning_mode"),
        "runner": spec.get("runner"),
        "providers": model_providers(spec["model"]),
    }


def resolve_model(alias: str) -> str:
    """Resolve an alias to its provider model identifier, or return it unchanged."""
    return resolve_route(alias)["model"]


def is_compound_alias(arg1: str, arg2: str) -> bool:
    """True when two CLI tokens are the start of a registered alias."""
    candidate = f"{arg1} {arg2}".strip().lower()
    if candidate in MODEL_MAP:
        return True
    prefix = candidate + " "
    return any(alias.startswith(prefix) for alias in MODEL_MAP)


def consume_alias(tokens: List[str]) -> Tuple[str, List[str]]:
    """Take the longest registered alias. A `--` token ends the alias and stays out of the prompt."""
    if not tokens:
        return "", []
    stop = None
    for index, token in enumerate(tokens):
        if token == "--":
            stop = index
            break
    if stop is None:
        window = tokens
        tail: List[str] = []
    else:
        window = tokens[:stop]
        tail = tokens[stop + 1:]
    upper = min(4, len(window))
    for count in range(upper, 0, -1):
        chunk = window[:count]
        if any(part.startswith("-") for part in chunk):
            continue
        candidate = " ".join(chunk).strip().lower()
        if candidate in MODEL_MAP:
            return candidate, window[count:] + tail
    if window:
        return window[0], window[1:] + tail
    return "", tail


if __name__ == "__main__":
    import sys
    load_dotenv()
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ORCHESTRATOR_MODEL
    route = resolve_route(target)
    providers = model_providers(route["model"])
    print(f"alias: {target}")
    print(f"model: {route['model']}")
    print(f"allowed_providers: {providers}")
    if providers:
        print(f"primary_provider: {providers[0]}")
        print(f"fallback_providers: {providers[1:]}")
    else:
        print("allowed_providers: all configured providers")
