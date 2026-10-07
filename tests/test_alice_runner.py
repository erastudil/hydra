"""
Unit tests for Alice agent runner integration in Hydra CLI.
Verifies Alice cognitive engine, philosophical canon evaluation, orchestrator, and worker.
"""

from unittest.mock import MagicMock, patch
import pytest

from hydra_cli.agent_runners import (
    get_agent_status,
    run_alice,
    alice_available,
    find_alice_core,
)
from hydra_cli.alice_runner import (
    AliceOrchestrator,
    AliceWorker,
    evaluate_with_alice,
)
from hydra_cli.swarm import run_single_head


def test_alice_available_and_path():
    """Verify Alice availability detection and core path discovery."""
    available = alice_available()
    assert isinstance(available, bool)
    path = find_alice_core()
    if available:
        assert path is not None
        assert "alice_core.js" in path


def test_get_agent_status_includes_alice():
    """get_agent_status returns availability and capabilities for Alice."""
    status = get_agent_status()
    assert "alice" in status
    assert "capabilities" in status["alice"]
    assert "cognitive-grammar" in status["alice"]["capabilities"]
    assert "philosophical-canon" in status["alice"]["capabilities"]
    assert "orchestrator" in status["alice"]["capabilities"]
    assert "baseline-worker" in status["alice"]["capabilities"]


def test_evaluate_with_alice_deterministic_math():
    """evaluate_with_alice returns verified proof on deterministic math."""
    if not alice_available():
        pytest.skip("Alice core or Node.js not available in current environment")

    res = evaluate_with_alice("evaluate: 2^10 + 42")
    assert res is not None
    assert res.get("route") == "DETERMINISTIC_EVAL"
    assert res.get("epistemicModality") == "[VERIFIED_PROOF]"
    assert "1066" in res.get("answer", "")


def _mind_on() -> bool:
    from hydra_cli import alice_mind
    return alice_mind.mind_available()


def test_evaluate_with_alice_philosophical_canon():
    """evaluate_with_alice answers philosophical queries from the stacks with citations; core path keeps PHILOSOPHICAL_CANON."""
    if not alice_available():
        pytest.skip("Alice core or Node.js not available in current environment")

    res = evaluate_with_alice("how does socrates navigate uncertainty?")
    assert res is not None
    assert "Socrates" in res.get("answer", "")
    assert "progenStream" in res
    if res.get("engine") == "alice-mind":
        assert res.get("route", "").startswith("CITED_")
        assert res.get("citations")
        assert all(c.get("stack") and c.get("locator") for c in res["citations"])
    else:
        assert res.get("route") == "PHILOSOPHICAL_CANON"
        assert res.get("epistemicModality") == "[VERIFIED_PROOF]"
        assert "socrates" in res["progenStream"]


def test_evaluate_with_core_engine_override(monkeypatch):
    """ALICE_ENGINE=core bypasses the mind and reaches alice_core.js directly."""
    from hydra_cli.alice_runner import evaluate_with_core, find_node_binary
    if not (find_node_binary() and find_alice_core()):
        pytest.skip("Alice core or Node.js not available in current environment")
    monkeypatch.setenv("ALICE_ENGINE", "core")
    res = evaluate_with_alice("evaluate: 2^10 + 42")
    assert res is not None
    assert res.get("engine") != "alice-mind"
    assert "1066" in res.get("answer", "")
    assert evaluate_with_core("evaluate: 10 + 5") is not None


def test_run_alice_verified_proof():
    """run_alice returns formatted answer with epistemic modality badge."""
    if not alice_available():
        pytest.skip("Alice core or Node.js not available in current environment")

    output = run_alice("what is looking glass?")
    assert output is not None
    if _mind_on():
        assert output.startswith("[CITED]")
        assert "Looking glass" in output
        assert "Sources:" in output and "[1]" in output
    else:
        assert "[VERBATIM_RECALL]" in output
        assert "LOOKING GLASS" in output


def test_mind_gap_escalates_with_label(monkeypatch):
    """A mind gap reaches the fallback model and the output carries the uncited label."""
    if not _mind_on():
        pytest.skip("alice mind not available")
    calls = {}

    def fake_complete(prompt, model, system_prompt, max_tokens):
        calls["system"] = system_prompt
        return "model text"

    monkeypatch.setattr("hydra_cli.providers.complete", fake_complete)
    output = run_alice("what is a quaternion flux capacitor of the zorblax membrane?")
    assert output.startswith("[SYNTHETIC_INFERENCE")
    assert "uncited" in output
    assert "no stack citation" in calls["system"]


def test_mind_gate_cited_answer_stays_local():
    """consult keeps a cited mind answer local and never summons."""
    from hydra_cli.alice_gate import consult
    res = {"engine": "alice-mind", "route": "CITED_DEFINITION", "mindRoute": "CITED_DEFINITION", "epistemicModality": "[CITED]", "answer": "x [1]", "escalate": False}
    decision = consult("what is aporia", summon_alias="glm 5.3 flash", evaluator=lambda p: res)
    assert decision.action == "local"
    gap = dict(res, route="EPISTEMIC_GAP", mindRoute="EPISTEMIC_GAP", escalate=True, answer="I don't know.")
    decision = consult("what is zorblax", summon_alias="glm 5.3 flash", evaluator=lambda p: gap)
    assert decision.action == "summon"
    assert "alice mind route : EPISTEMIC_GAP" in decision.discovery


def test_alice_orchestrator():
    """AliceOrchestrator decomposes task and formulates canonical Progen plan."""
    orchestrator = AliceOrchestrator()
    plan = orchestrator.plan_task("coordinate bridge between formal logic and empirical perception")
    assert "task" in plan
    assert "route" in plan
    assert "progen_plan" in plan
    assert "intention :" in plan["progen_plan"]
    assert "requirement :" in plan["progen_plan"]
    assert "course of action :" in plan["progen_plan"]


def test_alice_worker_invariant_verification():
    """AliceWorker verifies Progen invariants and reports zero copula compliance."""
    if not alice_available():
        pytest.skip("Alice core or Node.js not available in current environment")

    worker = AliceWorker()
    res = worker.execute_and_verify("evaluate: 10 + 5")
    assert res["valid_progen"] is True
    assert res["verified"] is True
    assert res["exit_code"] == 0


def test_run_single_head_with_alice_runner():
    """run_single_head executes head with runner='alice'."""
    with patch("hydra_cli.alice_runner.run_alice") as mock_alice:
        mock_alice.return_value = "[VERIFIED_PROOF] Alice cognitive consensus reached."
        cfg = {"title": "Alice Cognitive Head", "runner": "alice"}
        res = run_single_head("alice:cognitive", cfg, "Test cognitive reasoning", provider=[])
        assert res.role == "alice:cognitive"
        assert res.model == "alice-core"
        assert res.provider == "alice"
        assert "Alice cognitive consensus reached." in res.content
