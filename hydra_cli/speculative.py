"""
Speculative Decoding Local-to-Cloud Bridge for Hydra CLI (Masterplan WO-02).
Accelerates inference by generating K speculative candidate tokens with a fast
draft provider (e.g. RTX 4060 Ollama / local / fast free model) and verifying
them in parallel with a target verifier provider (Cloudflare / OpenRouter / Frontier).
"""

import json
import math
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from hydra_cli.config import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    load_dotenv,
    resolve_route,
)
from hydra_cli.providers import (
    CredentialsMissingError,
    ProviderError,
    UsageError,
    detect_local_endpoint,
    fetch_chat_completion,
    get_free_candidates,
    get_frontier_providers,
    providers_for_model,
    redact,
)


class SpeculativeResult:
    """Telemetry and output from a speculative decoding run or step."""

    def __init__(
        self,
        text: str,
        accepted_tokens: List[str],
        rejected_token: Optional[str] = None,
        corrected_token: Optional[str] = None,
        total_draft_tokens: int = 0,
        accepted_count: int = 0,
        fallback_used: bool = False,
        iterations: int = 1,
        target_calls: int = 1,
        draft_calls: int = 1,
        duration_sec: float = 0.0,
        draft_model: str = "",
        target_model: str = "",
        k: int = 4,
    ):
        self.text = text
        self.accepted_tokens = list(accepted_tokens)
        self.rejected_token = rejected_token
        self.corrected_token = corrected_token
        self.total_draft_tokens = total_draft_tokens
        self.accepted_count = accepted_count
        self.acceptance_rate = (
            float(accepted_count) / float(total_draft_tokens)
            if total_draft_tokens > 0
            else (1.0 if not fallback_used and accepted_count > 0 else 0.0)
        )
        self.fallback_used = fallback_used
        self.iterations = iterations
        self.target_calls = target_calls
        self.draft_calls = draft_calls
        self.duration_sec = duration_sec
        self.draft_model = draft_model
        self.target_model = target_model
        self.k = k

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "accepted_tokens": self.accepted_tokens,
            "rejected_token": self.rejected_token,
            "corrected_token": self.corrected_token,
            "total_draft_tokens": self.total_draft_tokens,
            "accepted_count": self.accepted_count,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "fallback_used": self.fallback_used,
            "iterations": self.iterations,
            "target_calls": self.target_calls,
            "draft_calls": self.draft_calls,
            "duration_sec": round(self.duration_sec, 3),
            "draft_model": self.draft_model,
            "target_model": self.target_model,
            "k": self.k,
        }


def tokenize(text: str) -> List[str]:
    """Lossless token splitting preserving exact whitespace and punctuation."""
    if not text:
        return []
    return re.findall(r"\w+|[^\w\s]+|\s+", text)


def detokenize(tokens: List[str]) -> str:
    """Reconstruct string exactly from tokens."""
    return "".join(tokens)


def compute_verification_score(target_prob: float, draft_prob: float) -> float:
    """
    Log-likelihood verification score: log(p(token|context)) - log(p(token)).
    Per Masterplan WO-02.
    """
    t = max(1e-12, min(1.0, float(target_prob)))
    d = max(1e-12, min(1.0, float(draft_prob)))
    return math.log(t) - math.log(d)


def verify_candidate_tokens(
    candidates: List[str],
    target_tokens: List[str],
    threshold: float = 0.5,
) -> Tuple[List[str], Optional[str], Optional[str]]:
    """
    Speculative acceptance / rejection logic:
    Compares sequential candidate tokens against target tokens starting from index 0.
    Returns (accepted_tokens, rejected_token, corrected_token).
    """
    accepted: List[str] = []
    min_len = min(len(candidates), len(target_tokens))

    for i in range(min_len):
        cand = candidates[i]
        tgt = target_tokens[i]
        if cand == tgt:
            accepted.append(cand)
        else:
            return accepted, cand, tgt

    if len(candidates) <= len(target_tokens):
        bonus_token = target_tokens[len(candidates)] if len(target_tokens) > len(candidates) else None
        return accepted, None, bonus_token
    else:
        rejected = candidates[min_len] if min_len < len(candidates) else None
        return accepted, rejected, None


class SpeculativeEngine:
    """
    Speculative Decoding Engine (WO-02):
    Dispatches K candidate tokens via draft provider, verifies with target provider.
    Falls back gracefully to direct target generation if draft is unavailable.
    """

    def __init__(
        self,
        draft_provider: Optional[Dict[str, Any]] = None,
        target_provider: Optional[Dict[str, Any]] = None,
        draft_model: Optional[str] = None,
        target_model: Optional[str] = None,
        k: int = 4,
        threshold: float = 0.5,
    ):
        load_dotenv()
        self.k = max(1, int(k))
        self.threshold = float(threshold)
        self.draft_provider = draft_provider
        self.target_provider = target_provider
        self.draft_model = draft_model
        self.target_model = target_model

    def resolve_target(self) -> Tuple[Dict[str, Any], str]:
        """Resolves target verifier provider dict and model identifier."""
        if self.target_provider:
            model = self.target_model or self.target_provider.get("model", "target")
            return self.target_provider, model

        target_model = self.target_model or "anthropic/claude-sonnet-5.5"
        providers = providers_for_model(target_model)
        if not providers:
            frontier = get_frontier_providers()
            if frontier:
                return frontier[0], target_model
            free_cands = get_free_candidates()
            if free_cands:
                return free_cands[0][0], free_cands[0][1]
            raise CredentialsMissingError("No target provider credentials available for speculative verification.")
        return providers[0], target_model

    def resolve_draft(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Resolves draft model provider dict and model identifier.
        Checks local engine first, then fast free models.
        """
        if self.draft_provider:
            model = self.draft_model or self.draft_provider.get("model", DEFAULT_LOCAL_MODEL)
            return self.draft_provider, model

        if self.draft_model and self.draft_model.lower() not in ("local", "ollama"):
            providers = providers_for_model(self.draft_model)
            if providers:
                return providers[0], self.draft_model

        local_url, local_name = detect_local_endpoint()
        if "unverified" not in local_name.lower():
            return {
                "name": local_name,
                "url": local_url,
                "headers": {"Content-Type": "application/json"},
            }, DEFAULT_LOCAL_MODEL

        free_cands = get_free_candidates()
        if free_cands:
            return free_cands[0][0], free_cands[0][1]

        return {
            "name": local_name,
            "url": local_url,
            "headers": {"Content-Type": "application/json"},
        }, DEFAULT_LOCAL_MODEL

    def generate_candidates(
        self,
        messages: List[Dict[str, str]],
        draft_prov: Dict[str, Any],
        draft_model_id: str,
        k: Optional[int] = None,
    ) -> List[str]:
        """Queries draft model for K speculative tokens."""
        spec_k = k or self.k
        raw_completion = fetch_chat_completion(
            url=draft_prov["url"],
            headers=draft_prov.get("headers", {}),
            model=draft_model_id,
            messages=messages,
            max_tokens=spec_k,
            temperature=0.2,
        )
        if not raw_completion:
            return []
        tokens = tokenize(raw_completion)
        return tokens[:spec_k]

    def step(
        self,
        messages: List[Dict[str, str]],
        k: Optional[int] = None,
    ) -> SpeculativeResult:
        """
        Executes exactly one speculative window step:
        Emits K draft candidate tokens, queries target verifier,
        and applies acceptance/rejection logic.
        """
        t_start = time.perf_counter()
        spec_k = k or self.k
        target_prov, target_model_id = self.resolve_target()

        draft_tokens: List[str] = []
        fallback_used = False
        draft_model_id = ""

        try:
            draft_prov, draft_model_id = self.resolve_draft()
            if draft_prov:
                draft_tokens = self.generate_candidates(messages, draft_prov, draft_model_id, spec_k)
        except Exception:
            fallback_used = True
            draft_tokens = []

        if not draft_tokens or fallback_used:
            target_resp = fetch_chat_completion(
                url=target_prov["url"],
                headers=target_prov.get("headers", {}),
                model=target_model_id,
                messages=messages,
                max_tokens=spec_k + 1,
            )
            duration = time.perf_counter() - t_start
            return SpeculativeResult(
                text=target_resp,
                accepted_tokens=[],
                rejected_token=None,
                corrected_token=None,
                total_draft_tokens=0,
                accepted_count=0,
                fallback_used=True,
                iterations=1,
                target_calls=1,
                draft_calls=1 if not fallback_used else 0,
                duration_sec=duration,
                draft_model=draft_model_id,
                target_model=target_model_id,
                k=spec_k,
            )

        target_resp = fetch_chat_completion(
            url=target_prov["url"],
            headers=target_prov.get("headers", {}),
            model=target_model_id,
            messages=messages,
            max_tokens=spec_k + 4,
        )
        target_tokens = tokenize(target_resp)

        accepted, rejected, corrected = verify_candidate_tokens(
            draft_tokens, target_tokens, self.threshold
        )

        advance_tokens = list(accepted)
        if corrected:
            advance_tokens.append(corrected)

        duration = time.perf_counter() - t_start
        return SpeculativeResult(
            text=detokenize(advance_tokens),
            accepted_tokens=accepted,
            rejected_token=rejected,
            corrected_token=corrected,
            total_draft_tokens=len(draft_tokens),
            accepted_count=len(accepted),
            fallback_used=False,
            iterations=1,
            target_calls=1,
            draft_calls=1,
            duration_sec=duration,
            draft_model=draft_model_id,
            target_model=target_model_id,
            k=spec_k,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 64,
        k: Optional[int] = None,
        max_iterations: int = 16,
    ) -> SpeculativeResult:
        """
        Iterative speculative decoding loop.
        Appends accepted tokens and corrected token per step until max_tokens or completion.
        """
        t_start = time.perf_counter()
        spec_k = k or self.k
        target_prov, target_model_id = self.resolve_target()

        sys_msg = system_prompt or DEFAULT_SYSTEM_PROMPT
        base_messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ]

        generated_tokens: List[str] = []
        all_accepted: List[str] = []
        total_draft_count = 0
        total_accepted_count = 0
        target_calls = 0
        draft_calls = 0
        fallback_used = False
        last_rejected: Optional[str] = None
        last_corrected: Optional[str] = None
        draft_model_id = ""

        for iteration in range(max_iterations):
            current_text = detokenize(generated_tokens)
            if len(generated_tokens) >= max_tokens:
                break

            current_messages = list(base_messages)
            if current_text:
                current_messages.append({"role": "assistant", "content": current_text})

            draft_tokens: List[str] = []
            try:
                draft_prov, draft_model_id = self.resolve_draft()
                if draft_prov and not fallback_used:
                    draft_calls += 1
                    draft_tokens = self.generate_candidates(current_messages, draft_prov, draft_model_id, spec_k)
            except Exception:
                fallback_used = True
                draft_tokens = []

            target_calls += 1
            if not draft_tokens or fallback_used:
                fallback_used = True
                rem_tokens = max(1, max_tokens - len(generated_tokens))
                try:
                    target_resp = fetch_chat_completion(
                        url=target_prov["url"],
                        headers=target_prov.get("headers", {}),
                        model=target_model_id,
                        messages=current_messages,
                        max_tokens=rem_tokens,
                    )
                    t_tokens = tokenize(target_resp)
                    generated_tokens.extend(t_tokens)
                except Exception as e:
                    if not generated_tokens:
                        raise ProviderError(f"Speculative target fallback failed: {e}")
                break

            try:
                target_resp = fetch_chat_completion(
                    url=target_prov["url"],
                    headers=target_prov.get("headers", {}),
                    model=target_model_id,
                    messages=current_messages,
                    max_tokens=spec_k + 2,
                )
            except Exception as e:
                if not generated_tokens:
                    raise ProviderError(f"Target verification failed: {e}")
                break

            target_tokens = tokenize(target_resp)
            accepted, rejected, corrected = verify_candidate_tokens(
                draft_tokens, target_tokens, self.threshold
            )

            total_draft_count += len(draft_tokens)
            total_accepted_count += len(accepted)
            all_accepted.extend(accepted)
            last_rejected = rejected
            last_corrected = corrected

            generated_tokens.extend(accepted)
            if corrected:
                generated_tokens.append(corrected)

            if not accepted and not corrected:
                break

        duration = time.perf_counter() - t_start
        return SpeculativeResult(
            text=detokenize(generated_tokens),
            accepted_tokens=all_accepted,
            rejected_token=last_rejected,
            corrected_token=last_corrected,
            total_draft_tokens=total_draft_count,
            accepted_count=total_accepted_count,
            fallback_used=fallback_used,
            iterations=iteration + 1 if max_iterations > 0 else 0,
            target_calls=target_calls,
            draft_calls=draft_calls,
            duration_sec=duration,
            draft_model=draft_model_id,
            target_model=target_model_id,
            k=spec_k,
        )


def speculative_complete(
    prompt: str,
    target_model: Optional[str] = None,
    draft_model: Optional[str] = None,
    k: int = 4,
    system_prompt: Optional[str] = None,
    max_tokens: int = 64,
) -> SpeculativeResult:
    """Convenience function for executing a speculative decoding completion."""
    engine = SpeculativeEngine(
        draft_model=draft_model,
        target_model=target_model,
        k=k,
    )
    return engine.generate(prompt=prompt, system_prompt=system_prompt, max_tokens=max_tokens, k=k)
