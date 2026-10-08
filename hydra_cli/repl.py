"""
Interactive Hydra REPL with Claude-Code-style slash commands.

Enter with `hydra`, `hydra chat`, or `hydra tui` on a TTY.
The 3-head TUI splash + wordmark prints once at start.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Tuple

from hydra_cli import __version__
from hydra_cli.config import DEFAULT_SYSTEM_PROMPT, MODEL_MAP, resolve_route
from hydra_cli.ui import GREEN_BRIGHT, GREEN_DIM, RESET, print_banner, supports_color


SLASH_HELP = """
Slash commands (Claude Code / Codex style):
  /help                 Show this help
  /banner               Reprint the TUI splash once
  /models [--verbose]   List aliases (ids with --verbose)
  /model <alias>        Switch the active model alias
  /system <text>        Set the system prompt for this session
  /effort <level>       Set reasoning effort (low|medium|high|xhigh|max)
  /status               Show session model, effort, system prompt
  /clear                Clear the screen (keeps session settings)
  /quit  /exit  /q      Leave the REPL

Anything else is sent to the active model as a prompt.
""".strip()


class ReplSession:
    def __init__(self) -> None:
        self.alias = os.environ.get("HYDRA_DEFAULT_ALIAS", "sonnet 5.5").strip() or "sonnet 5.5"
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.effort: Optional[str] = None
        self.reasoning_mode: Optional[str] = None
        route = resolve_route(self.alias)
        if route.get("effort"):
            self.effort = route["effort"]
        if route.get("reasoning_mode"):
            self.reasoning_mode = route["reasoning_mode"]

    def status_line(self) -> str:
        bits = [f"model={self.alias}"]
        if self.effort:
            bits.append(f"effort={self.effort}")
        if self.reasoning_mode:
            bits.append(f"mode={self.reasoning_mode}")
        return " · ".join(bits)


def _c(text: str, code: str) -> str:
    if supports_color():
        return f"{code}{text}{RESET}"
    return text


def _prompt_prefix(session: ReplSession) -> str:
    return _c(f"hydra ({session.alias}) › ", GREEN_BRIGHT)


def _handle_slash(session: ReplSession, line: str) -> Tuple[Optional[int], bool]:
    """
    Returns (exit_code_or_None, handled).
    exit_code None means stay in the loop; int means leave the REPL.
    """
    from hydra_cli.router import print_registered_models

    raw = line.strip()
    if not raw.startswith("/"):
        return None, False
    parts = raw.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("/help", "/?", "/h"):
        print(SLASH_HELP)
        return None, True
    if cmd == "/banner":
        print_banner(detailed=True, version=__version__)
        return None, True
    if cmd in ("/models", "/list-models"):
        verbose = any(a in ("--verbose", "-V", "--show-ids") for a in args)
        print_registered_models(verbose=verbose)
        return None, True
    if cmd == "/model":
        if not args:
            print(f"Active model: {session.alias}")
            print("Usage: /model <alias>")
            return None, True
        # Allow multi-token aliases: /model sol 5.6 pro
        candidate = " ".join(args).strip().lower()
        if candidate not in MODEL_MAP and " ".join(args[:2]).lower() in MODEL_MAP:
            candidate = " ".join(args[:2]).lower()
        if candidate not in MODEL_MAP and args[0].lower() in MODEL_MAP:
            candidate = args[0].lower()
        if candidate not in MODEL_MAP:
            # Still accept raw model ids / unknown aliases (resolve_route passthrough)
            candidate = " ".join(args).strip()
        session.alias = candidate
        route = resolve_route(session.alias)
        session.effort = route.get("effort")
        session.reasoning_mode = route.get("reasoning_mode")
        print(f"Switched to {session.alias}" + (f" ({route['model']})" if candidate in MODEL_MAP else ""))
        return None, True
    if cmd == "/system":
        if not args:
            print("Usage: /system <prompt text>")
            return None, True
        session.system_prompt = " ".join(args)
        print("System prompt updated.")
        return None, True
    if cmd == "/effort":
        if not args:
            print(f"Effort: {session.effort or '(default)'}")
            return None, True
        level = args[0].strip().lower()
        if level in ("none", "off", "clear", "-"):
            session.effort = None
            print("Effort cleared.")
        else:
            session.effort = level
            print(f"Effort set to {level}.")
        return None, True
    if cmd == "/status":
        route = resolve_route(session.alias)
        print(f"alias:    {session.alias}")
        if session.alias in MODEL_MAP or route["model"] != session.alias:
            print(f"model:    {route['model']}")
        print(f"effort:   {session.effort or '(default)'}")
        print(f"mode:     {session.reasoning_mode or '(default)'}")
        preview = session.system_prompt.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"system:   {preview}")
        return None, True
    if cmd == "/clear":
        # Plain clear — never enable mouse tracking / alt screen permanently.
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()
        return None, True
    if cmd in ("/quit", "/exit", "/q"):
        return 0, True

    print(f"Unknown slash command: {cmd}. Try /help")
    return None, True


def run_repl(initial_argv: Optional[List[str]] = None) -> int:
    """Run the interactive Hydra session. Returns a process exit code."""
    from hydra_cli.router import execute_summon

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write(
            "[ERROR] Interactive mode needs a TTY. "
            "Pipe a prompt instead: echo hi | hydra sonnet 5.5\n"
        )
        return 1

    session = ReplSession()
    if initial_argv:
        # Optional: hydra chat sonnet 5.5  → start on that alias
        joined = " ".join(initial_argv).strip().lower()
        if joined in MODEL_MAP:
            session.alias = joined
            route = resolve_route(session.alias)
            session.effort = route.get("effort")
            session.reasoning_mode = route.get("reasoning_mode")
        elif initial_argv[0].lower() in MODEL_MAP:
            session.alias = initial_argv[0].lower()
            route = resolve_route(session.alias)
            session.effort = route.get("effort")
            session.reasoning_mode = route.get("reasoning_mode")

    # Splash once.
    print_banner(detailed=True, version=__version__)
    print(_c(f"Interactive session · {session.status_line()}", GREEN_DIM))
    print(_c("Type /help for slash commands. Ctrl-C or /quit to leave.\n", GREEN_DIM))

    while True:
        try:
            line = input(_prompt_prefix(session))
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue

        text = line.strip()
        if not text:
            continue

        code, handled = _handle_slash(session, text)
        if handled:
            if code is not None:
                return code
            continue

        try:
            rc = execute_summon(
                alias=session.alias,
                prompt=text,
                system_prompt=session.system_prompt,
                effort=session.effort,
                reasoning_mode=session.reasoning_mode,
                stream=True,
                json_mode=False,
            )
            if rc != 0:
                sys.stderr.write(f"[session] last turn exited {rc}\n")
        except KeyboardInterrupt:
            sys.stderr.write("\n[INTERRUPTED]\n")
        except Exception as exc:
            sys.stderr.write(f"\n[HYDRA ERROR] {exc}\n")
