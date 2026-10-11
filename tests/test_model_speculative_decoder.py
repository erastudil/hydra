"""
Unit tests for Hydra Desktop Speculative Decoding Accelerator Subsystem.
Verifies draft proposal generation, target verification, acceptance criteria,
rejection rollback, and speculative speedup ratio calculation.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest
from desktop.model_speculative_decoder import (
    AcceptanceCriterion,
    DraftTokenProposal,
    VerificationResult,
    SpeculativeDecodingStep,
    SpeculativeStats,
    NGramDraftGenerator,
    ModelSpeculativeDecoder,
    get_speculative_decoder,
    reset_speculative_decoder,
)


def test_draft_proposal_and_ngram_generator():
    gen = NGramDraftGenerator(n=3)
    text = (
        "the sovereign multi headed engine routes prompts across models. "
        "the sovereign multi headed router optimizes compute latency. "
        "the sovereign multi headed runner dispatches autonomous tasks."
    )
    gen.train(text)

    proposals = gen.propose("the sovereign", lookahead=3)
    assert len(proposals) >= 1
    assert proposals[0].token_str == "multi"
    if len(proposals) >= 2:
        assert proposals[1].token_str == "headed"

    data = proposals[0].to_dict()
    assert data["token_str"] == "multi"
    assert data["position"] > 0


def test_speculative_step_all_accepted():
    decoder = ModelSpeculativeDecoder(
        default_lookahead=3,
        criterion=AcceptanceCriterion.GREEDY_MATCH,
    )

    def draft_engine(ctx: str, k: int):
        return [
            DraftTokenProposal("sovereign", "sovereign", 0.9, 1),
            DraftTokenProposal("engine", "engine", 0.85, 2),
            DraftTokenProposal("accelerator", "accelerator", 0.8, 3),
        ]

    def target_engine(ctx: str, props):
        return [
            (p, 0.95, DraftTokenProposal(p.token_id, p.token_str, 0.95, p.position))
            for p in props
        ]

    step = decoder.execute_step(
        context="hydra",
        draft_callable=draft_engine,
        target_verifier=target_engine,
        lookahead=3,
        draft_latency_ms=5.0,
        target_latency_ms=25.0,
    )

    assert len(step.verification.accepted_tokens) == 3
    assert step.verification.rejected_token is None
    assert step.verification.corrected_token is None
    assert step.verification.acceptance_rate == 1.0
    assert step.verification.rollback_index is None
    assert len(step.emitted_tokens) == 3
    assert step.step_speedup_ratio > 1.0


def test_speculative_step_rejection_and_rollback():
    decoder = ModelSpeculativeDecoder(
        default_lookahead=3,
        criterion=AcceptanceCriterion.GREEDY_MATCH,
    )

    def draft_engine(ctx: str, k: int):
        return [
            DraftTokenProposal("token1", "token1", 0.9, 1),
            DraftTokenProposal("token2_bad", "token2_bad", 0.8, 2),
            DraftTokenProposal("token3", "token3", 0.7, 3),
        ]

    def target_engine(ctx: str, props):
        return [
            (props[0], 0.95, DraftTokenProposal("token1", "token1", 0.95, 1)),
            (props[1], 0.10, DraftTokenProposal("token2_correct", "token2_correct", 0.90, 2)),
            (props[2], 0.80, DraftTokenProposal("token3", "token3", 0.80, 3)),
        ]

    step = decoder.execute_step(
        context="prefix",
        draft_callable=draft_engine,
        target_verifier=target_engine,
        lookahead=3,
        draft_latency_ms=4.0,
        target_latency_ms=20.0,
    )

    assert len(step.verification.accepted_tokens) == 1
    assert step.verification.rejected_token is not None
    assert step.verification.rejected_token.token_str == "token2_bad"
    assert step.verification.corrected_token is not None
    assert step.verification.corrected_token.token_str == "token2_correct"
    assert step.verification.rollback_index == 1
    assert len(step.emitted_tokens) == 2
    assert step.emitted_tokens[0].token_str == "token1"
    assert step.emitted_tokens[1].token_str == "token2_correct"


def test_speculative_threshold_and_prob_ratio():
    decoder_thresh = ModelSpeculativeDecoder(
        criterion=AcceptanceCriterion.THRESHOLD,
        acceptance_threshold=0.7,
    )

    proposals = [
        DraftTokenProposal("a", "a", 0.8, 1),
        DraftTokenProposal("b", "b", 0.6, 2),
    ]

    def verifier(ctx, props):
        return [
            (props[0], 0.85, None),
            (props[1], 0.40, DraftTokenProposal("c", "c", 0.9, 2)),
        ]

    res = decoder_thresh.verify_proposals("test", proposals, verifier)
    assert len(res.accepted_tokens) == 1
    assert res.accepted_tokens[0].token_str == "a"
    assert res.rejected_token.token_str == "b"
    assert res.corrected_token.token_str == "c"
    assert res.rollback_index == 1


def test_speedup_calculation_and_statistics():
    decoder = ModelSpeculativeDecoder()
    speedup = decoder.calculate_theoretical_speedup(
        draft_cost_ratio=0.1,
        acceptance_rate=0.8,
        lookahead=4,
    )
    assert speedup > 1.5

    def draft_engine(ctx: str, k: int):
        return [DraftTokenProposal(f"tok_{i}", f"tok_{i}", 0.9, i) for i in range(k)]

    def target_engine(ctx: str, props):
        return [(p, 0.95, p) for p in props]

    decoder.execute_step("prompt_1", draft_engine, target_engine, lookahead=2, draft_latency_ms=2.0, target_latency_ms=10.0)
    decoder.execute_step("prompt_2", draft_engine, target_engine, lookahead=3, draft_latency_ms=3.0, target_latency_ms=15.0)

    stats = decoder.get_stats()
    assert stats.total_draft_proposed == 5
    assert stats.total_draft_accepted == 5
    assert stats.total_draft_rejected == 0
    assert stats.total_verification_steps == 2
    assert stats.acceptance_rate == 1.0
    assert stats.effective_tokens_per_step == 3.5
    assert stats.speculative_speedup_ratio > 1.0

    history = decoder.get_history()
    assert len(history) == 2

    decoder.reset()
    clean_stats = decoder.get_stats()
    assert clean_stats.total_draft_proposed == 0
    assert len(decoder.get_history()) == 0


def test_singleton_getter_and_reset():
    d1 = get_speculative_decoder()
    d2 = get_speculative_decoder()
    assert d1 is d2

    d3 = reset_speculative_decoder()
    assert d3 is not d1
    assert get_speculative_decoder() is d3
