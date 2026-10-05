"""
Configuration and model resolution for Hydra CLI.
Aliases live in catalog.json so the Python and Node runtimes share one map.
"""

import json
import os
from typing import Dict, List, Optional, Tuple

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.json")


def load_catalog() -> Dict:
    with open(_CATALOG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


CATALOG = load_catalog()

DEFAULT_SYSTEM_PROMPT = os.environ.get(
    "HYDRA_SYSTEM_PROMPT",
    "You are a world-class sovereign systems engineer. Speak concisely, rigorously, and without corporate filler or disclaimers.",
)

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
)
_SENSITIVE_EXACT = {"CLOUDFLARE_ACCOUNT_ID"}


def _sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    if upper in _SENSITIVE_EXACT:
        return True
    return upper.endswith(_SENSITIVE_SUFFIXES)


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


def resolve_route(alias: str) -> Dict[str, Optional[str]]:
    """Return the canonical model id plus any reasoning effort or mode."""
    cleaned = alias.strip().lower()
    spec = CATALOG["aliases"].get(cleaned)
    if not spec:
        return {"model": alias, "effort": None, "reasoning_mode": None}
    return {
        "model": spec["model"],
        "effort": spec.get("effort"),
        "reasoning_mode": spec.get("reasoning_mode"),
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
