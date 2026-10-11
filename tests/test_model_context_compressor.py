"""
Integration test suite for Hydra Desktop Model Context Compressor.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest

from desktop.model_context_compressor import (
    ModelContextCompressor,
    ContextMessage,
    MessageRole,
    CompressionStrategy,
    CompressionResult,
    get_context_compressor,
    reset_context_compressor,
)


@pytest.fixture
def compressor():
    c = ModelContextCompressor(
        default_token_budget=100,
        sink_messages=1,
        recent_messages=2,
    )
    yield c


def test_token_estimation_and_passthrough_when_under_budget(compressor: ModelContextCompressor):
    """Verify context messages below token budget pass through unmutated."""
    msgs = [
        ContextMessage(role=MessageRole.SYSTEM.value, content="System prompt instructions"),
        ContextMessage(role=MessageRole.USER.value, content="Hello there"),
    ]
    res = compressor.compress_context(msgs, budget=200)

    assert res.compressed_message_count == 2
    assert res.tokens_saved == 0
    assert res.evicted_count == 0
    assert res.messages[0].content == "System prompt instructions"


def test_attention_sink_preservation_and_middle_pruning(compressor: ModelContextCompressor):
    """Verify attention sink (first message) is preserved while middle messages are evicted."""
    msgs = [
        ContextMessage(role=MessageRole.SYSTEM.value, content="Critical System Invariants (Sink)"),
        ContextMessage(role=MessageRole.USER.value, content="Old question 1" * 10),
        ContextMessage(role=MessageRole.ASSISTANT.value, content="Old answer 1" * 10),
        ContextMessage(role=MessageRole.USER.value, content="Old question 2" * 10),
        ContextMessage(role=MessageRole.ASSISTANT.value, content="Old answer 2" * 10),
        ContextMessage(role=MessageRole.USER.value, content="Recent question"),
        ContextMessage(role=MessageRole.ASSISTANT.value, content="Recent answer"),
    ]

    total_before = compressor.calculate_total_tokens(msgs)
    assert total_before > 100

    res = compressor.compress_context(msgs, budget=80, strategy=CompressionStrategy.PRUNE_MIDDLE)

    assert res.compressed_tokens <= 80
    assert res.tokens_saved > 0
    assert res.evicted_count > 0

    # Attention sink head preserved
    assert res.messages[0].content == "Critical System Invariants (Sink)"

    # Recent window tail preserved
    assert res.messages[-1].content == "Recent answer"
    assert res.messages[-2].content == "Recent question"


def test_summarize_middle_strategy(compressor: ModelContextCompressor):
    """Verify middle messages are consolidated into a condensed summary message."""
    msgs = [
        ContextMessage(role=MessageRole.SYSTEM.value, content="System Prompt"),
        ContextMessage(role=MessageRole.USER.value, content="Mid turn 1" * 10),
        ContextMessage(role=MessageRole.ASSISTANT.value, content="Mid response 1" * 10),
        ContextMessage(role=MessageRole.USER.value, content="Active turn"),
        ContextMessage(role=MessageRole.ASSISTANT.value, content="Active response"),
    ]

    res = compressor.compress_context(
        msgs,
        budget=65,
        strategy=CompressionStrategy.SUMMARIZE_MIDDLE,
    )

    # 1 sink + 1 summary + 2 recent = 4 messages
    assert res.compressed_message_count == 4
    assert res.summarized_count == 2
    assert "Condensed context summary" in res.messages[1].content
    assert res.messages[0].content == "System Prompt"
    assert res.messages[-1].content == "Active response"


def test_sliding_window_strategy(compressor: ModelContextCompressor):
    """Verify sliding window preserves sink head and newest turns."""
    msgs = [
        ContextMessage(role=MessageRole.SYSTEM.value, content="System Prompt"),
        ContextMessage(role=MessageRole.USER.value, content="Oldest turn" * 15),
        ContextMessage(role=MessageRole.USER.value, content="Newer turn"),
    ]

    res = compressor.compress_context(msgs, budget=65, strategy=CompressionStrategy.SLIDING_WINDOW)
    assert res.compressed_tokens <= 50
    assert res.messages[0].content == "System Prompt"
    assert res.messages[-1].content == "Newer turn"


def test_compression_metrics_tracking(compressor: ModelContextCompressor):
    """Verify compaction runs and cumulative token savings are logged in metrics."""
    msgs = [
        ContextMessage(role=MessageRole.SYSTEM.value, content="System Prompt"),
        ContextMessage(role=MessageRole.USER.value, content="Huge message " * 50),
        ContextMessage(role=MessageRole.USER.value, content="Latest"),
    ]
    compressor.compress_context(msgs, budget=30)
    metrics = compressor.get_compression_metrics()

    assert metrics["total_compaction_runs"] >= 1
    assert metrics["total_tokens_saved"] > 0


def test_global_singleton_compressor():
    """Verify singleton lifecycle for ModelContextCompressor."""
    c1 = get_context_compressor()
    c2 = get_context_compressor()
    assert c1 is c2

    c3 = reset_context_compressor()
    assert c3 is not c1
    assert get_context_compressor() is c3
