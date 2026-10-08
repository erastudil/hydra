from unittest.mock import MagicMock, patch
import json
import os
import pytest

from hydra_cli.agent import (
    detect_project_rules,
    dialect_instruction,
    parse_heat,
    parse_window,
    progen_paths,
    run_agent_loop,
    run_interactive_agent,
)
from hydra_cli.router import route_command


def test_native_tool_execution_in_agent_loop(tmp_path):
    # Setup test file in tmp_path
    test_file = tmp_path / "hello.py"
    test_file.write_text("print('sovereign hydra')\n", encoding="utf-8")

    mock_provider = [{"url": "https://api.openai.com/v1/chat/completions", "headers": {"Authorization": "Bearer test"}}]
    
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_native_read",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "hello.py"}),
                            },
                        }
                    ],
                }
            }
        ]
    }
    
    turn2_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "File contains: sovereign hydra",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        
        result = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Read hello.py and explain",
            cwd=str(tmp_path),
            enable_native_tools=True,
        )
        assert "sovereign hydra" in result


def test_native_write_and_edit_in_agent_loop(tmp_path):
    mock_provider = [{"url": "https://api.openai.com/v1/chat/completions", "headers": {"Authorization": "Bearer test"}}]
    
    # Turn 1 calls write_file
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_write",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "app.py", "content": "value = 100\n"}),
                            },
                        }
                    ],
                }
            }
        ]
    }

    # Turn 2 calls edit_file
    turn2_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_edit",
                            "function": {
                                "name": "edit_file",
                                "arguments": json.dumps({"path": "app.py", "old_text": "100", "new_text": "200"}),
                            },
                        }
                    ],
                }
            }
        ]
    }

    # Turn 3 completes
    turn3_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Created and edited app.py successfully.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp, turn3_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        
        result = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Create app.py and update value to 200",
            cwd=str(tmp_path),
            enable_native_tools=True,
        )
        assert "successfully" in result
        written = (tmp_path / "app.py").read_text(encoding="utf-8")
        assert "value = 200" in written


def test_free_tier_routing(tmp_path):
    mock_free_provider = {
        "name": "Cloudflare Workers AI",
        "url": "https://api.cloudflare.com/client/v4/accounts/test/ai/v1/chat/completions",
        "headers": {"Authorization": "Bearer cf_test"},
    }
    mock_candidates = [(mock_free_provider, "@cf/meta/llama-3-8b-instruct")]

    mock_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Free tier response.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_free_candidates", return_value=mock_candidates), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp) as mock_fetch, \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        
        res = run_agent_loop("agent", "Do free task", tier="free", cwd=str(tmp_path))
        assert res == "Free tier response."
        call_url = mock_fetch.call_args[0][0]
        assert "cloudflare.com" in call_url


def test_local_tier_routing(tmp_path):
    mock_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Local model response.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.detect_local_endpoint", return_value=("http://127.0.0.1:11434/v1/chat/completions", "Ollama")), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp) as mock_fetch, \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        
        res = run_agent_loop("agent", "Do local task", tier="local", cwd=str(tmp_path))
        assert res == "Local model response."
        call_url = mock_fetch.call_args[0][0]
        assert "11434" in call_url


def test_project_rules_detection_and_injection(tmp_path):
    # Create AGENTS.md in tmp_path
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("rule: zero copula predication\nrule: strictly standard library\n", encoding="utf-8")

    # 1. Direct detector test
    rules = detect_project_rules(str(tmp_path))
    assert rules is not None
    assert "AGENTS.md" in rules
    assert "zero copula predication" in rules

    # 2. Subdirectory inheritance test
    sub_dir = tmp_path / "sub" / "deep"
    sub_dir.mkdir(parents=True)
    inherited_rules = detect_project_rules(str(sub_dir))
    assert inherited_rules is not None
    assert "zero copula predication" in inherited_rules

    # 3. Agent loop injection test
    mock_provider = [{"url": "https://api.openai.com/v1/chat/completions", "headers": {"Authorization": "Bearer test"}}]
    mock_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Rule injection acknowledged.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp) as mock_fetch, \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        
        run_agent_loop("sonnet 5.5", "Check rules", cwd=str(sub_dir))
        payload = mock_fetch.call_args[0][2]
        system_content = payload["messages"][0]["content"]
        assert "AGENTS.md" in system_content
        assert "zero copula predication" in system_content


def test_run_interactive_agent_commands(capsys, tmp_path):
    # Simulate user typing: /status, /model opus 5.5, /tier free, /help, /exit
    inputs = [
        "/status",
        "/model opus 5.5",
        "/tier free",
        "/help",
        "/history",
        "/clear",
        "/exit",
    ]
    input_generator = iter(inputs)

    with patch("builtins.input", lambda _: next(input_generator)):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    captured = capsys.readouterr().out
    assert "HYDRA CODING AGENT" in captured
    assert "Active Model set to: opus 5.5" in captured or "opus 5.5" in captured
    assert "Active tier set to: free" in captured or "free" in captured
    assert "HYDRA CODING AGENT SLASH COMMANDS:" in captured
    assert "Exiting Hydra agent session." in captured


def test_interactive_agent_task_execution(capsys, tmp_path):
    inputs = [
        "Audit module",
        "/exit",
    ]
    input_generator = iter(inputs)

    with patch("builtins.input", lambda _: next(input_generator)), \
         patch("hydra_cli.agent.run_agent_loop", return_value="Audit completed with 0 errors."):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    captured = capsys.readouterr().out
    assert "Audit completed with 0 errors." in captured


def test_router_agent_flags():
    # 1. hydra agent with prompt routes to execute_agent_mode
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent", "--free", "Optimize parser"])
        assert ret == 0
        mock_agent.assert_called_once()
        assert mock_agent.call_args.kwargs["tier"] == "free"
        assert mock_agent.call_args.kwargs["prompt"] == "Optimize parser"

    # 2. hydra agent --local routes to local tier
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent", "--local", "Run test"])
        assert ret == 0
        assert mock_agent.call_args.kwargs["tier"] == "local"

    # 3. hydra agent --tier paid routes to paid tier
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent", "--tier", "paid", "Run test"])
        assert ret == 0
        assert mock_agent.call_args.kwargs["tier"] == "paid"

    # 4. hydra agent without prompt enters interactive mode
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent"])
        assert ret == 0
        assert mock_agent.call_args.kwargs["interactive"] is True

    # 5. hydra agent -i enters interactive mode
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent", "-i"])
        assert ret == 0
        assert mock_agent.call_args.kwargs["interactive"] is True


def test_model_alice_resolves_to_glm_flash():
    from hydra_cli.config import resolve_route
    from hydra_cli.agent import model_status_label

    route = resolve_route("alice")
    assert route["model"] == "glm-5.3-flash"
    assert route["runner"] == "alice"
    assert model_status_label("alice") == "alice -> glm-5.3-flash"
    from hydra_cli.agent import session_model_label
    assert session_model_label("alice", "alice", "glm 5.3 flash") == "alice"


def test_repl_model_alice_reports_glm_flash(capsys, tmp_path):
    inputs = iter(["/model alice", "/exit"])
    with patch("builtins.input", lambda _: next(inputs)):
        assert run_interactive_agent(cwd=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Active model set to: alice" in out
    assert "Local brain. Summon model: glm-5.3-flash" in out
    assert "searches local rules and Alice" in out
    assert "stays off the agent loop" not in out


def test_progen_specification_is_read_before_project_rules(tmp_path):
    spec = tmp_path / "progen_invariants.md"
    skill = tmp_path / "SKILL.md"
    spec.write_text("unit structure : topic comment\n", encoding="utf-8")
    skill.write_text("name : progen\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        f"progen specification: {spec}.\n\nprogen skill specification: {skill}.\n\nrule: local only\n",
        encoding="utf-8",
    )
    rules = detect_project_rules(str(tmp_path))
    assert rules.index("PROGEN READ FIRST: progen_invariants.md") < rules.index("PROGEN READ FIRST: SKILL.md")
    assert rules.index("name : progen") < rules.index("[PROJECT CONTEXT & RULES: AGENTS.md]")
    assert rules.index("[PROJECT CONTEXT & RULES: AGENTS.md]") < rules.index("rule: local only")


def test_documents_agents_names_progen_first():
    agents = r"C:\Users\jpm05\Documents\AGENTS.md"
    text = open(agents, encoding="utf-8").read()
    paths = progen_paths(text)
    assert paths[0].endswith("progen_invariants.md")
    assert paths[1].replace("/", "\\").endswith("\\progen\\SKILL.md")
    rules = detect_project_rules(r"C:\Users\jpm05\Documents")
    assert rules.index("PROGEN READ FIRST: progen_invariants.md") < rules.index("PROGEN READ FIRST: SKILL.md")
    assert rules.index("PROGEN READ FIRST: SKILL.md") < rules.index("[PROJECT CONTEXT & RULES: AGENTS.md]")


def test_effort_and_heat_pickers():
    from hydra_cli.agent import format_effort_picker, format_heat_picker, resolve_effort_choice

    assert resolve_effort_choice("3") == "high"
    assert resolve_effort_choice("max") == "max"
    assert resolve_effort_choice("9") is None
    effort = format_effort_picker("high")
    assert "> 3  high" in effort
    heat = format_heat_picker(None)
    assert "> off" in heat
    heat = format_heat_picker(0.7)
    assert "> 0.7" in heat


def test_heat_window_and_dialect_parsers():
    assert parse_heat("off") is None
    assert parse_heat("0.7") == 0.7
    assert parse_window("128k") == 128 * 1024
    assert parse_window("1m") == 1024 * 1024
    assert "REGISTER: syntax" in dialect_instruction("syntax")
    with pytest.raises(ValueError):
        parse_heat("9")


def test_effort_heat_and_dialect_reach_the_request(tmp_path):
    mock_resp = {"choices": [{"message": {"role": "assistant", "content": "ok", "tool_calls": None}}]}
    provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    with patch("hydra_cli.agent.get_frontier_providers", return_value=provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp) as fetch, \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        assert run_agent_loop(
            alias="sonnet 5.5",
            prompt="check settings",
            cwd=str(tmp_path),
            temperature=0.4,
            effort="high",
            dialect="instruct",
            strategy="sliding",
        ) == "ok"
    payload = fetch.call_args[0][2]
    assert payload["stream"] is False
    assert payload["temperature"] == 0.4
    assert payload["reasoning"]["effort"] == "high"
    assert "REGISTER: instruct" in payload["messages"][0]["content"]


def test_retrieve_strategy_puts_ledger_turns_ahead_of_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    from hydra_cli.context import SessionContextLedger

    ledger = SessionContextLedger(session_id="pytest_retrieve")
    ledger.append_turn("assistant", "alpha_widget lives in the parser module")
    mock_resp = {"choices": [{"message": {"role": "assistant", "content": "found", "tool_calls": None}}]}
    provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    with patch("hydra_cli.agent.get_frontier_providers", return_value=provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp) as fetch, \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop(
            alias="sonnet 5.5",
            prompt="where is alpha_widget",
            cwd=str(tmp_path),
            ledger=ledger,
            strategy="retrieve",
        )
    user = fetch.call_args[0][2]["messages"][1]["content"]
    assert user.index("[RETRIEVED CONTEXT]") < user.index("where is alpha_widget")
    assert "alpha_widget lives in the parser module" in user


def test_repl_session_settings(capsys, tmp_path):
    inputs = iter([
        "/dialect instruct",
        "/effort high",
        "/heat 0.4",
        "/window 128k",
        "/strategy compact",
        "/status",
        "/exit",
    ])
    with patch("builtins.input", lambda _: next(inputs)):
        assert run_interactive_agent(cwd=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Dialect set to: instruct" in out
    assert "Effort set to: high" in out
    assert "Sampling heat set to: 0.4" in out
    assert "Context window set to: 131,072 tokens" in out
    assert "Context strategy set to: compact" in out
    assert "effort  high" in out
    assert "heat    0.4" in out
