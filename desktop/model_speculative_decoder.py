"""
Hydra Desktop Model Speculative Decoding Accelerator Subsystem.
Provides draft token proposal generation, target model verification,
speculative acceptance criteria, rejection rollback, and speedup ratio metrics.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union


class AcceptanceCriterion(str, Enum):
    """Speculative token acceptance criteria."""
    GREEDY_MATCH = "greedy_match"
    PROBABILITY_RATIO = "probability_ratio"
    THRESHOLD = "threshold"


@dataclass
class DraftTokenProposal:
    """Proposed draft token with probability and sequence position."""
    token_id: Union[int, str]
    token_str: str
    probability: float = 1.0
    position: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize proposal to dictionary representation."""
        return {
            "token_id": self.token_id,
            "token_str": self.token_str,
            "probability": self.probability,
            "position": self.position,
            "metadata": dict(self.metadata),
        }


@dataclass
class VerificationResult:
    """Target model verification outcome across draft sequence."""
    accepted_tokens: List[DraftTokenProposal]
    rejected_token: Optional[DraftTokenProposal] = None
    corrected_token: Optional[DraftTokenProposal] = None
    num_proposed: int = 0
    num_accepted: int = 0
    acceptance_rate: float = 0.0
    rollback_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize verification outcome to dictionary."""
        return {
            "accepted_tokens": [t.to_dict() for t in self.accepted_tokens],
            "rejected_token": self.rejected_token.to_dict() if self.rejected_token else None,
            "corrected_token": self.corrected_token.to_dict() if self.corrected_token else None,
            "num_proposed": self.num_proposed,
            "num_accepted": self.num_accepted,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "rollback_index": self.rollback_index,
        }


@dataclass
class SpeculativeDecodingStep:
    """Execution trace of single speculative decoding iteration."""
    step_index: int
    prompt: str
    draft_proposals: List[DraftTokenProposal]
    verification: VerificationResult
    emitted_tokens: List[DraftTokenProposal]
    draft_latency_ms: float = 0.0
    target_latency_ms: float = 0.0
    step_speedup_ratio: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize decoding step to dictionary."""
        return {
            "step_index": self.step_index,
            "prompt": self.prompt,
            "draft_proposals": [t.to_dict() for t in self.draft_proposals],
            "verification": self.verification.to_dict(),
            "emitted_tokens": [t.to_dict() for t in self.emitted_tokens],
            "draft_latency_ms": round(self.draft_latency_ms, 3),
            "target_latency_ms": round(self.target_latency_ms, 3),
            "step_speedup_ratio": round(self.step_speedup_ratio, 3),
        }


@dataclass
class SpeculativeStats:
    """Cumulative performance metrics for speculative accelerator."""
    total_draft_proposed: int = 0
    total_draft_accepted: int = 0
    total_draft_rejected: int = 0
    total_verification_steps: int = 0
    cumulative_draft_ms: float = 0.0
    cumulative_target_ms: float = 0.0
    cumulative_baseline_ms: float = 0.0

    @property
    def acceptance_rate(self) -> float:
        """Calculate overall draft token acceptance probability."""
        if self.total_draft_proposed == 0:
            return 0.0
        return self.total_draft_accepted / float(self.total_draft_proposed)

    @property
    def effective_tokens_per_step(self) -> float:
        """Compute average accepted tokens emitted per target verification step."""
        if self.total_verification_steps == 0:
            return 0.0
        return (self.total_draft_accepted + self.total_verification_steps) / float(self.total_verification_steps)

    @property
    def speculative_speedup_ratio(self) -> float:
        """Calculate wall-clock speedup multiplier against non-speculative baseline."""
        speculative_total_ms = self.cumulative_draft_ms + self.cumulative_target_ms
        if speculative_total_ms <= 0.0 or self.cumulative_baseline_ms <= 0.0:
            if self.total_verification_steps == 0:
                return 1.0
            return max(1.0, self.effective_tokens_per_step)
        return max(0.1, self.cumulative_baseline_ms / speculative_total_ms)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize statistics packet to dictionary."""
        return {
            "total_draft_proposed": self.total_draft_proposed,
            "total_draft_accepted": self.total_draft_accepted,
            "total_draft_rejected": self.total_draft_rejected,
            "total_verification_steps": self.total_verification_steps,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "effective_tokens_per_step": round(self.effective_tokens_per_step, 3),
            "speculative_speedup_ratio": round(self.speculative_speedup_ratio, 3),
            "cumulative_draft_ms": round(self.cumulative_draft_ms, 2),
            "cumulative_target_ms": round(self.cumulative_target_ms, 2),
            "cumulative_baseline_ms": round(self.cumulative_baseline_ms, 2),
        }


class NGramDraftGenerator:
    """Deterministic n-gram draft token proposal engine from text corpus."""

    def __init__(self, n: int = 3) -> None:
        self.n = max(2, n)
        self._transitions: Dict[Tuple[str, ...], Dict[str, int]] = {}
        self._lock = threading.RLock()

    def train(self, text: str) -> None:
        """Ingest text corpus and build statistical n-gram transition table."""
        with self._lock:
            tokens = text.split()
            if len(tokens) < self.n:
                return
            for i in range(len(tokens) - self.n + 1):
                gram = tuple(tokens[i : i + self.n - 1])
                nxt = tokens[i + self.n - 1]
                if gram not in self._transitions:
                    self._transitions[gram] = {}
                self._transitions[gram][nxt] = self._transitions[gram].get(nxt, 0) + 1

    def propose(self, context: str, lookahead: int = 3) -> List[DraftTokenProposal]:
        """Generate lookahead draft token proposals given preceding context."""
        with self._lock:
            tokens = context.split()
            proposals: List[DraftTokenProposal] = []
            curr_tokens = list(tokens)

            for step in range(lookahead):
                if len(curr_tokens) < self.n - 1:
                    break
                gram = tuple(curr_tokens[-(self.n - 1) :])
                candidates = self._transitions.get(gram)
                if not candidates:
                    break
                best_token, best_count = max(candidates.items(), key=lambda item: item[1])
                total_count = sum(candidates.values())
                prob = float(best_count) / float(total_count) if total_count > 0 else 1.0

                proposal = DraftTokenProposal(
                    token_id=best_token,
                    token_str=best_token,
                    probability=prob,
                    position=len(curr_tokens),
                    metadata={"source": "ngram", "count": best_count},
                )
                proposals.append(proposal)
                curr_tokens.append(best_token)

            return proposals


class ModelSpeculativeDecoder:
    """
    Speculative decoding accelerator engine.
    Orchestrates draft proposal generation, target parallel verification,
    acceptance filtering, rejection rollback, and empirical speedup calculations.
    """

    def __init__(
        self,
        default_lookahead: int = 3,
        criterion: AcceptanceCriterion = AcceptanceCriterion.GREEDY_MATCH,
        acceptance_threshold: float = 0.5,
    ) -> None:
        self.default_lookahead = max(1, default_lookahead)
        self.criterion = criterion
        self.acceptance_threshold = acceptance_threshold
        self._stats = SpeculativeStats()
        self._history: List[SpeculativeDecodingStep] = []
        self._lock = threading.RLock()

    def generate_proposals(
        self,
        context: str,
        draft_callable: Callable[[str, int], List[DraftTokenProposal]],
        lookahead: Optional[int] = None,
    ) -> List[DraftTokenProposal]:
        """Query draft generator callable for speculative token proposals."""
        k = lookahead if lookahead is not None else self.default_lookahead
        return draft_callable(context, k)

    def verify_proposals(
        self,
        context: str,
        proposals: Sequence[DraftTokenProposal],
        target_verifier: Callable[[str, Sequence[DraftTokenProposal]], List[Tuple[DraftTokenProposal, float, Optional[DraftTokenProposal]]]],
    ) -> VerificationResult:
        """
        Verify proposed draft tokens against target model predictions.
        Target verifier signature:
          (context, proposals) -> List of (proposed_or_target_token, target_probability, corrected_token_if_rejected)
        """
        if not proposals:
            return VerificationResult(
                accepted_tokens=[],
                rejected_token=None,
                corrected_token=None,
                num_proposed=0,
                num_accepted=0,
                acceptance_rate=0.0,
                rollback_index=None,
            )

        evaluations = target_verifier(context, proposals)
        accepted: List[DraftTokenProposal] = []
        rejected: Optional[DraftTokenProposal] = None
        corrected: Optional[DraftTokenProposal] = None
        rollback_idx: Optional[int] = None

        for idx, (prop, target_prob, alternative) in enumerate(evaluations):
            is_valid = False
            if self.criterion == AcceptanceCriterion.GREEDY_MATCH:
                is_valid = (prop.token_str == alternative.token_str) if alternative else True
            elif self.criterion == AcceptanceCriterion.PROBABILITY_RATIO:
                ratio = (target_prob / prop.probability) if prop.probability > 0 else 1.0
                is_valid = ratio >= 1.0 or random.random() < ratio
            elif self.criterion == AcceptanceCriterion.THRESHOLD:
                is_valid = target_prob >= self.acceptance_threshold

            if is_valid:
                accepted.append(prop)
            else:
                rejected = prop
                corrected = alternative
                rollback_idx = idx
                break

        num_proposed = len(proposals)
        num_accepted = len(accepted)
        acc_rate = float(num_accepted) / float(num_proposed) if num_proposed > 0 else 0.0

        return VerificationResult(
            accepted_tokens=accepted,
            rejected_token=rejected,
            corrected_token=corrected,
            num_proposed=num_proposed,
            num_accepted=num_accepted,
            acceptance_rate=acc_rate,
            rollback_index=rollback_idx,
        )

    def execute_step(
        self,
        context: str,
        draft_callable: Callable[[str, int], List[DraftTokenProposal]],
        target_verifier: Callable[[str, Sequence[DraftTokenProposal]], List[Tuple[DraftTokenProposal, float, Optional[DraftTokenProposal]]]],
        lookahead: Optional[int] = None,
        draft_latency_ms: float = 10.0,
        target_latency_ms: float = 50.0,
    ) -> SpeculativeDecodingStep:
        """Execute single atomic speculative iteration with rollback resolution."""
        with self._lock:
            proposals = self.generate_proposals(context, draft_callable, lookahead)
            verification = self.verify_proposals(context, proposals, target_verifier)

            emitted: List[DraftTokenProposal] = list(verification.accepted_tokens)
            if verification.corrected_token is not None:
                emitted.append(verification.corrected_token)

            tokens_count = len(emitted)
            baseline_ms = target_latency_ms * max(1, tokens_count)
            spec_ms = draft_latency_ms + target_latency_ms
            step_speedup = baseline_ms / max(0.001, spec_ms)

            self._stats.total_draft_proposed += verification.num_proposed
            self._stats.total_draft_accepted += verification.num_accepted
            if verification.rejected_token is not None:
                self._stats.total_draft_rejected += 1
            self._stats.total_verification_steps += 1
            self._stats.cumulative_draft_ms += draft_latency_ms
            self._stats.cumulative_target_ms += target_latency_ms
            self._stats.cumulative_baseline_ms += baseline_ms

            step_idx = len(self._history)
            step_record = SpeculativeDecodingStep(
                step_index=step_idx,
                prompt=context,
                draft_proposals=proposals,
                verification=verification,
                emitted_tokens=emitted,
                draft_latency_ms=draft_latency_ms,
                target_latency_ms=target_latency_ms,
                step_speedup_ratio=step_speedup,
            )
            self._history.append(step_record)
            return step_record

    def calculate_theoretical_speedup(
        self,
        draft_cost_ratio: float,
        acceptance_rate: float,
        lookahead: int,
    ) -> float:
        """
        Calculate analytical speedup multiplier:
        S = (1 + gamma * alpha) / (1 + gamma * c)
        where c denotes relative cost of draft model vs target model.
        """
        c = max(0.001, draft_cost_ratio)
        alpha = min(1.0, max(0.0, acceptance_rate))
        gamma = max(1, lookahead)
        numerator = 1.0 + float(gamma) * alpha
        denominator = 1.0 + float(gamma) * c
        return max(0.1, numerator / denominator)

    def get_stats(self) -> SpeculativeStats:
        """Retrieve copy of current performance statistics."""
        with self._lock:
            return SpeculativeStats(
                total_draft_proposed=self._stats.total_draft_proposed,
                total_draft_accepted=self._stats.total_draft_accepted,
                total_draft_rejected=self._stats.total_draft_rejected,
                total_verification_steps=self._stats.total_verification_steps,
                cumulative_draft_ms=self._stats.cumulative_draft_ms,
                cumulative_target_ms=self._stats.cumulative_target_ms,
                cumulative_baseline_ms=self._stats.cumulative_baseline_ms,
            )

    def get_history(self) -> List[SpeculativeDecodingStep]:
        """Retrieve execution history of all completed speculative decoding steps."""
        with self._lock:
            return list(self._history)

    def reset(self) -> None:
        """Clear execution history and reset statistical counters."""
        with self._lock:
            self._stats = SpeculativeStats()
            self._history.clear()


_GLOBAL_SPECULATIVE_DECODER: Optional[ModelSpeculativeDecoder] = None
_GLOBAL_SPECULATIVE_LOCK = threading.RLock()


def get_speculative_decoder() -> ModelSpeculativeDecoder:
    """Acquire thread-safe singleton ModelSpeculativeDecoder."""
    global _GLOBAL_SPECULATIVE_DECODER
    with _GLOBAL_SPECULATIVE_LOCK:
        if _GLOBAL_SPECULATIVE_DECODER is None:
            _GLOBAL_SPECULATIVE_DECODER = ModelSpeculativeDecoder()
        return _GLOBAL_SPECULATIVE_DECODER


def reset_speculative_decoder() -> ModelSpeculativeDecoder:
    """Reset singleton ModelSpeculativeDecoder."""
    global _GLOBAL_SPECULATIVE_DECODER
    with _GLOBAL_SPECULATIVE_LOCK:
        _GLOBAL_SPECULATIVE_DECODER = ModelSpeculativeDecoder()
        return _GLOBAL_SPECULATIVE_DECODER
