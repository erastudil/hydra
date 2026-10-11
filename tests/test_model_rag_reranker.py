"""
Unit tests for Hydra Desktop Model RAG Cross-Encoder Reranker Subsystem.
Verifies cross-encoder scoring, Reciprocal Rank Fusion (RRF),
candidate truncation, and score normalization.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest
from desktop.model_rag_reranker import (
    ModelRagReranker,
    NormalizationMethod,
    RerankCandidate,
    compute_cross_encoder_score,
    get_rag_reranker,
    normalize_scores,
    reset_rag_reranker,
)


def test_cross_encoder_scoring_relevance():
    query = "speculative decoding latency speedup"

    doc_high = "Speculative decoding accelerates inference latency with empirical speedup gains."
    doc_low = "The database cluster maintains replication logs for disaster recovery."

    score_high = compute_cross_encoder_score(query, doc_high)
    score_low = compute_cross_encoder_score(query, doc_low)

    assert score_high > score_low
    assert score_high > 0.35
    assert score_low == 0.0


def test_score_normalization_methods():
    scores = [1.0, 2.0, 5.0]

    # Min-max
    norm_minmax = normalize_scores(scores, NormalizationMethod.MIN_MAX)
    assert norm_minmax[0] == 0.0
    assert norm_minmax[2] == 1.0

    # Sigmoid
    norm_sigmoid = normalize_scores(scores, NormalizationMethod.SIGMOID)
    assert all(0.0 < s < 1.0 for s in norm_sigmoid)
    assert norm_sigmoid[0] < norm_sigmoid[1] < norm_sigmoid[2]

    # Softmax
    norm_softmax = normalize_scores(scores, NormalizationMethod.SOFTMAX)
    assert sum(norm_softmax) == pytest.approx(1.0)
    assert norm_softmax[2] > norm_softmax[1] > norm_softmax[0]


def test_reranker_filtering_and_truncation():
    reranker = ModelRagReranker(default_top_k=2)

    candidates = [
        RerankCandidate("c1", "completely irrelevant text about gardening"),
        RerankCandidate("c2", "hydra model routing and load balancing"),
        RerankCandidate("c3", "hydra model routing accelerator"),
    ]

    results = reranker.rerank(
        query="hydra model routing",
        candidates=candidates,
        top_k=2,
        min_score=0.1,
        normalization=NormalizationMethod.NONE,
    )

    assert len(results) == 2
    assert results[0].final_rank == 1
    assert results[1].final_rank == 2
    assert "hydra" in results[0].text
    assert "hydra" in results[1].text
    assert results[0].candidate_id in ("c2", "c3")


def test_reciprocal_rank_fusion():
    reranker = ModelRagReranker()

    # Dense retrieval run
    run_dense = [
        RerankCandidate("doc_a", "text a"),
        RerankCandidate("doc_b", "text b"),
        RerankCandidate("doc_c", "text c"),
    ]

    # Sparse BM25 retrieval run
    run_sparse = [
        RerankCandidate("doc_b", "text b"),
        RerankCandidate("doc_a", "text a"),
        RerankCandidate("doc_d", "text d"),
    ]

    fused = reranker.reciprocal_rank_fusion([run_dense, run_sparse], k_constant=60)
    # doc_a: 1/(60+1) + 1/(60+2) = 1/61 + 1/62 = 0.01639 + 0.01613 = 0.03252
    # doc_b: 1/(60+2) + 1/(60+1) = 0.03252 (tied)
    # doc_c: 1/(60+3) = 0.01587
    # doc_d: 1/(60+3) = 0.01587
    assert len(fused) == 4
    top_ids = {fused[0].candidate_id, fused[1].candidate_id}
    assert top_ids == {"doc_a", "doc_b"}
    assert fused[0].final_rank == 1
    assert fused[1].final_rank == 2


def test_custom_scoring_function():
    reranker = ModelRagReranker()

    candidates = [
        RerankCandidate("c1", "apple"),
        RerankCandidate("c2", "banana"),
    ]

    def custom_scorer(q: str, doc: str) -> float:
        return float(len(doc))

    results = reranker.rerank(
        query="test",
        candidates=candidates,
        scoring_fn=custom_scorer,
        normalization=NormalizationMethod.NONE,
    )

    # banana has 6 chars, apple has 5 chars
    assert results[0].candidate_id == "c2"
    assert results[0].rerank_score == 6.0
    assert results[1].candidate_id == "c1"


def test_singleton_reranker_and_reset():
    r1 = get_rag_reranker()
    r2 = get_rag_reranker()
    assert r1 is r2

    r3 = reset_rag_reranker()
    assert r3 is not r1
    assert get_rag_reranker() is r3
