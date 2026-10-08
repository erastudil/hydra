"""Slash-command REPL routing and session helpers."""

import sys
from unittest.mock import patch

from hydra_cli.repl import ReplSession, SLASH_HELP, _handle_slash, run_repl
from hydra_cli.router import route_command


class _Stdin:
    def __init__(self, tty=True):
        self._tty = tty

    def isatty(self):
        return self._tty


class _Stdout:
    def __init__(self, tty=True):
        self._tty = tty

    def isatty(self):
        return self._tty


def test_slash_help_and_quit():
    session = ReplSession()
    code, handled = _handle_slash(session, "/help")
    assert handled is True
    assert code is None
    assert "/model" in SLASH_HELP

    code, handled = _handle_slash(session, "/quit")
    assert handled is True
    assert code == 0


def test_slash_model_switch():
    session = ReplSession()
    code, handled = _handle_slash(session, "/model opus 5.5")
    assert handled is True
    assert code is None
    assert session.alias == "opus 5.5"


def test_chat_and_tui_route_to_repl():
    calls = []

    def fake_repl(initial_argv=None):
        calls.append(list(initial_argv or []))
        return 0

    with patch("hydra_cli.repl.run_repl", fake_repl):
        assert route_command(["chat"]) == 0
        assert route_command(["tui", "sonnet", "5.5"]) == 0
    assert calls == [[], ["sonnet", "5.5"]]


def test_bare_hydra_on_tty_enters_repl(monkeypatch):
    entered = []

    def fake_repl(initial_argv=None):
        entered.append(True)
        return 0

    monkeypatch.setattr("sys.stdin", _Stdin(True))
    monkeypatch.setattr("sys.stdout", _Stdout(True))
    with patch("hydra_cli.repl.run_repl", fake_repl):
        assert route_command([]) == 0
    assert entered == [True]


def test_bare_hydra_non_tty_prints_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin(False))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert route_command([]) == 0
    out = capsys.readouterr().out
    assert "USAGE:" in out


def test_run_repl_rejects_non_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin(False))
    monkeypatch.setattr("sys.stdout", _Stdout(True))
    assert run_repl() == 1
    assert "TTY" in capsys.readouterr().err
