"""
Hydra Desktop Model RAG Cross-Encoder Reranker Subsystem.
Provides cross-encoder scoring, Reciprocal Rank Fusion (RRF),
candidate truncation, and score normalization across multi-modal retrieval lists.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import collections
import math
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union


class NormalizationMethod(str, Enum):
    """Supported relevance score normalization techniques."""
    MIN_MAX = "min_max"
    SIGMOID = "sigmoid"
    SOFTMAX = "softmax"
    NONE = "none"


@dataclass
class RerankCandidate:
    """Document chunk candidate subjected to relevance reranking."""
    candidate_id: str
    text: str
    initial_score: float = 0.0
    initial_rank: int = 0
    rerank_score: float = 0.0
    final_rank: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize rerank candidate to dictionary."""
        return {
            "candidate_id": self.candidate_id,
            "text": self.text,
            "initial_score": round(self.initial_score, 4),
            "initial_rank": self.initial_rank,
            "rerank_score": round(self.rerank_score, 4),
            "final_rank": self.final_rank,
            "metadata": dict(self.metadata),
        }


def extract_terms(text: str) -> List[str]:
    """Extract lowercased alphanumeric tokens from text string."""
    return re.findall(r"\b[\w-]+\b", text.lower())


def compute_cross_encoder_score(query: str, document_text: str) -> float:
    """
    Deterministic cross-encoder relevance function.
    Evaluates joint query-document interactions via token overlap,
    exact phrase matching, bi-gram matching, and proximity density.
    """
    q_tokens = extract_terms(query)
    d_tokens = extract_terms(document_text)

    if not q_tokens or not d_tokens:
        return 0.0

    q_set = set(q_tokens)
    d_set = set(d_tokens)

    # 1. Unigram recall & precision
    overlap = q_set & d_set
    recall = len(overlap) / float(len(q_set))
    precision = len(overlap) / float(len(d_set)) if d_set else 0.0
    unigram_f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    # 2. Bigram overlap
    def get_bigrams(tokens: Sequence[str]) -> Set[Tuple[str, str]]:
        if len(tokens) < 2:
            return set()
        return {tuple(tokens[i : i + 2]) for i in range(len(tokens) - 1)}

    q_bigrams = get_bigrams(q_tokens)
    d_bigrams = get_bigrams(d_tokens)
    bigram_overlap = len(q_bigrams & d_bigrams) / float(len(q_bigrams)) if q_bigrams else 0.0

    # 3. Exact query substring bonus
    exact_match = 1.0 if query.strip().lower() in document_text.lower() else 0.0

    # 4. Proximity penalty: distance between query terms in document
    if not overlap:
        span_factor = 0.0
    else:
        first_positions = [d_tokens.index(t) for t in overlap if t in d_tokens]
        span_factor = 1.0
        if len(first_positions) > 1:
            span = max(first_positions) - min(first_positions) + 1
            span_factor = len(first_positions) / float(span)

    composite_score = (
        0.4 * unigram_f1
        + 0.3 * bigram_overlap
        + 0.2 * exact_match
        + 0.1 * span_factor
    )
    return composite_score


def normalize_scores(
    scores: Sequence[float],
    method: NormalizationMethod = NormalizationMethod.MIN_MAX,
) -> List[float]:
    """Scale sequence of numerical scores according to specified normalization technique."""
    if not scores:
        return []

    if method == NormalizationMethod.NONE:
        return list(scores)

    if method == NormalizationMethod.MIN_MAX:
        min_v = min(scores)
        max_v = max(scores)
        diff = max_v - min_v
        if diff <= 0.0:
            return [1.0] * len(scores)
        return [(s - min_v) / diff for s in scores]

    if method == NormalizationMethod.SIGMOID:
        # Standard logistic function 1 / (1 + e^-x)
        return [1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, s)))) for s in scores]

    if method == NormalizationMethod.SOFTMAX:
        max_s = max(scores)
        exps = [math.exp(s - max_s) for s in scores]
        sum_e = sum(exps)
        if sum_e <= 0.0:
            return [1.0 / len(scores)] * len(scores)
        return [e / sum_e for e in exps]

    return list(scores)


class ModelRagReranker:
    """
    RAG retrieval cross-encoder reranker engine.
    Orchestrates joint semantic scoring, Reciprocal Rank Fusion across disparate search lists,
    filtering, truncation, and output normalization.
    """

    def __init__(
        self,
        default_top_k: int = 10,
        min_score_threshold: float = 0.0,
        default_normalization: NormalizationMethod = NormalizationMethod.MIN_MAX,
    ) -> None:
        self.default_top_k = max(1, default_top_k)
        self.min_score_threshold = min_score_threshold
        self.default_normalization = default_normalization
        self._lock = threading.RLock()

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        normalization: Optional[NormalizationMethod] = None,
        scoring_fn: Optional[Callable[[str, str], float]] = None,
    ) -> List[RerankCandidate]:
        """
        Rerank document candidates against query using cross-encoder scoring.
        Applies filtering, sort ordering, rank assignment, and score normalization.
        """
        with self._lock:
            if not candidates:
                return []

            k = top_k if top_k is not None else self.default_top_k
            threshold = min_score if min_score is not None else self.min_score_threshold
            norm_method = normalization if normalization is not None else self.default_normalization
            scorer = scoring_fn or compute_cross_encoder_score

            scored_list: List[RerankCandidate] = []
            for cand in candidates:
                score = scorer(query, cand.text)
                new_cand = RerankCandidate(
                    candidate_id=cand.candidate_id,
                    text=cand.text,
                    initial_score=cand.initial_score,
                    initial_rank=cand.initial_rank,
                    rerank_score=score,
                    metadata=dict(cand.metadata),
                )
                scored_list.append(new_cand)

            # Sort descending by raw rerank score
            scored_list.sort(key=lambda c: (-c.rerank_score, c.candidate_id))

            # Filter below min threshold
            filtered = [c for c in scored_list if c.rerank_score >= threshold]

            # Truncate to top_k
            truncated = filtered[:k]

            # Assign 1-based final ranks
            for idx, c in enumerate(truncated, start=1):
                c.final_rank = idx

            # Normalize scores if requested
            if norm_method != NormalizationMethod.NONE and truncated:
                raw_scores = [c.rerank_score for c in truncated]
                norm_vals = normalize_scores(raw_scores, norm_method)
                for c, nv in zip(truncated, norm_vals):
                    c.rerank_score = nv

            return truncated

    def reciprocal_rank_fusion(
        self,
        ranked_runs: Sequence[Sequence[RerankCandidate]],
        k_constant: int = 60,
        top_k: Optional[int] = None,
    ) -> List[RerankCandidate]:
        """
        Fuse multiple retrieval runs via Reciprocal Rank Fusion (RRF).
        Formula: RRF_score(d) = sum_m 1 / (k_constant + rank_m(d))
        """
        with self._lock:
            if not ranked_runs:
                return []

            rrf_scores: Dict[str, float] = collections.defaultdict(float)
            candidate_map: Dict[str, RerankCandidate] = {}

            for run in ranked_runs:
                for rank_idx, cand in enumerate(run, start=1):
                    cid = cand.candidate_id
                    rrf_scores[cid] += 1.0 / float(k_constant + rank_idx)
                    if cid not in candidate_map:
                        candidate_map[cid] = cand

            sorted_ids = sorted(rrf_scores.items(), key=lambda item: (-item[1], item[0]))
            fused: List[RerankCandidate] = []

            for rank_idx, (cid, score) in enumerate(sorted_ids, start=1):
                base = candidate_map[cid]
                merged = RerankCandidate(
                    candidate_id=cid,
                    text=base.text,
                    initial_score=base.initial_score,
                    initial_rank=base.initial_rank,
                    rerank_score=score,
                    final_rank=rank_idx,
                    metadata=dict(base.metadata),
                )
                merged.metadata["rrf_raw_score"] = score
                fused.append(merged)

            limit = top_k if top_k is not None else self.default_top_k
            return fused[:limit]


_GLOBAL_RAG_RERANKER: Optional[ModelRagReranker] = None
_GLOBAL_RERANKER_LOCK = threading.RLock()


def get_rag_reranker() -> ModelRagReranker:
    """Acquire thread-safe singleton ModelRagReranker."""
    global _GLOBAL_RAG_RERANKER
    with _GLOBAL_RERANKER_LOCK:
        if _GLOBAL_RAG_RERANKER is None:
            _GLOBAL_RAG_RERANKER = ModelRagReranker()
        return _GLOBAL_RAG_RERANKER


def reset_rag_reranker() -> ModelRagReranker:
    """Reset singleton ModelRagReranker."""
    global _GLOBAL_RAG_RERANKER
    with _GLOBAL_RERANKER_LOCK:
        _GLOBAL_RAG_RERANKER = ModelRagReranker()
        return _GLOBAL_RAG_RERANKER
