"""Display and streaming UX: sanitization, banner composition, alias listing."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from hydra_cli import __version__
from hydra_cli.display import TokenStreamWriter, sanitize_stream_text
from hydra_cli.providers import SSEParser, _delta_text, stream_chat_completion
from hydra_cli.router import HELP_BANNER, print_registered_models, route_command
from hydra_cli.ui import get_terminal_banner


def test_sanitize_strips_carriage_returns_and_ansi():
    raw = "Hello\r\nworld\rX\x1b[2J\x1b[?1000h\x1b[31mred\x1b[0m"
    clean = sanitize_stream_text(raw)
    assert "\r" not in clean
    assert "\x1b" not in clean
    assert "Hello\nworldXred" == clean


def test_token_stream_writer_coalesces_and_sanitizes():
    buf = StringIO()
    writer = TokenStreamWriter(buf, interval_s=10.0, max_buffer=1000)
    writer.write("Hello\r")
    writer.write(" world")
    assert buf.getvalue() == ""  # still buffered
    writer.write("!\n")
    assert buf.getvalue() == "Hello world!\n"
    writer.finish(trailing_newline=True)
    assert buf.getvalue().endswith("\n\n") or buf.getvalue().endswith("!\n\n")


def test_banner_is_single_composition(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    banner = get_terminal_banner(detailed=True)
    assert banner.count("[1]") == 1
    assert banner.count("Sovereign Multi-Headed AI Shell") == 1
    # Wordmark must not be stacked under the heads (that looked like a double print).
    assert "___ ___" not in banner
    assert f"v{__version__}" in banner


def test_help_banner_uses_wordmark_not_heads():
    assert "[1]" not in HELP_BANNER
    assert "___ ___" in HELP_BANNER
    assert HELP_BANNER.count("Sovereign Multi-Headed AI Shell") == 1


def test_list_models_alias_only_by_default(capsys):
    assert route_command(["--list-models"]) == 0
    out = capsys.readouterr().out
    assert "sonnet 5.5" in out
    assert "->" not in out.split("Frontier Aliases:")[1].split("Free Forge")[0]
    assert "anthropic/claude-sonnet-5.5" not in out.split("Frontier Aliases:")[1].split("Free Forge")[0]


def test_list_models_verbose_shows_ids(capsys):
    assert route_command(["--list-models", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "sonnet 5.5" in out
    assert "anthropic/claude-sonnet-5.5" in out


def test_sse_parser_handles_split_chunks():
    parser = SSEParser()
    part1 = b'data: {"choices":[{"delta":{"content":"Hel'
    part2 = b'lo"}}]}\n\ndata: [DONE]\n\n'
    events = parser.feed(part1)
    assert events == []
    events = parser.feed(part2)
    assert len(events) == 2
    assert '"Hel' in events[0]["data"] or "Hello" in events[0]["data"]
    assert events[1]["data"] == "[DONE]"


def test_delta_text_supports_text_field_and_parts():
    assert _delta_text({"content": "a"}) == "a"
    assert _delta_text({"text": "b"}) == "b"
    assert _delta_text({"content": [{"text": "c"}, {"text": "d"}]}) == "cd"


def test_stream_tolerates_missing_done_after_tokens():
    body = (
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        # no [DONE]
    )
    mock_resp = MagicMock()
    mock_resp.read.side_effect = [body, b""]
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        tokens = list(
            stream_chat_completion(
                url="https://api.test/v1/chat/completions",
                headers={},
                model="test",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert tokens == ["ok"]


def test_removed_broken_alice_emap_alias():
    from hydra_cli.config import MODEL_MAP
    assert "alice-emap" not in MODEL_MAP


def test_sol_56_and_muse_aliases():
    from hydra_cli.config import resolve_route
    assert resolve_route("sol 5.6")["model"] == "openai/gpt-5.6-sol"
    assert resolve_route("gpt-5.6")["model"] == "openai/gpt-5.6-sol"
    assert resolve_route("muse")["model"] == "meta/muse-spark-1.3"
    assert resolve_route("llama 3.1 8b")["providers"] == ["huggingface"]
