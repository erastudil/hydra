"""
Hydra — Sovereign Multi-Headed AI Summoning CLI
"""

from hydra_cli._version import __version__
__author__ = "erastudil"

from hydra_cli.agent import (
    HierarchicalScratchpad,
    SessionCheckpointer,
    run_agent_loop,
)
from hydra_cli.agent_runners import (
    hermes_available,
    pi_available,
    run_hermes,
    run_pi,
)
from hydra_cli.config import (
    IMMUTABLE_AGENT_INVARIANTS,
    build_cached_system_prompt,
)
from hydra_cli.mcp import McpSubprocessClient
from hydra_cli.mcp_registry import McpRegistry
from hydra_cli.providers import complete
from hydra_cli.sandbox import (
    CommandInspector,
    EnvironmentScrubber,
    SandboxConfig,
    SandboxExecutionResult,
    SandboxRunner,
)
from hydra_cli.serve import run_server
from hydra_cli.speculative import (
    SpeculativeEngine,
    SpeculativeResult,
    speculative_complete,
)
from hydra_cli.ui import print_banner
from hydra_cli.voice import (
    AudioChunk,
    AudioStreamBuffer,
    KokoroTTSClient,
    LatencyBreakdown,
    SileroVADDetector,
    VADFrameResult,
    VADState,
    VoicePipelineBenchmark,
    execute_voice_command,
)

__all__ = [
    "complete",
    "run_server",
    "run_agent_loop",
    "HierarchicalScratchpad",
    "SessionCheckpointer",
    "IMMUTABLE_AGENT_INVARIANTS",
    "build_cached_system_prompt",
    "SandboxConfig",
    "CommandInspector",
    "EnvironmentScrubber",
    "SandboxExecutionResult",
    "SandboxRunner",
    "run_hermes",
    "run_pi",
    "hermes_available",
    "pi_available",
    "McpSubprocessClient",
    "McpRegistry",
    "print_banner",
    "SpeculativeEngine",
    "SpeculativeResult",
    "speculative_complete",
    "SileroVADDetector",
    "VADState",
    "VADFrameResult",
    "AudioStreamBuffer",
    "KokoroTTSClient",
    "AudioChunk",
    "VoicePipelineBenchmark",
    "LatencyBreakdown",
    "execute_voice_command",
    "__version__",
]
