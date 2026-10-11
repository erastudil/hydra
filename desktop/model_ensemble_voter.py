"""
Hydra Desktop Model Ensemble Consensus Voter Subsystem.
Provides majority voting, confidence-weighted soft voting, Borda count ranking,
semantic disagreement detection, and consensus score telemetry across model swarms.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import collections
import math
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


class VotingMethod(str, Enum):
    """Supported voting aggregation methods."""
    MAJORITY = "majority"
    CONFIDENCE_WEIGHTED = "confidence_weighted"
    BORDA_COUNT = "borda_count"


@dataclass
class ModelVote:
    """Individual ballot cast by model ensemble member."""
    model_id: str
    candidate: str
    confidence: float = 1.0
    rankings: Optional[List[str]] = None
    text_response: str = ""
    model_weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize vote ballot to dictionary."""
        return {
            "model_id": self.model_id,
            "candidate": self.candidate,
            "confidence": self.confidence,
            "rankings": list(self.rankings) if self.rankings else None,
            "text_response": self.text_response,
            "model_weight": self.model_weight,
            "metadata": dict(self.metadata),
        }


@dataclass
class ConsensusScore:
    """Consensus telemetry packet synthesizing ensemble deliberation."""
    winning_candidate: str
    confidence_score: float
    agreement_ratio: float
    entropy: float
    unanimous: bool
    disagreement_index: float
    candidate_scores: Dict[str, float]
    outlier_models: List[str]
    method: VotingMethod
    total_votes: int
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize consensus metrics to dictionary."""
        return {
            "winning_candidate": self.winning_candidate,
            "confidence_score": round(self.confidence_score, 4),
            "agreement_ratio": round(self.agreement_ratio, 4),
            "entropy": round(self.entropy, 4),
            "unanimous": self.unanimous,
            "disagreement_index": round(self.disagreement_index, 4),
            "candidate_scores": {k: round(v, 4) for k, v in self.candidate_scores.items()},
            "outlier_models": list(self.outlier_models),
            "method": self.method.value,
            "total_votes": self.total_votes,
            "telemetry": dict(self.telemetry),
        }


def compute_normalized_entropy(distribution: Sequence[float]) -> float:
    """Calculate normalized Shannon entropy in closed interval [0.0, 1.0]."""
    total = sum(distribution)
    if total <= 0.0 or len(distribution) <= 1:
        return 0.0

    probs = [v / total for v in distribution if v > 0.0]
    if len(probs) <= 1:
        return 0.0

    raw_entropy = -sum(p * math.log2(p) for p in probs)
    max_entropy = math.log2(len(distribution))
    if max_entropy <= 0.0:
        return 0.0
    return min(1.0, max(0.0, raw_entropy / max_entropy))


def tokenize_text(text: str) -> Set[str]:
    """Extract normalized alphanumeric word tokens from raw text string."""
    words = re.findall(r"\b[\w-]+\b", text.lower())
    return set(words)


def compute_jaccard_similarity(tokens_a: Set[str], tokens_b: Set[str]) -> float:
    """Calculate Jaccard token overlap index across two token sets."""
    if not tokens_a and not tokens_b:
        return 1.0
    union_len = len(tokens_a | tokens_b)
    if union_len == 0:
        return 1.0
    intersection_len = len(tokens_a & tokens_b)
    return float(intersection_len) / float(union_len)


class EnsembleConsensusVoter:
    """
    Ensemble consensus voting engine.
    Aggregates multi-model outputs via plurality majority, confidence-weighted soft voting,
    and Borda count ranking; measures semantic divergence and outlier behaviors.
    """

    def __init__(self, semantic_disagreement_threshold: float = 0.4) -> None:
        self.semantic_disagreement_threshold = semantic_disagreement_threshold
        self._history: List[ConsensusScore] = []
        self._lock = threading.RLock()

    def majority_vote(self, votes: Sequence[ModelVote]) -> ConsensusScore:
        """Execute discrete plurality hard voting across ensemble ballots."""
        if not votes:
            raise ValueError("Voting requires at least one vote ballot")

        counts: Dict[str, float] = collections.defaultdict(float)
        for v in votes:
            counts[v.candidate] += v.model_weight

        total_weight = sum(counts.values())
        sorted_candidates = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        winner, win_weight = sorted_candidates[0]

        distribution = list(counts.values())
        entropy_val = compute_normalized_entropy(distribution)
        agreement = (win_weight / total_weight) if total_weight > 0 else 0.0
        is_unanimous = len(counts) == 1

        disagreement = detect_semantic_divergence(votes)
        outliers = [
            v.model_id for v in votes
            if v.candidate != winner or v.model_id in disagreement.get("outlier_models", [])
        ]

        # Mean confidence of winning cohort
        winner_confs = [v.confidence for v in votes if v.candidate == winner]
        mean_conf = (sum(winner_confs) / len(winner_confs)) if winner_confs else 1.0

        score = ConsensusScore(
            winning_candidate=winner,
            confidence_score=mean_conf,
            agreement_ratio=agreement,
            entropy=entropy_val,
            unanimous=is_unanimous,
            disagreement_index=disagreement.get("overall_disagreement", 1.0 - agreement),
            candidate_scores=dict(counts),
            outlier_models=outliers,
            method=VotingMethod.MAJORITY,
            total_votes=len(votes),
            telemetry={"total_weight": total_weight, "semantic_metrics": disagreement},
        )
        return score

    def weighted_soft_vote(self, votes: Sequence[ModelVote]) -> ConsensusScore:
        """Aggregate continuous confidence-weighted soft voting probabilities."""
        if not votes:
            raise ValueError("Voting requires at least one vote ballot")

        scores: Dict[str, float] = collections.defaultdict(float)
        for v in votes:
            effective_conf = max(0.0, min(1.0, v.confidence))
            scores[v.candidate] += effective_conf * v.model_weight

        total_score = sum(scores.values())
        if total_score <= 0.0:
            return self.majority_vote(votes)

        sorted_candidates = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        winner, win_score = sorted_candidates[0]

        distribution = list(scores.values())
        entropy_val = compute_normalized_entropy(distribution)
        agreement = (win_score / total_score) if total_score > 0 else 0.0

        winner_votes = [v for v in votes if v.candidate == winner]
        is_unanimous = len(scores) == 1

        disagreement = detect_semantic_divergence(votes)
        outliers = [v.model_id for v in votes if v.candidate != winner]

        normalized_scores = {k: v / total_score for k, v in scores.items()}

        score = ConsensusScore(
            winning_candidate=winner,
            confidence_score=agreement,
            agreement_ratio=agreement,
            entropy=entropy_val,
            unanimous=is_unanimous,
            disagreement_index=disagreement.get("overall_disagreement", entropy_val),
            candidate_scores=normalized_scores,
            outlier_models=outliers,
            method=VotingMethod.CONFIDENCE_WEIGHTED,
            total_votes=len(votes),
            telemetry={"total_score": total_score, "semantic_metrics": disagreement},
        )
        return score

    def borda_count_vote(
        self,
        votes: Sequence[ModelVote],
        candidate_pool: Optional[Sequence[str]] = None,
    ) -> ConsensusScore:
        """Execute Borda count preferential ranking aggregation."""
        if not votes:
            raise ValueError("Voting requires at least one vote ballot")

        all_candidates: Set[str] = set(candidate_pool) if candidate_pool else set()
        for v in votes:
            all_candidates.add(v.candidate)
            if v.rankings:
                all_candidates.update(v.rankings)

        candidates_list = sorted(all_candidates)
        m = len(candidates_list)
        if m == 0:
            raise ValueError("Candidate pool contains zero options")

        borda_points: Dict[str, float] = {c: 0.0 for c in candidates_list}

        for v in votes:
            rank_list = list(v.rankings) if v.rankings else [v.candidate]
            seen_in_ballot: Set[str] = set()

            for rank_idx, cand in enumerate(rank_list):
                if cand in borda_points and cand not in seen_in_ballot:
                    points = max(0, m - 1 - rank_idx) * v.model_weight
                    borda_points[cand] += points
                    seen_in_ballot.add(cand)

        total_points = sum(borda_points.values())
        sorted_candidates = sorted(borda_points.items(), key=lambda item: (-item[1], item[0]))
        winner, win_points = sorted_candidates[0]

        distribution = list(borda_points.values())
        entropy_val = compute_normalized_entropy(distribution)
        agreement = (win_points / total_points) if total_points > 0 else 0.0

        disagreement = detect_semantic_divergence(votes)
        outliers = [v.model_id for v in votes if (v.rankings and v.rankings[0] != winner) or (not v.rankings and v.candidate != winner)]

        normalized_scores = {k: v / total_points if total_points > 0 else 0.0 for k, v in borda_points.items()}

        score = ConsensusScore(
            winning_candidate=winner,
            confidence_score=agreement,
            agreement_ratio=agreement,
            entropy=entropy_val,
            unanimous=(len(votes) > 1 and all(v.candidate == winner for v in votes)),
            disagreement_index=disagreement.get("overall_disagreement", entropy_val),
            candidate_scores=normalized_scores,
            outlier_models=outliers,
            method=VotingMethod.BORDA_COUNT,
            total_votes=len(votes),
            telemetry={"total_points": total_points, "borda_points": borda_points},
        )
        return score

    def evaluate_consensus(
        self,
        votes: Sequence[ModelVote],
        method: VotingMethod = VotingMethod.CONFIDENCE_WEIGHTED,
    ) -> ConsensusScore:
        """Evaluate ensemble deliberation and record consensus telemetry."""
        with self._lock:
            if method == VotingMethod.MAJORITY:
                res = self.majority_vote(votes)
            elif method == VotingMethod.CONFIDENCE_WEIGHTED:
                res = self.weighted_soft_vote(votes)
            elif method == VotingMethod.BORDA_COUNT:
                res = self.borda_count_vote(votes)
            else:
                raise ValueError(f"Unknown voting method: {method}")

            self._history.append(res)
            return res

    def get_history(self) -> List[ConsensusScore]:
        """Retrieve chronological sequence of consensus evaluations."""
        with self._lock:
            return list(self._history)

    def reset_history(self) -> None:
        """Clear consensus history telemetry."""
        with self._lock:
            self._history.clear()


def detect_semantic_divergence(
    votes: Sequence[ModelVote],
    threshold: float = 0.4,
) -> Dict[str, Any]:
    """Analyze pairwise textual and candidate token divergence across votes."""
    if len(votes) <= 1:
        return {
            "overall_disagreement": 0.0,
            "mean_pairwise_similarity": 1.0,
            "outlier_models": [],
        }

    token_sets = [
        tokenize_text(f"{v.candidate} {v.text_response}")
        for v in votes
    ]

    n = len(votes)
    pair_sims: List[float] = []
    model_avg_sim: Dict[str, float] = {}

    for i in range(n):
        sims_for_i: List[float] = []
        for j in range(n):
            if i != j:
                sim = compute_jaccard_similarity(token_sets[i], token_sets[j])
                pair_sims.append(sim)
                sims_for_i.append(sim)
        model_avg_sim[votes[i].model_id] = sum(sims_for_i) / len(sims_for_i) if sims_for_i else 1.0

    mean_sim = sum(pair_sims) / len(pair_sims) if pair_sims else 1.0
    overall_disagreement = max(0.0, min(1.0, 1.0 - mean_sim))

    outliers = [
        m_id for m_id, avg_sim in model_avg_sim.items()
        if avg_sim < threshold
    ]

    return {
        "overall_disagreement": round(overall_disagreement, 4),
        "mean_pairwise_similarity": round(mean_sim, 4),
        "outlier_models": outliers,
        "model_similarities": {k: round(v, 4) for k, v in model_avg_sim.items()},
    }


_GLOBAL_ENSEMBLE_VOTER: Optional[EnsembleConsensusVoter] = None
_GLOBAL_VOTER_LOCK = threading.RLock()


def get_ensemble_voter() -> EnsembleConsensusVoter:
    """Acquire thread-safe singleton EnsembleConsensusVoter."""
    global _GLOBAL_ENSEMBLE_VOTER
    with _GLOBAL_VOTER_LOCK:
        if _GLOBAL_ENSEMBLE_VOTER is None:
            _GLOBAL_ENSEMBLE_VOTER = EnsembleConsensusVoter()
        return _GLOBAL_ENSEMBLE_VOTER


def reset_ensemble_voter() -> EnsembleConsensusVoter:
    """Reset singleton EnsembleConsensusVoter."""
    global _GLOBAL_ENSEMBLE_VOTER
    with _GLOBAL_VOTER_LOCK:
        _GLOBAL_ENSEMBLE_VOTER = EnsembleConsensusVoter()
        return _GLOBAL_ENSEMBLE_VOTER
