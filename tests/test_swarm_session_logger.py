"""
Unit and integration test suite for Wave 22 Swarm Session Logger and Model Routing.
Verifies Mimo 2.6 Pro daily driver routing, CheaperInference defaults, fan-out swarm configuration,
SwarmSessionLogger human writeups, and git/vercel/local native tool capabilities.
Adheres to AGENTS.md genome invariants: zero copula P018, deterministic exit code 0.
"""

import os
import shutil
import tempfile
from typing import Any, Dict

import pytest

from hydra_cli.config import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_FANOUT_SWARM_ALIASES,
    DEFAULT_MODEL,
    DEFAULT_ORCHESTRATOR_MODEL,
    IMMUTABLE_AGENT_INVARIANTS,
    get_context_window,
    model_providers,
    resolve_model,
    resolve_route,
)
from hydra_cli.native_tools import AstDocstringLinter, NativeToolRegistry
from hydra_cli.providers import adapt_model_for_url
from hydra_cli.repl import DEFAULT_MODEL_ALIASES
from hydra_cli.session_logger import (
    SwarmHeadDispatch,
    SwarmHeadSummary,
    SwarmSessionLogger,
    SwarmSessionSummary,
    SwarmToolExecution,
    get_swarm_session_logger,
    reset_swarm_session_logger,
)


def test_mimo_26_model_routing_and_cheaperinference_defaults():
    """Verify Mimo 2.6 Pro daily driver and CheaperInference provider precedence."""
    # Default daily driver resolution
    assert DEFAULT_MODEL == "mimo 2.6 pro"
    assert DEFAULT_ORCHESTRATOR_MODEL == "mimo 2.6 pro"
    assert DEFAULT_AGENT_MODEL == "mimo 2.6 pro"

    # Alias resolution
    assert resolve_model("mimo 2.6 pro") == "mimo-2.6-pro"
    assert resolve_model("mimo-2.6-pro") == "mimo-2.6-pro"
    assert resolve_model("mimo") == "mimo-2.6-pro"
    assert resolve_model("mimo 2.6") == "mimo-2.6-pro"
    assert resolve_model("mimo pro") == "mimo-2.6-pro"
    assert resolve_model("daily driver") == "mimo-2.6-pro"

    assert resolve_model("mimo 2.6 flash") == "mimo-2.6-flash"
    assert resolve_model("mimo-2.6-flash") == "mimo-2.6-flash"
    assert resolve_model("mimo flash") == "mimo-2.6-flash"

    # CheaperInference default provider routing
    provs_pro = model_providers("mimo 2.6 pro")
    assert provs_pro[0] == "cheaperinference"
    assert "openrouter" in provs_pro

    provs_flash = model_providers("mimo 2.6 flash")
    assert provs_flash[0] == "cheaperinference"
    assert "openrouter" in provs_flash

    # REPL alias expansions
    assert DEFAULT_MODEL_ALIASES["mimo"] == "mimo 2.6 pro"
    assert DEFAULT_MODEL_ALIASES["mimo pro"] == "mimo 2.6 pro"
    assert DEFAULT_MODEL_ALIASES["mimo flash"] == "mimo 2.6 flash"
    assert DEFAULT_MODEL_ALIASES["daily driver"] == "mimo 2.6 pro"


def test_swarm_fanout_aliases_and_adaptation():
    """Verify default swarm fan-out aliases and provider URL adaptation."""
    expected_trio = ["glm 5.3 flash", "deepseek 4.1 flash", "mimo 2.6 flash"]
    assert set(DEFAULT_FANOUT_SWARM_ALIASES) == set(expected_trio)

    ci_url = "https://api.cheaperinference.com/v1/chat/completions"
    or_url = "https://openrouter.ai/api/v1/chat/completions"

    assert adapt_model_for_url(ci_url, "mimo-2.6-pro") == "mimo-2.6-pro"
    assert adapt_model_for_url(ci_url, "mimo-2.6-flash") == "mimo-2.6-flash"
    assert adapt_model_for_url(ci_url, "xiaomi/mimo-2.6-pro") == "mimo-2.6-pro"
    assert adapt_model_for_url(ci_url, "xiaomi/mimo-2.6-flash") == "mimo-2.6-flash"

    assert adapt_model_for_url(or_url, "mimo-2.6-pro") == "xiaomi/mimo-2.6-pro"
    assert adapt_model_for_url(or_url, "mimo-2.6-flash") == "xiaomi/mimo-2.6-flash"
    assert adapt_model_for_url(or_url, "glm-5.3-flash") == "z-ai/glm-5.3-flash"
    assert adapt_model_for_url(or_url, "deepseek-4.1-flash") == "deepseek/deepseek-4.1-flash"


def test_context_window_sizing_and_ai_ml_invariant():
    """Verify 1M context sizing for cloud models and AI/ML search invariant."""
    # 1M context verification (1,048,576 tokens)
    assert DEFAULT_CONTEXT_WINDOW == 1048576
    assert get_context_window("mimo 2.6 pro") == 1048576
    assert get_context_window("mimo 2.6 flash") == 1048576
    assert get_context_window("deepseek 4.1 flash") == 1048576
    assert get_context_window("glm 5.3 flash") == 1048576
    assert get_context_window("anthropic/claude-opus-5.5") == 1048576
    assert get_context_window("openai/gpt-6.1-sol") == 1048576

    # Gemma model expanded context (eliminate 8k cap)
    assert get_context_window("gemma-e4b") == 131072
    assert get_context_window("google/gemma-4-26b-a4b-it") == 131072

    # AI/ML research invariant verification
    assert "AI/ML Research Invariant" in IMMUTABLE_AGENT_INVARIANTS
    assert "search web first" in IMMUTABLE_AGENT_INVARIANTS


def test_swarm_session_logger_lifecycle_and_writeup():
    """Verify full lifecycle of SwarmSessionLogger and readable markdown writeup."""
    temp_dir = tempfile.mkdtemp(prefix="hydra_test_session_")
    try:
        logger = SwarmSessionLogger(sessions_dir=temp_dir)
        session_id = logger.start_session(task="Optimize database indexing", swarm_name="test_swarm")
        assert session_id.startswith("swarm_")

        # Record dispatches
        logger.record_dispatch(session_id, "architect", "GLM Architect", "glm-5.3-flash", "cheaperinference", "arch_01")
        logger.record_dispatch(session_id, "coder", "DeepSeek Coder", "deepseek-4.1-flash", "cheaperinference", "code_01")
        logger.record_dispatch(session_id, "auditor", "Mimo Auditor", "mimo-2.6-flash", "cheaperinference", "audit_01")

        # Record tool calls
        logger.record_tool_call(
            session_id=session_id,
            role="coder",
            tool_name="read_file",
            arguments={"path": "schema.sql"},
            result="CREATE TABLE users (id INT PRIMARY KEY);",
            duration_ms=14.2,
        )
        logger.record_tool_call(
            session_id=session_id,
            role="coder",
            tool_name="edit_file",
            arguments={"path": "schema.sql", "patch": "+INDEX (email)"},
            result="Applied patch to schema.sql",
            duration_ms=28.5,
        )

        # Record completions
        logger.record_head_completion(
            session_id=session_id,
            role="architect",
            title="GLM Architect",
            model="glm-5.3-flash",
            content="Architectural review recommends compound index on users(email, created_at).",
            duration_sec=1.45,
            provider="cheaperinference",
            cost=0.0012,
        )
        logger.record_head_completion(
            session_id=session_id,
            role="coder",
            title="DeepSeek Coder",
            model="deepseek-4.1-flash",
            content="Added compound index and verified query execution plan.",
            duration_sec=2.10,
            provider="cheaperinference",
            cost=0.0018,
        )
        logger.record_head_completion(
            session_id=session_id,
            role="auditor",
            title="Mimo Auditor",
            model="mimo-2.6-flash",
            content="Security and performance audit passing. Zero locks detected.",
            duration_sec=1.20,
            provider="cheaperinference",
            cost=0.0009,
        )

        # Record synthesis
        logger.record_synthesis(
            session_id=session_id,
            synthesis_content="Consensus roadmap: 1. Deploy index migration. 2. Verify with EXPLAIN ANALYZE.",
            duration_sec=0.85,
        )

        # Finalize and write to disk
        summary = logger.finalize_session(session_id=session_id, exit_status=0)
        assert summary.exit_status == 0
        assert len(summary.heads) == 4
        assert summary.total_cost_usd > 0.0

        md_path, json_path = logger.write_summary_to_disk(session_id=session_id)
        assert os.path.isfile(md_path)
        assert os.path.isfile(json_path)

        # Read back markdown writeup
        with open(md_path, "r", encoding="utf-8") as f:
            writeup = f.read()

        assert "# Hydra Swarm Session Summary" in writeup
        assert "Optimize database indexing" in writeup
        assert "GLM Architect" in writeup
        assert "DeepSeek Coder" in writeup
        assert "Mimo Auditor" in writeup
        assert "read_file" in writeup
        assert "edit_file" in writeup
        assert "Consensus Synthesis Roadmap" in writeup
        assert "PASSING (EXIT CODE 0)" in writeup

        # Verify load_session_summary
        loaded = logger.load_session_summary(session_id)
        assert loaded is not None
        assert loaded["session_id"] == session_id
        assert loaded["exit_status"] == 0

        # Verify list_all_session_summaries
        all_summaries = logger.list_all_session_summaries()
        assert len(all_summaries) == 1
        assert all_summaries[0]["session_id"] == session_id

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_native_tool_implementations():
    """Verify git, vercel deployment triggers, and local machine control tools."""
    temp_workspace = tempfile.mkdtemp(prefix="hydra_test_workspace_")
    try:
        registry = NativeToolRegistry(cwd=temp_workspace)

        # Verify tool registration
        assert registry.has_tool("git_commit_and_push")
        assert registry.has_tool("trigger_vercel_deploy")
        assert registry.has_tool("inspect_environment")
        assert registry.has_tool("execute_bash")

        # 1. Environment inspection
        env_info = registry.inspect_environment()
        assert env_info["isError"] is False
        assert "os" in env_info
        assert "python_version" in env_info
        assert "tools_available" in env_info
        assert env_info["working_directory"] == temp_workspace

        # 2. Local machine control / bash command execution
        bash_res = registry.execute_bash("echo 'sovereign hydra'")
        assert bash_res.get("isError") is not True
        assert "sovereign hydra" in bash_res.get("stdout", "")

        # 3. Git commit & push tool verification
        # Clean directory test
        clean_res = registry.git_commit_and_push(commit_message="Test clean commit", dry_run=True)
        # Outside a git repo or in clean dir, reports gracefully
        assert isinstance(clean_res, dict)
        assert "isError" in clean_res

        # 4. Vercel deployment trigger verification (dry run)
        vercel_dry = registry.trigger_vercel_deploy(project_dir=temp_workspace, prod=False, dry_run=True)
        assert vercel_dry["isError"] is False
        assert vercel_dry["status"] == "DRY_RUN"
        assert vercel_dry["target"] == "preview"

        vercel_prod_dry = registry.trigger_vercel_deploy(project_dir=temp_workspace, prod=True, dry_run=True)
        assert vercel_prod_dry["isError"] is False
        assert vercel_prod_dry["status"] == "DRY_RUN"
        assert vercel_prod_dry["target"] == "production"

    finally:
        shutil.rmtree(temp_workspace, ignore_errors=True)


def test_docstring_p018_linter_on_new_modules():
    """Verify AST docstrings and zero copula P018 invariants."""
    linter = AstDocstringLinter(check_modules=True, require_zero_copula=True)

    for rel_path in [
        "hydra_cli/session_logger.py",
        "desktop/swarm_session_logger.py",
        "tests/test_swarm_session_logger.py",
    ]:
        full_path = os.path.abspath(rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            src = f.read()
        res = linter.analyze_source(src)
        violations = res.get("violations", [])
        assert len(violations) == 0, f"Docstring violations in {rel_path}: {violations}"
