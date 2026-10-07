"""
Regression tests for the 1.2.1 fixes: agent routing, packaging, Free Forge fallback,
secret hygiene, and swarm ordering and limits. Every test runs without network access.
"""

import json
import os
import sys
import threading
import time
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from hydra_cli import __version__
from hydra_cli.providers import ProviderError

PROVIDER_VARS = (
    "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_TOKEN", "CHEAPERINFERENCE_API_KEY",
    "CHEAPERINFERENCE_API_BASE", "RUNPOD_API_KEY", "RUNPOD_ENDPOINT_URL", "RUNPOD_ENDPOINT_ID",
    "MODAL_ENDPOINT_URL", "MODAL_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "HYDRA_FREE_MODEL", "HYDRA_CLOUDFLARE_MODEL", "HYDRA_HOME", "GITHUB_TOKEN",
)

OPENROUTER = {
    "id": "openrouter", "name": "OpenRouter",
    "url": "https://openrouter.ai/api/v1/chat/completions", "headers": {"Authorization": "Bearer or"},
}
VERCEL = {
    "id": "vercel", "name": "Vercel AI Gateway",
    "url": "https://ai-gateway.vercel.sh/v1/chat/completions", "headers": {"Authorization": "Bearer vg"},
}


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    """No real ~/.hydra, no real keys, a scratch working directory."""
    home = tmp_path / "home"
    (home / ".hydra").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda path: str(home) + path[1:] if path.startswith("~") else path,
    )
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


def _final(content):
    return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": None}}]}


def _tool_call(name, args):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}}]}


# ---------------------------------------------------------------------------
# 1. Agent mode: model id, provider fallback, JSON tool results
# ---------------------------------------------------------------------------

def test_agent_sends_model_id_not_url():
    from hydra_cli.agent import run_agent_loop
    sent = []

    def fake_fetch(url, headers, payload, timeout=120):
        sent.append((url, payload["model"]))
        return _final("done")

    with patch("hydra_cli.agent.get_frontier_providers", return_value=[OPENROUTER, VERCEL]), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fake_fetch):
        assert run_agent_loop("sonnet 5.5", "hi") == "done"
    assert sent == [(OPENROUTER["url"], "anthropic/claude-sonnet-5.5")]


def test_agent_adapts_namespace_per_provider():
    from hydra_cli.agent import run_agent_loop
    sent = []

    def fake_fetch(url, headers, payload, timeout=120):
        sent.append(payload["model"])
        return _final("ok")

    with patch("hydra_cli.agent.get_frontier_providers", return_value=[VERCEL]), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fake_fetch):
        run_agent_loop("grok 4.7", "hi")
    assert sent == ["spacexai/grok-4.7"]


def test_agent_falls_back_after_402_and_sticks_with_working_provider():
    from hydra_cli.agent import run_agent_loop
    calls = []
    responses = iter([_tool_call("filesystem__list_directory", {"path": "."}), _final("empty dir")])

    def fake_fetch(url, headers, payload, timeout=120):
        calls.append(url)
        if url == OPENROUTER["url"]:
            raise ProviderError("HTTP 402 from openrouter.ai: insufficient credits")
        return next(responses)

    registry = MagicMock()
    registry.get_openai_tools.return_value = [{"type": "function", "function": {"name": "filesystem__list_directory"}}]
    registry.dispatch.return_value = {"content": [{"type": "text", "text": ""}], "isError": False}

    with patch("hydra_cli.agent.get_frontier_providers", return_value=[OPENROUTER, VERCEL]), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fake_fetch):
        assert run_agent_loop("sonnet 5.5", "list", registry=registry) == "empty dir"
    # Turn 1: OpenRouter fails, Vercel answers. Turn 2 starts with Vercel.
    assert calls == [OPENROUTER["url"], VERCEL["url"], VERCEL["url"]]


def test_agent_raises_when_every_provider_fails():
    from hydra_cli.agent import run_agent_loop
    with patch("hydra_cli.agent.get_frontier_providers", return_value=[OPENROUTER, VERCEL]), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=ProviderError("HTTP 402")):
        with pytest.raises(ProviderError, match="All configured providers failed"):
            run_agent_loop("sonnet 5.5", "hi")


def test_agent_tool_results_reach_model_as_json():
    from hydra_cli.agent import run_agent_loop
    seen = []
    responses = iter([_tool_call("filesystem__list_directory", {"path": "."}), _final("ok")])
    result = {"content": [{"type": "text", "text": "[FILE] a.txt"}], "isError": False}

    def fake_fetch(url, headers, payload, timeout=120):
        seen.append(json.loads(json.dumps(payload["messages"])))
        return next(responses)

    registry = MagicMock()
    registry.get_openai_tools.return_value = []
    registry.dispatch.return_value = result
    with patch("hydra_cli.agent.get_frontier_providers", return_value=[VERCEL]), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fake_fetch):
        run_agent_loop("sonnet 5.5", "list", registry=registry)
    tool_msg = seen[1][-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"]) == result


def test_agent_respects_vercel_only_models():
    from hydra_cli.agent import run_agent_loop
    calls = []

    def fake_fetch(url, headers, payload, timeout=120):
        calls.append(url)
        return _final("fast")

    with patch("hydra_cli.agent.get_frontier_providers", return_value=[OPENROUTER, VERCEL]), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fake_fetch):
        run_agent_loop("opus 5.5 fast", "hi")
    assert calls == [VERCEL["url"]]


# ---------------------------------------------------------------------------
# 2. Packaging: the MCP template ships with the wheel, and `mcp init` copies it
# ---------------------------------------------------------------------------

def test_pyproject_ships_mcp_template():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as handle:
        text = handle.read()
    section = text.split("[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
    assert "mcp_servers.default.json" in section
    assert os.path.isfile(os.path.join(root, "hydra_cli", "mcp_servers.default.json"))


def test_mcp_init_writes_home_config(hermetic):
    from hydra_cli.router import route_command
    assert route_command(["mcp", "init"]) == 0
    written = hermetic / ".hydra" / "mcp_servers.json"
    servers = json.loads(written.read_text(encoding="utf-8"))["mcpServers"]
    assert "filesystem" in servers


# ---------------------------------------------------------------------------
# 3. Free Forge: valid Cloudflare default and OpenRouter fallback
# ---------------------------------------------------------------------------

def test_cloudflare_default_model_is_real():
    from hydra_cli.config import CATALOG
    assert CATALOG["default_cloudflare_model"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def test_free_candidates_order(monkeypatch):
    from hydra_cli.config import DEFAULT_FREE_MODEL
    from hydra_cli.providers import get_free_candidates
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-abcdef")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123456")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-abcdef")
    candidates = get_free_candidates()
    assert candidates[0][0]["id"] == "cloudflare"
    assert candidates[1] == (candidates[1][0], DEFAULT_FREE_MODEL)
    assert all(p["id"] == "openrouter-free" and m.endswith(":free") for p, m in candidates[1:])
    # An @cf/ override stays on Cloudflare.
    only_cf = get_free_candidates("@cf/meta/llama-3.1-8b-instruct-fp8")
    assert [(p["id"], m) for p, m in only_cf] == [("cloudflare", "@cf/meta/llama-3.1-8b-instruct-fp8")]


def test_free_falls_back_to_openrouter_when_cloudflare_fails(monkeypatch, capsys):
    from hydra_cli.router import execute_free
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-abcdef")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123456")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-abcdef")
    calls = []

    def fake_fetch(url, headers, model, messages, **kwargs):
        calls.append((url, model))
        if "cloudflare" in url:
            raise ProviderError("HTTP 400 error from api.cloudflare.com: No such model")
        return "free answer"

    with patch("hydra_cli.router.fetch_chat_completion", side_effect=fake_fetch):
        assert execute_free("hi", "sys", stream=False) == 0
    out = capsys.readouterr()
    assert "free answer" in out.out
    assert "acct123456" not in out.err
    assert "cloudflare" in calls[0][0] and "openrouter.ai" in calls[1][0]


# ---------------------------------------------------------------------------
# 4. Secret hygiene: redacted errors and a minimal MCP environment
# ---------------------------------------------------------------------------

def test_redact_strips_paths_account_ids_and_secret_values(monkeypatch):
    from hydra_cli.providers import redact
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-supersecret")
    text = ("HTTP 400 from https://api.cloudflare.com/client/v4/accounts/0123abcd/ai/v1/chat/completions "
            "key=sk-or-v1-supersecret path /accounts/0123abcd/ai")
    clean = redact(text)
    assert "0123abcd" not in clean
    assert "sk-or-v1-supersecret" not in clean
    assert "https://api.cloudflare.com/..." in clean


def test_http_error_message_names_host_only(monkeypatch):
    from hydra_cli.providers import fetch_chat_completion
    url = "https://api.cloudflare.com/client/v4/accounts/feedface99/ai/v1/chat/completions"
    err = urllib.error.HTTPError(url, 400, "Bad Request", {}, BytesIO(b'{"errors":[{"message":"No such model"}]}'))
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ProviderError) as info:
            fetch_chat_completion(url, {}, "@cf/x", [{"role": "user", "content": "hi"}])
    assert "feedface99" not in str(info.value)
    assert "api.cloudflare.com" in str(info.value)


def test_router_error_output_is_redacted(monkeypatch, capsys):
    from hydra_cli import router
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-777777")

    def boom(**kwargs):
        raise ProviderError("failed at https://api.cloudflare.com/client/v4/accounts/acct-777777/ai")

    with patch.object(router, "execute_free", side_effect=boom):
        assert router.route_command(["free", "hi"]) == 1
    err = capsys.readouterr().err
    assert "acct-777777" not in err


def test_mcp_server_env_is_minimal(monkeypatch):
    from hydra_cli.mcp import McpSubprocessClient
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-should-not-leak")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vg-should-not-leak")
    monkeypatch.setenv("CUSTOM_PASS", "allowed")
    client = McpSubprocessClient(command="srv", env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_named"},
                                 env_passthrough=["CUSTOM_PASS"])
    env = client.build_env()
    assert "OPENROUTER_API_KEY" not in env
    assert "AI_GATEWAY_API_KEY" not in env
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_named"
    assert env["CUSTOM_PASS"] == "allowed"
    assert env.get("PATH") == os.environ.get("PATH")


def test_mcp_popen_receives_minimal_env(monkeypatch):
    from hydra_cli.mcp import McpSubprocessClient
    monkeypatch.setenv("CHEAPERINFERENCE_API_KEY", "ci-should-not-leak")
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        raise OSError("stop after capture")

    client = McpSubprocessClient(command="srv")
    with patch("subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(OSError):
            client.start()
    assert "CHEAPERINFERENCE_API_KEY" not in captured["env"]


def test_registry_passes_env_passthrough_and_timeout(monkeypatch):
    from hydra_cli.mcp_registry import McpRegistry
    factory = MagicMock()
    reg = McpRegistry(servers={"s": {"command": "x", "env_passthrough": ["FOO"], "timeout": 5}},
                      client_factory=factory, auto_load=False)
    reg.get_client("s")
    kwargs = factory.call_args.kwargs
    assert kwargs["env_passthrough"] == ["FOO"]
    assert kwargs["timeout"] == 5.0


# ---------------------------------------------------------------------------
# 6. Swarm: timeouts, max tokens, auditor ordering, provider reporting
# ---------------------------------------------------------------------------

def test_swarm_high_effort_head_gets_600s_wait():
    from hydra_cli.swarm import run_single_head
    waits = {}

    def fake_fetch(**kwargs):
        waits[kwargs["model"]] = kwargs["timeout"]
        return "ok"

    with patch("hydra_cli.swarm.fetch_chat_completion", side_effect=fake_fetch):
        run_single_head("auditor", {"model": "openai/gpt-6.1-sol", "effort": "high", "reasoning_mode": "pro"},
                        "t", [VERCEL])
        run_single_head("coder", {"model": "anthropic/claude-sonnet-5.5"}, "t", [VERCEL])
    assert waits["openai/gpt-6.1-sol"] == 600
    assert waits["anthropic/claude-sonnet-5.5"] == 180


def test_swarm_honors_max_tokens_and_runs_auditor_after_coder():
    from hydra_cli.swarm import execute_swarm
    log = []
    lock = threading.Lock()

    def fake_fetch(**kwargs):
        user = kwargs["messages"][1]["content"]
        system = kwargs["messages"][0]["content"]
        with lock:
            log.append({"system": system, "user": user, "max_tokens": kwargs["max_tokens"]})
        if "Principal Software Engineer" in system:
            time.sleep(0.05)
            return "def rev(s): return s[::-1]"
        return "review or design"

    with patch("hydra_cli.swarm.get_frontier_providers", return_value=[VERCEL]), \
         patch("hydra_cli.swarm.fetch_chat_completion", side_effect=fake_fetch):
        results = execute_swarm("reverse a string", json_output=True, max_tokens=321)

    assert [r.role for r in results] == ["architect", "coder", "auditor", "synthesizer"]
    assert all(entry["max_tokens"] == 321 for entry in log)
    order = [e["system"] for e in log]
    coder_at = next(i for i, s in enumerate(order) if "Principal Software Engineer" in s)
    auditor_at = next(i for i, s in enumerate(order) if "Inspector" in s)
    assert auditor_at > coder_at
    assert "def rev(s): return s[::-1]" in log[auditor_at]["user"]


def test_swarm_reports_provider_model_and_cost(capsys):
    from hydra_cli.swarm import run_single_head, _print_head

    def fake_fetch(**kwargs):
        if kwargs["url"] == OPENROUTER["url"]:
            raise ProviderError("HTTP 402 error from openrouter.ai: no credits")
        return {"content": "ok", "model": "anthropic/claude-sonnet-5.5", "upstream": None,
                "usage": {"cost": 0.0123}}

    with patch("hydra_cli.swarm.fetch_chat_completion", side_effect=fake_fetch):
        res = run_single_head("coder", {"title": "Implementer", "model": "anthropic/claude-sonnet-5.5"},
                              "t", [OPENROUTER, VERCEL])
    assert res.provider == "Vercel AI Gateway"
    assert res.cost == 0.0123
    assert res.to_dict()["provider"] == "Vercel AI Gateway"
    _print_head(res)
    out = capsys.readouterr().out
    # Default human view: head title + cost; full model/provider ids only when verbose.
    assert "[HEAD: IMPLEMENTER]" in out
    assert "$0.0123" in out
    assert "OpenRouter: HTTP 402" in out
    assert "via Vercel AI Gateway" not in out
    assert "anthropic/claude-sonnet-5.5" not in out
    _print_head(res, verbose=True)
    verbose_out = capsys.readouterr().out
    assert "via Vercel AI Gateway" in verbose_out
    assert "anthropic/claude-sonnet-5.5" in verbose_out


# ---------------------------------------------------------------------------
# 8. Minor fixes
# ---------------------------------------------------------------------------

def test_mcp_rpc_times_out_on_silent_server():
    from hydra_cli.mcp import McpSubprocessClient

    class SilentStdout:
        def readline(self):
            time.sleep(5)
            return ""

    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = SilentStdout()
    proc.poll.return_value = None
    client = McpSubprocessClient(command="srv", timeout=0.2)
    client._process = proc
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client.list_tools()
    assert time.monotonic() - started < 2


def test_mcp_client_reports_package_version():
    from hydra_cli.mcp import McpSubprocessClient
    client = McpSubprocessClient(command="srv")
    with patch.object(client, "_send_rpc", return_value={}) as rpc, \
         patch.object(client, "_send_notification"):
        client._initialize()
    params = rpc.call_args.args[1]
    assert params["clientInfo"]["version"] == __version__


def test_banner_has_one_tagline(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from hydra_cli.ui import get_terminal_banner
    banner = get_terminal_banner()
    assert banner.count("Sovereign Multi-Headed AI Shell") == 1
    assert f"v{__version__}" in banner
    assert banner.count("___ ___") == 1
    assert "HERMES" not in banner
    assert banner.count(r"(\___/)") == 3


def test_default_mcp_config_has_no_deprecated_packages_and_keeps_sqlite_in_hydra_home():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "hydra_cli", "mcp_servers.default.json"), encoding="utf-8") as handle:
        servers = json.load(handle)["mcpServers"]
    flat = json.dumps(servers)
    for dead in ("server-fetch", "server-github", "server-brave-search", "server-postgres", "server-puppeteer"):
        assert f"@modelcontextprotocol/{dead}" not in flat
    assert servers["fetch"] == {**servers["fetch"], "command": "uvx", "args": ["mcp-server-fetch"]}
    assert "${HYDRA_HOME}/workspace.db" in servers["sqlite"]["args"]


def test_hydra_home_interpolation(hermetic):
    from hydra_cli.mcp_registry import interpolate_env_vars
    assert interpolate_env_vars("${HYDRA_HOME}/workspace.db") == os.path.join(str(hermetic), ".hydra") + "/workspace.db"


def test_failed_head_reports_every_provider_error():
    from hydra_cli.swarm import run_single_head

    def fake_fetch(**kwargs):
        if kwargs["url"] == OPENROUTER["url"]:
            raise ProviderError("HTTP 402 error from openrouter.ai: no credits")
        raise ProviderError("Empty completion from ai-gateway.vercel.sh: the token limit ran out")

    with patch("hydra_cli.swarm.fetch_chat_completion", side_effect=fake_fetch):
        res = run_single_head("architect", {"model": "anthropic/claude-opus-5.5"}, "t", [OPENROUTER, VERCEL])
    assert "OpenRouter: HTTP 402" in res.error
    assert "Vercel AI Gateway: Empty completion" in res.error


def test_empty_completion_at_length_limit_says_so():
    from hydra_cli.providers import fetch_chat_completion
    body = json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    with patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(ProviderError, match="Raise --max-tokens"):
            fetch_chat_completion(VERCEL["url"], {}, "anthropic/claude-opus-5.5", [], max_tokens=10)
