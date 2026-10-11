"""
Unit tests for Hydra Desktop Model Ensemble Consensus Voter Subsystem.
Verifies majority voting, confidence-weighted soft voting, Borda count ranking,
semantic disagreement detection, and consensus score telemetry.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest
from desktop.model_ensemble_voter import (
    ConsensusScore,
    EnsembleConsensusVoter,
    ModelVote,
    VotingMethod,
    compute_jaccard_similarity,
    compute_normalized_entropy,
    detect_semantic_divergence,
    get_ensemble_voter,
    reset_ensemble_voter,
    tokenize_text,
)


def test_model_vote_and_score_serialization():
    vote = ModelVote(
        model_id="qwen-3.8",
        candidate="python_backend",
        confidence=0.92,
        rankings=["python_backend", "go_backend", "node_backend"],
        text_response="optimal latency and posix compliance",
        model_weight=1.5,
    )
    data = vote.to_dict()
    assert data["model_id"] == "qwen-3.8"
    assert data["candidate"] == "python_backend"
    assert data["model_weight"] == 1.5

    entropy = compute_normalized_entropy([0.8, 0.2])
    assert 0.0 < entropy < 1.0
    assert compute_normalized_entropy([1.0, 0.0]) == 0.0


def test_majority_voting_and_unanimity():
    voter = EnsembleConsensusVoter()

    # Unanimous cohort
    votes = [
        ModelVote("m1", "action_click", 0.95),
        ModelVote("m2", "action_click", 0.90),
        ModelVote("m3", "action_click", 0.85),
    ]
    res = voter.majority_vote(votes)
    assert res.winning_candidate == "action_click"
    assert res.agreement_ratio == 1.0
    assert res.unanimous is True
    assert res.entropy == 0.0
    assert len(res.outlier_models) == 0

    # Non-unanimous cohort
    votes_split = [
        ModelVote("m1", "option_a", 0.9),
        ModelVote("m2", "option_a", 0.8),
        ModelVote("m3", "option_b", 0.7),
    ]
    res_split = voter.majority_vote(votes_split)
    assert res_split.winning_candidate == "option_a"
    assert res_split.agreement_ratio == pytest.approx(2.0 / 3.0)
    assert res_split.unanimous is False
    assert "m3" in res_split.outlier_models


def test_confidence_weighted_soft_voting():
    voter = EnsembleConsensusVoter()

    # m3 has high weight and confidence in option_b
    votes = [
        ModelVote("m1", "option_a", confidence=0.4, model_weight=1.0),
        ModelVote("m2", "option_a", confidence=0.4, model_weight=1.0),
        ModelVote("m3", "option_b", confidence=0.95, model_weight=2.0),
    ]
    res = voter.weighted_soft_vote(votes)
    # total score option_a = 0.4 + 0.4 = 0.8
    # total score option_b = 0.95 * 2.0 = 1.90
    assert res.winning_candidate == "option_b"
    assert res.method == VotingMethod.CONFIDENCE_WEIGHTED
    assert res.candidate_scores["option_b"] > res.candidate_scores["option_a"]


def test_borda_count_ranking():
    voter = EnsembleConsensusVoter()

    votes = [
        ModelVote("m1", "cand_a", rankings=["cand_a", "cand_b", "cand_c"]),
        ModelVote("m2", "cand_b", rankings=["cand_b", "cand_a", "cand_c"]),
        ModelVote("m3", "cand_a", rankings=["cand_a", "cand_c", "cand_b"]),
    ]

    res = voter.borda_count_vote(votes)
    # cand_a gets 2 + 1 + 2 = 5 points
    # cand_b gets 1 + 2 + 0 = 3 points
    # cand_c gets 0 + 0 + 1 = 1 point
    assert res.winning_candidate == "cand_a"
    assert res.method == VotingMethod.BORDA_COUNT
    assert res.candidate_scores["cand_a"] > res.candidate_scores["cand_b"]


def test_semantic_disagreement_detection():
    tokens1 = tokenize_text("deterministic mathematical logic")
    tokens2 = tokenize_text("deterministic scientific logic")
    tokens3 = tokenize_text("complete unrelated poetry words")

    sim_12 = compute_jaccard_similarity(tokens1, tokens2)
    sim_13 = compute_jaccard_similarity(tokens1, tokens3)
    assert sim_12 > sim_13
    assert sim_13 == 0.0

    votes = [
        ModelVote("m1", "code", text_response="deterministic invariant checking"),
        ModelVote("m2", "code", text_response="deterministic invariant validation"),
        ModelVote("m3", "other", text_response="freeform expressive writing"),
    ]

    divergence = detect_semantic_divergence(votes, threshold=0.3)
    assert divergence["overall_disagreement"] > 0.0
    assert "m3" in divergence["outlier_models"]


def test_voter_evaluation_and_history():
    voter = EnsembleConsensusVoter()
    votes = [
        ModelVote("m1", "plan_1", 0.9),
        ModelVote("m2", "plan_1", 0.8),
    ]

    score = voter.evaluate_consensus(votes, method=VotingMethod.MAJORITY)
    assert score.winning_candidate == "plan_1"

    hist = voter.get_history()
    assert len(hist) == 1
    assert hist[0].winning_candidate == "plan_1"

    voter.reset_history()
    assert len(voter.get_history()) == 0


def test_singleton_voter_and_reset():
    v1 = get_ensemble_voter()
    v2 = get_ensemble_voter()
    assert v1 is v2

    v3 = reset_ensemble_voter()
    assert v3 is not v1
    assert get_ensemble_voter() is v3
