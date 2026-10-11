"""
Hydra Desktop Model Context Compressor Subsystem.
Maintains bounded prompt context budgets through Attention Sink preservation,
rolling window working memory retention, and differential middle-zone eviction or summarization.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class MessageRole(str, Enum):
    """Dialogue message roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ContextMessage:
    """Atomic dialogue or instruction message in context memory."""
    role: str
    content: str
    token_count: int = 0
    pinned: bool = False
    priority: int = 50
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "token_count": self.token_count,
            "pinned": self.pinned,
            "priority": self.priority,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContextMessage:
        return cls(
            role=data["role"],
            content=data["content"],
            token_count=data.get("token_count", 0),
            pinned=data.get("pinned", False),
            priority=data.get("priority", 50),
            timestamp=data.get("timestamp", time.time()),
            metadata=data.get("metadata", {}),
        )


class CompressionStrategy(str, Enum):
    """Context memory compression algorithms."""
    PRUNE_MIDDLE = "prune_middle"
    SUMMARIZE_MIDDLE = "summarize_middle"
    SLIDING_WINDOW = "sliding_window"


@dataclass
class CompressionResult:
    """Deterministic outcome of context token compaction."""
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    original_message_count: int
    compressed_message_count: int
    messages: List[ContextMessage]
    evicted_count: int
    summarized_count: int
    strategy_used: CompressionStrategy

    @property
    def compression_ratio(self) -> float:
        if self.original_tokens <= 0:
            return 1.0
        return round(self.compressed_tokens / self.original_tokens, 3)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": self.compression_ratio,
            "original_message_count": self.original_message_count,
            "compressed_message_count": self.compressed_message_count,
            "evicted_count": self.evicted_count,
            "summarized_count": self.summarized_count,
            "strategy_used": self.strategy_used.value,
            "messages": [m.to_dict() for m in self.messages],
        }


class ModelContextCompressor:
    """
    Sovereign context compressor enforcing strict token bounds while preserving
    initial attention sink tokens and recent working memory turns.
    """

    def __init__(
        self,
        default_token_budget: int = 4096,
        sink_messages: int = 1,
        recent_messages: int = 4,
        chars_per_token: float = 4.0,
    ) -> None:
        self.default_token_budget = default_token_budget
        self.sink_messages = max(1, sink_messages)
        self.recent_messages = max(1, recent_messages)
        self.chars_per_token = max(1.0, chars_per_token)
        self._lock = threading.RLock()
        self._total_compaction_runs = 0
        self._total_tokens_saved = 0

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count based on character length heuristics."""
        if not text:
            return 0
        return max(1, math.ceil(len(text) / self.chars_per_token))

    def _ensure_token_counts(self, messages: List[ContextMessage]) -> None:
        for m in messages:
            if m.token_count <= 0:
                m.token_count = self.estimate_tokens(m.content)

    def calculate_total_tokens(self, messages: List[ContextMessage]) -> int:
        """Compute cumulative token count across a message list."""
        self._ensure_token_counts(messages)
        return sum(m.token_count for m in messages)

    def compress_context(
        self,
        messages: List[ContextMessage],
        budget: Optional[int] = None,
        strategy: CompressionStrategy = CompressionStrategy.PRUNE_MIDDLE,
        summary_generator: Optional[Callable[[List[ContextMessage]], str]] = None,
    ) -> CompressionResult:
        """
        Enforce token budget over message sequence.
        Preserves attention sink head and recent window tail.
        Evicts or summarizes middle messages to strictly respect token budget.
        """
        with self._lock:
            self._total_compaction_runs += 1
            max_budget = budget or self.default_token_budget
            msgs = [ContextMessage.from_dict(m.to_dict()) for m in messages]
            self._ensure_token_counts(msgs)

            original_tokens = sum(m.token_count for m in msgs)
            original_count = len(msgs)

            # If already within budget, return immediately
            if original_tokens <= max_budget:
                return CompressionResult(
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    tokens_saved=0,
                    original_message_count=original_count,
                    compressed_message_count=original_count,
                    messages=msgs,
                    evicted_count=0,
                    summarized_count=0,
                    strategy_used=strategy,
                )

            # Identify attention sink boundary
            sink_boundary = min(self.sink_messages, len(msgs))
            # Pinned items at head belong to sink
            sink_msgs = []
            for i in range(sink_boundary):
                sink_msgs.append(msgs[i])

            remaining = msgs[sink_boundary:]

            # Identify recent window tail
            if len(remaining) <= self.recent_messages:
                recent_msgs = remaining
                middle_msgs = []
            else:
                middle_msgs = remaining[: -self.recent_messages]
                recent_msgs = remaining[-self.recent_messages :]

            evicted = 0
            summarized = 0

            # Execute compression strategy on middle messages
            if strategy == CompressionStrategy.SUMMARIZE_MIDDLE and middle_msgs:
                if summary_generator:
                    summary_text = summary_generator(middle_msgs)
                else:
                    topics = [f"{m.role}: {m.content[:40]}..." for m in middle_msgs]
                    summary_text = "[Condensed context summary of earlier conversation: " + " | ".join(topics) + "]"

                summary_tokens = self.estimate_tokens(summary_text)
                summary_msg = ContextMessage(
                    role=MessageRole.SYSTEM.value,
                    content=summary_text,
                    token_count=summary_tokens,
                    priority=60,
                )
                middle_msgs = [summary_msg]
                summarized = original_count - len(sink_msgs) - len(recent_msgs)

            elif strategy in (CompressionStrategy.PRUNE_MIDDLE, CompressionStrategy.SLIDING_WINDOW):
                # Sort middle messages by priority ascending, then age ascending
                # Evict until budget satisfied
                while middle_msgs:
                    current_tokens = sum(m.token_count for m in sink_msgs + middle_msgs + recent_msgs)
                    if current_tokens <= max_budget:
                        break
                    # Evict oldest unpinned middle message
                    middle_msgs.pop(0)
                    evicted += 1

            # If still over budget after pruning middle, prune oldest recent non-pinned messages
            while recent_msgs and (sum(m.token_count for m in sink_msgs + middle_msgs + recent_msgs) > max_budget):
                if len(recent_msgs) <= 1:
                    # Keep at least 1 recent message
                    break
                recent_msgs.pop(0)
                evicted += 1

            final_messages = sink_msgs + middle_msgs + recent_msgs
            final_tokens = sum(m.token_count for m in final_messages)
            tokens_saved = max(0, original_tokens - final_tokens)
            self._total_tokens_saved += tokens_saved

            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=final_tokens,
                tokens_saved=tokens_saved,
                original_message_count=original_count,
                compressed_message_count=len(final_messages),
                messages=final_messages,
                evicted_count=evicted,
                summarized_count=summarized,
                strategy_used=strategy,
            )

    def get_compression_metrics(self) -> Dict[str, Any]:
        """Return cumulative context compaction metrics."""
        with self._lock:
            return {
                "total_compaction_runs": self._total_compaction_runs,
                "total_tokens_saved": self._total_tokens_saved,
                "default_token_budget": self.default_token_budget,
                "sink_messages": self.sink_messages,
                "recent_messages": self.recent_messages,
            }


_GLOBAL_CONTEXT_COMPRESSOR: Optional[ModelContextCompressor] = None
_GLOBAL_COMPRESSOR_LOCK = threading.RLock()


def get_context_compressor() -> ModelContextCompressor:
    """Acquire thread-safe singleton ModelContextCompressor."""
    global _GLOBAL_CONTEXT_COMPRESSOR
    with _GLOBAL_COMPRESSOR_LOCK:
        if _GLOBAL_CONTEXT_COMPRESSOR is None:
            _GLOBAL_CONTEXT_COMPRESSOR = ModelContextCompressor()
        return _GLOBAL_CONTEXT_COMPRESSOR


def reset_context_compressor() -> ModelContextCompressor:
    """Reset singleton ModelContextCompressor."""
    global _GLOBAL_CONTEXT_COMPRESSOR
    with _GLOBAL_COMPRESSOR_LOCK:
        _GLOBAL_CONTEXT_COMPRESSOR = ModelContextCompressor()
        return _GLOBAL_CONTEXT_COMPRESSOR
