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
    # Anthropic Claude
    "opus 5.5": "anthropic/claude-opus-5.5",
    "opus": "anthropic/claude-opus-5.5",
    "opus 5": "anthropic/claude-opus-5",
    "sonnet 5.5": "anthropic/claude-sonnet-5.5",
    "sonnet": "anthropic/claude-sonnet-5.5",
    "claude-5.5-sonnet": "anthropic/claude-sonnet-5.5",
    "claude-sonnet-5.5": "anthropic/claude-sonnet-5.5",
    "sonnet 3.7": "anthropic/claude-3.7-sonnet",
    "haiku 4.5": "anthropic/claude-haiku-4.5",
    "haiku": "anthropic/claude-haiku-4.5",
    "fable 5.1": "anthropic/claude-fable-5.1",
    "fable": "anthropic/claude-fable-5.1",

    # OpenAI / Sol
    "sol 6.1": "openai/gpt-6.1-sol",
    "sol 6.1 pro": "openai/gpt-6.1-sol-pro",
    "sol": "openai/gpt-6.1-sol",
    "gpt-6.1-sol": "openai/gpt-6.1-sol",
    "luna": "openai/gpt-6-luna",
    "astra": "openai/gpt-6-astra",
    "gpt-5.5": "openai/gpt-5.5",
    "gpt-5": "openai/gpt-5.5",
    "gpt-4o": "openai/gpt-4o",
    "o3": "openai/o3",
    "o3-mini": "openai/o3-mini",
    "o4-mini": "openai/o4-mini",
    "o4": "openai/o4-mini",
    "o1": "openai/o1",

    # Google Gemini & Gemma
    "gemini 3.8": "google/gemini-3.8-flash",
    "gemini 3.7": "google/gemini-3.7-flash",
    "gemini 3.5": "google/gemini-3.5-flash",
    "gemini": "google/gemini-3.8-flash",
    "gemini-flash": "google/gemini-3.8-flash",
    "gemini 2.5": "google/gemini-2.5-pro",
    "gemini-pro": "google/gemini-2.5-pro",
    "gemma 4": "google/gemma-4-26b-a4b-it",

    # Alibaba / Qwen
    "qwen 3.8": "qwen/qwen3.8-27b",
    "qwen": "qwen/qwen3.8-27b",
    "qwen-coder": "alibaba/qwen3-coder",
    "qwen coder": "alibaba/qwen3-coder",
    "qwen 3b": "qwen/qwen-2.5-3b-instruct",
    "qwen 3": "qwen/qwen-2.5-coder-32b-instruct",

    # xAI / SpaceX AI Grok
    "grok 4.7": "x-ai/grok-4.7",
    "grok 4.6": "x-ai/grok-4.6",
    "grok": "x-ai/grok-4.7",
    "grok 2": "x-ai/grok-2-1212",

    # Meta Llama
    "llama 4": "meta-llama/llama-4-maverick",
    "llama 4 maverick": "meta-llama/llama-4-maverick",
    "llama 4 scout": "meta-llama/llama-4-scout",
    "llama 3.3": "meta-llama/llama-3.3-70b-instruct",
    "llama": "meta-llama/llama-3.3-70b-instruct",

    # DeepSeek
    "deepseek": "deepseek/deepseek-chat",
    "deepseek r1": "deepseek/deepseek-r1",
    "deepseek-chat": "deepseek/deepseek-chat",
}

# Free tier models on OpenRouter or Cloudflare Workers AI
FREE_MODELS: List[str] = [
    "qwen/qwen3.8-27b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-26b-a4b-it:free",
    "deepseek/deepseek-chat:free",
    "nvidia/nemotron-3.5-lightning:free",
]

DEFAULT_FREE_MODEL = os.environ.get("HYDRA_FREE_MODEL", "qwen/qwen3.8-27b:free")
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
        "model": "anthropic/claude-sonnet-5.5",
        "system": "You are the Principal Software Engineer. Provide complete, executable, clean implementation code adhering strictly to zero-dependency principles and production standards.",
    },
    "auditor": {
        "title": "Inspector",
        "model": "openai/gpt-6.1-sol",
        "system": "You are the Security & Performance Inspector. Audit the proposed design and code for edge cases, resource leaks, security vulnerabilities, and verification gates.",
    },
    "synthesizer": {
        "title": "Synthesizer",
        "model": "google/gemini-3.8-flash",
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
