"""
Deterministic Verification Suite for Alice Google Cloud Loop.
Verifies:
1. GCP environment and Application Default Credentials authentication.
2. Token budget tracker and strict $100 spending limit enforcement.
3. Logic map, graph relations, and Sorites polysyllogism tables in kb/facts.db.
4. FTS5 fact database integration.
5. GFC textbook compliance via gfc linter in educate mode.
6. Authoritative whitelist reference door bindings (.gov, .edu, BIPM, NIST, IEEE).
7. Alice conversational language wrapper embodying Lewis Carroll's Alice.
8. Real end-to-end cognitive engine execution across deterministic and 2M context routes.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure documents root on sys.path
DOCUMENTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(DOCUMENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCUMENTS_ROOT))

from hydra_cli.alice_gcloud import (
    check_gcp_environment,
    TokenBudgetTracker,
    BudgetExceededError,
    synthesize_with_2m_context,
    DEFAULT_FAST_MODEL,
    DEFAULT_SYNTHESIS_MODEL,
)
from hydra_cli.alice_chat import AliceCognitiveAgent, alice_respond
from kb.facts import find_facts, get_fact, add_fact
from kb.logic_map import (
    get_node,
    get_relations,
    find_deduction_path,
    solve_sorites,
    seed_foundational_logic,
)


def test_gcp_environment_verification():
    """Verify GCP authentication, ADC availability, and SDK configuration."""
    status = check_gcp_environment()
    assert status["gcloud_installed"] is True, "gcloud CLI must be installed"
    assert status["adc_file_exists"] is True, "Application Default Credentials file must exist"
    assert status["sdk_available"] is True, "google-genai SDK must be available"
    assert status["client_ready"] is True, "GenAI client must be ready"
    assert status["active_project"] == "bastion-510306", "Active project must match bastion-510306"


def test_budget_tracker_enforcement():
    """Verify TokenBudgetTracker cost calculation, persistence, and budget enforcement."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_budget_file = Path(tmpdir) / "test_budget.json"
        tracker = TokenBudgetTracker(budget_path=tmp_budget_file, limit_usd=0.05)

        assert tracker.total_spent_usd == 0.0
        assert tracker.calls_count == 0

        # Record standard call
        cost = tracker.record_usage(
            model="gemini-2.5-flash",
            prompt_tokens=1000,
            candidate_tokens=500,
            thought_tokens=200,
            prompt_preview="Test call 1",
        )
        assert cost > 0.0
        assert tracker.total_tokens == 1500
        assert tracker.calls_count == 1
        assert tracker.total_spent_usd == cost

        # Verify persistence
        tracker2 = TokenBudgetTracker(budget_path=tmp_budget_file, limit_usd=0.05)
        assert tracker2.calls_count == 1
        assert tracker2.total_tokens == 1500

        # Verify budget violation halts execution
        with pytest.raises(BudgetExceededError):
            # Attempt to exceed $0.05 budget limit with 100M tokens
            tracker.check_permit(model="gemini-2.5-pro", estimated_prompt_tokens=50_000_000)


def test_logic_map_and_sorites_tables():
    """Verify foundational logic nodes, relations, and Sorites solvers in kb/facts.db."""
    # Ensure seeded
    seed_foundational_logic()

    # Verify Tractatus cardinal nodes
    t1 = get_node("Tractatus:1.0")
    assert t1 is not None, "Tractatus:1.0 node must exist"
    assert t1["tractatus_anchor"] == "1.0"
    assert "world" in t1["definition"].lower()

    # Verify Kantian category nodes
    k_unity = get_node("Kant:Category:Unity")
    assert k_unity is not None, "Kant:Category:Unity node must exist"
    assert k_unity["kantian_category"] == "Quantity:Unity"

    # Verify deduction path in graph
    path = find_deduction_path("Tractatus:1.0", "Concept:AtomicFact")
    assert path is not None, "Path from Tractatus:1.0 to Concept:AtomicFact must exist"
    assert len(path) >= 1

    # Verify Lewis Carroll sorites resolution
    carroll_res = solve_sorites("carroll_babies")
    assert carroll_res["valid"] is True, "Carroll babies sorites chain must be valid"
    assert carroll_res["steps_count"] == 3
    assert carroll_res["subject"] == "babies"
    assert "crocodiles" in carroll_res["final_predicate"]
    assert carroll_res["final_conclusion"] == "All babies are unable to manage crocodiles"

    # Verify Physics induction sorites resolution
    phys_res = solve_sorites("electromagnetic_induction_chain")
    assert phys_res["valid"] is True, "Physics induction sorites chain must be valid"
    assert phys_res["steps_count"] == 4
    assert phys_res["final_conclusion"] == "All relative motions of conductors are opposing secondary magnetic fields"


def test_facts_db_fts5_integration():
    """Verify high-speed SQLite FTS5 fact retrieval from kb/facts.db."""
    # Query Tractatus
    tractatus_facts = find_facts("Tractatus")
    assert len(tractatus_facts) > 0, "FTS5 must retrieve Tractatus facts"

    # Query Induction
    induction_facts = find_facts("induction")
    assert len(induction_facts) > 0, "FTS5 must retrieve induction facts"


def test_gfc_textbook_educate_mode():
    """Verify authored physics textbook conforms strictly to Greene Feynman Clarity in educate mode."""
    textbook_path = DOCUMENTS_ROOT / "kb" / "stacks" / "physics" / "textbook_electromagnetic_induction.md"
    assert textbook_path.exists(), "Foundational textbook must exist"

    # Execute gfc linter in educate mode
    cmd = [sys.executable, "-m", "gfc", "lint", str(textbook_path), "--mode", "educate"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"gfc lint failed: {proc.stderr or proc.stdout}"
    assert "clean" in proc.stdout.lower(), f"gfc lint output should be clean, got: {proc.stdout}"


def test_whitelist_references_bound():
    """Verify textbook and system bind authoritative whitelist doors (.gov, .edu, BIPM, NIST, IEEE)."""
    textbook_path = DOCUMENTS_ROOT / "kb" / "stacks" / "physics" / "textbook_electromagnetic_induction.md"
    content = textbook_path.read_text(encoding="utf-8")

    whitelist_doors = ["bipm.org", "nist.gov", "physics.nist.gov", "ieee.org", "codata.org"]
    for door in whitelist_doors:
        assert door in content, f"Textbook must bind authoritative whitelist door: {door}"


def test_alice_conversational_wrapper_deterministic():
    """Verify Alice conversational wrapper resolves deterministic queries with Lewis Carroll persona."""
    agent = AliceCognitiveAgent()

    # Arithmetic
    res_math = agent.respond("evaluate: 25 * 4")
    assert res_math["route"] == "DETERMINISTIC_EVAL"
    assert "100" in res_math["answer"]
    assert "Synthetic a priori" in res_math["answer"]
    assert res_math["telemetry"]["cost_usd"] == 0.0

    # Sorites Polysyllogism
    res_sorites = agent.respond("Can babies manage crocodiles?")
    assert res_sorites["route"] == "DETERMINISTIC_LOGIC"
    assert "Curiouser and curiouser" in res_sorites["answer"]
    assert "All babies are unable to manage crocodiles" in res_sorites["answer"]
    assert res_sorites["telemetry"]["cost_usd"] == 0.0


def test_alice_chat_end_to_end_gemini_synthesis():
    """Verify end-to-end Google Cloud Gemini 2M context synthesis with token and budget tracking."""
    agent = AliceCognitiveAgent(model=DEFAULT_FAST_MODEL)

    prompt = "Summarize the primary mechanism of electromagnetic induction and cite NIST and BIPM standards."
    res = agent.respond(prompt)

    assert res["route"] == "GEMINI_2M_SYNTHESIS"
    assert len(res["answer"]) > 50
    assert "telemetry" in res
    tel = res["telemetry"]
    assert tel["prompt_tokens"] > 0
    assert tel["candidate_tokens"] > 0
    assert tel["cost_usd"] > 0.0
    assert tel["budget_remaining_usd"] <= 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])