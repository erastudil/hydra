"""
Configuration and model resolution for Hydra CLI.
"""

import os
from typing import Dict, List, Optional, Tuple

DEFAULT_SYSTEM_PROMPT = os.environ.get(
    "HYDRA_SYSTEM_PROMPT",
    "You are a world-class sovereign systems engineer. Speak concisely, rigorously, and without corporate filler or disclaimers."
)

MODEL_MAP: Dict[str, str] = {
    # Anthropic
    "opus 5.5": "anthropic/claude-opus-5.5",
    "opus": "anthropic/claude-opus-5.5",
    "sonnet 5.5": "anthropic/claude-5.5-sonnet",
    "sonnet": "anthropic/claude-5.5-sonnet",
    "sonnet 3.7": "anthropic/claude-3.7-sonnet",
    "haiku": "anthropic/claude-3.5-haiku",

    # OpenAI / Sol
    "sol 6.1": "openai/gpt-6.1-sol-pro",
    "sol 6.1 pro": "openai/gpt-6.1-sol-pro",
    "sol": "openai/gpt-6.1-sol-pro",
    "gpt-6.1-sol": "openai/gpt-6.1-sol-pro",
    "gpt-4o": "openai/gpt-4o",
    "o1": "openai/o1",
    "o3-mini": "openai/o3-mini",

    # Google Gemini
    "gemini 2.5": "google/gemini-2.5-pro",
    "gemini 3.5": "google/gemini-2.5-flash",
    "gemini": "google/gemini-2.5-flash",
    "gemini-flash": "google/gemini-2.5-flash",
    "gemini-pro": "google/gemini-2.5-pro",

    # Qwen
    "qwen 3b": "qwen/qwen-2.5-3b-instruct",
    "qwen 3": "qwen/qwen-2.5-coder-32b-instruct",
    "qwen": "qwen/qwen-2.5-coder-32b-instruct",
    "qwen-coder": "qwen/qwen-2.5-coder-32b-instruct",

    # xAI Grok
    "grok": "x-ai/grok-2-1212",
    "grok 2": "x-ai/grok-2-1212",
    "grok-beta": "x-ai/grok-beta",

    # Meta Llama
    "llama": "meta-llama/llama-3.3-70b-instruct",
    "llama 3.3": "meta-llama/llama-3.3-70b-instruct",
    "llama-70b": "meta-llama/llama-3.3-70b-instruct",

    # DeepSeek
    "deepseek": "deepseek/deepseek-chat",
    "deepseek r1": "deepseek/deepseek-r1",
    "deepseek-chat": "deepseek/deepseek-chat",
}

# Free tier models on OpenRouter or Cloudflare Workers AI
FREE_MODELS: List[str] = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "deepseek/deepseek-chat:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]

DEFAULT_FREE_MODEL = os.environ.get("HYDRA_FREE_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
DEFAULT_CLOUDFLARE_MODEL = os.environ.get("HYDRA_CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct")

# Local inference endpoints
DEFAULT_OLLAMA_ENDPOINT = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
DEFAULT_LLAMACPP_ENDPOINT = os.environ.get("LLAMACPP_HOST", "http://localhost:8080").rstrip("/")
DEFAULT_EASYLM_ENDPOINT = os.environ.get("LOCAL_AI_BASE", "http://localhost:8000").rstrip("/")
DEFAULT_LOCAL_MODEL = os.environ.get("HYDRA_LOCAL_MODEL", "qwen2.5-coder:latest")

# Swarm default heads configuration
SWARM_HEADS: Dict[str, Dict[str, str]] = {
    "architect": {
        "title": "Architect",
        "model": "anthropic/claude-opus-5.5",
        "system": "You are the Lead Systems Architect. Analyze the requirements, state invariants, data flows, and architectural failure modes. Produce a minimal, robust architecture design.",
    },
    "coder": {
        "title": "Implementer",
        "model": "anthropic/claude-5.5-sonnet",
        "system": "You are the Principal Software Engineer. Provide complete, executable, clean implementation code adhering strictly to zero-dependency principles and production standards.",
    },
    "auditor": {
        "title": "Inspector",
        "model": "openai/gpt-6.1-sol-pro",
        "system": "You are the Security & Performance Inspector. Audit the proposed design and code for edge cases, resource leaks, security vulnerabilities, and verification gates.",
    },
    "synthesizer": {
        "title": "Synthesizer",
        "model": "google/gemini-2.5-pro",
        "system": "You are the Swarm Lead Synthesizer. Review all perspectives, resolve conflicting tradeoffs, and emit a final prioritized execution roadmap.",
    },
}

def resolve_model(alias: str) -> str:
    """Resolve an alias to its provider model identifier, or return unchanged if already a model ID."""
    cleaned = alias.strip().lower()
    return MODEL_MAP.get(cleaned, alias)

def is_compound_alias(arg1: str, arg2: str) -> bool:
    """Determine if two consecutive CLI tokens match a compound alias (e.g. 'opus 5.5')."""
    candidate = f"{arg1} {arg2}".strip().lower()
    return candidate in MODEL_MAP
