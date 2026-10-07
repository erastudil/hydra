from unittest.mock import patch

from hydra_cli.agent import (
    HydraReplCompleter,
    MODEL_PICKER,
    resolve_model_choice,
    run_agent_loop,
    run_interactive_agent,
)
from hydra_cli.alice_gate import GateDecision, consult, load_skills, stack_report


def _boom(_prompt):
    raise AssertionError("evaluator ran")


def test_skill_index_loads_written_cards():
    skills = load_skills()
    ids = {skill["id"] for skill in skills}
    assert {"hydra", "alice", "easylm", "code", "containers", "discovery"} <= ids


def test_stack_report_names_present_hydra():
    text = stack_report()
    assert "hydra : present" in text
    assert r"Documents\hydra" in text


def test_vague_prompt_asks_without_alice():
    decision = consult("fix it", summon_alias="glm 5.3 flash", evaluator=_boom)
    assert decision.action == "ask"
    assert decision.route == "VAGUENESS_DETECTED"
    assert "clarification required" in decision.text


def test_vague_prompt_with_referent_summons():
    def evaluator(_prompt):
        return {"route": "SYNTHETIC_LLM_ROUTING", "answer": "gap"}

    decision = consult(
        "fix it",
        summon_alias="opus 5.5",
        has_referent=True,
        prior=("repair parser.py", "the parser still fails"),
        evaluator=evaluator,
    )
    assert decision.action == "summon"
    assert "repair parser.py" in decision.discovery
    assert "summon : opus 5.5" in decision.discovery


def test_question_hits_one_skill_and_skips_alice():
    decision = consult("where is hydra", summon_alias="glm 5.3 flash", evaluator=_boom)
    assert decision.action == "local"
    assert decision.route == "SKILL_INDEX"
    assert "hydra : present" in decision.text


def test_tied_skills_ask_for_one_subject():
    decision = consult("what is docker devops", summon_alias="glm 5.3 flash", evaluator=_boom)
    assert decision.action == "ask"
    assert decision.route == "DISAMBIGUATION"
    assert "docker" in decision.text or "containers" in decision.text


def test_typo_normalization_reaches_the_skill():
    decision = consult("waht is hydra", summon_alias="glm 5.3 flash", evaluator=_boom)
    assert decision.action == "local"
    assert "waht -> what" in decision.text


def test_deterministic_alice_route_stays_local():
    def evaluator(_prompt):
        return {"route": "DETERMINISTIC_EVAL", "answer": "4", "epistemicModality": "a priori"}

    decision = consult("2 + 2", summon_alias="glm 5.3 flash", evaluator=evaluator)
    assert decision.action == "local"
    assert "4" in decision.text
    assert decision.route == "DETERMINISTIC_EVAL"


def test_synthetic_route_summons_without_a_provider():
    def evaluator(_prompt):
        return {"route": "SYNTHETIC_LLM_ROUTING", "answer": "no local proof"}

    with patch("hydra_cli.providers.complete") as complete:
        decision = consult("refactor the parser module", summon_alias="glm 5.3 flash", evaluator=evaluator)
    assert decision.action == "summon"
    assert "plan :" in decision.discovery
    complete.assert_not_called()


def test_epistemic_gap_asks():
    def evaluator(_prompt):
        return {"route": "EPISTEMIC_GAP", "answer": "which file holds the failing test"}

    decision = consult("explain the failure", summon_alias="glm 5.3 flash", evaluator=evaluator)
    assert decision.action == "ask"
    assert "which file" in decision.text


def test_model_choice_accepts_picker_numbers_and_refuses_unknown():
    assert resolve_model_choice("1") == "alice"
    assert resolve_model_choice("alise") == "alice"
    assert resolve_model_choice("opus 5.5") == "opus 5.5"
    assert resolve_model_choice("nope") is None
    assert resolve_model_choice("99") is None
    assert MODEL_PICKER[0] == "alice"


def test_model_completer_lists_the_picker_first():
    completer = HydraReplCompleter()
    empty = completer.get_candidates("", "/model ")
    assert empty[0] == "alice"
    assert empty == [name for name in MODEL_PICKER]


def test_model_picker_new_and_unknown_alias(capsys, tmp_path):
    inputs = iter(["/model", "/model nope", "/model 2", "/new", "/stack", "/skills", "/exit"])
    with patch("builtins.input", lambda _: next(inputs)):
        assert run_interactive_agent(cwd=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "model picker :" in out
    assert "local brain, summons" in out
    assert "Unknown alias 'nope'" in out
    assert "Active model set to: glm 5.3 flash" in out
    assert "Request model: glm-5.3-flash" in out
    assert "New session history" in out
    assert "hydra : present" in out
    assert "skills :" in out


def test_alice_local_answer_skips_the_agent_loop(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    decision = GateDecision(action="local", text="four\n\nroute : DETERMINISTIC_EVAL", route="DETERMINISTIC_EVAL")
    inputs = iter(["/model alice", "what is 2 + 2", "/route", "/exit"])
    with patch("builtins.input", lambda _: next(inputs)), \
         patch("hydra_cli.alice_gate.consult", return_value=decision), \
         patch("hydra_cli.agent.run_agent_loop") as loop:
        assert run_interactive_agent(cwd=str(tmp_path)) == 0
    loop.assert_not_called()
    out = capsys.readouterr().out
    assert "four" in out
    assert "Last gate: local DETERMINISTIC_EVAL" in out


def test_alice_summon_posts_the_summon_alias(capsys, tmp_path):
    decision = GateDecision(
        action="summon",
        text="",
        route="SYNTHETIC_LLM_ROUTING",
        discovery="plan : read the file",
    )
    inputs = iter(["/model alice", "/summon opus 5.5", "refactor the parser module", "/exit"])
    with patch("builtins.input", lambda _: next(inputs)), \
         patch("hydra_cli.alice_gate.consult", return_value=decision), \
         patch("hydra_cli.agent.run_agent_loop", return_value="patched") as loop:
        assert run_interactive_agent(cwd=str(tmp_path)) == 0
    assert loop.call_args.kwargs["alias"] == "opus 5.5"
    assert "plan : read the file" in loop.call_args.kwargs["system_prompt"]
    out = capsys.readouterr().out
    assert "Summon model set to: opus 5.5" in out


def test_sliding_strategy_includes_prior_turns(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    from hydra_cli.context import SessionContextLedger

    ledger = SessionContextLedger(session_id="pytest_sliding")
    ledger.append_turn("user", "first prompt alpha_marker")
    ledger.append_turn("assistant", "first answer beta_marker")
    mock_resp = {"choices": [{"message": {"role": "assistant", "content": "next ok", "tool_calls": None}}]}
    provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    with patch("hydra_cli.agent.get_frontier_providers", return_value=provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp) as fetch, \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop(
            alias="sonnet 5.5",
            prompt="continue the parser",
            cwd=str(tmp_path),
            ledger=ledger,
            strategy="sliding",
        )
    user = fetch.call_args[0][2]["messages"][1]["content"]
    assert user.index("[PRIOR TURNS]") < user.index("continue the parser")
    assert "alpha_marker" in user
    assert "beta_marker" in user
