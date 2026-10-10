"""
Interactive Hydra REPL with Claude-Code-style slash commands.

Enter with `hydra`, `hydra chat`, or `hydra tui` on a TTY.
The 3-head TUI splash + wordmark prints once at start.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

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



COMMAND_HINTS: Dict[str, Dict[str, Any]] = {
    "/model": {
        "parameter": "<alias>",
        "hint_text": "alias from registered models",
        "description": "Switch active model alias",
        "choices": [
            "sonnet 5.5",
            "opus 5.5",
            "gpt 6.1 sol",
            "flash 2.5",
            "haiku 4.5",
            "gemini 2.5 flash",
            "claude 3.7 sonnet",
        ],
    },
    "/effort": {
        "parameter": "<level>",
        "hint_text": "low | medium | high | xhigh | max | none",
        "description": "Set reasoning effort level",
        "choices": ["low", "medium", "high", "xhigh", "max", "none", "off", "clear"],
    },
    "/system": {
        "parameter": "<text>",
        "hint_text": "system prompt string",
        "description": "Set system prompt for this session",
        "choices": [],
    },
    "/models": {
        "parameter": "[--verbose]",
        "hint_text": "--verbose | --show-ids",
        "description": "List registered models and aliases",
        "choices": ["--verbose", "--show-ids", "-V"],
    },
    "/banner": {
        "parameter": "",
        "hint_text": "",
        "description": "Reprint TUI splash banner",
        "choices": [],
    },
    "/status": {
        "parameter": "",
        "hint_text": "",
        "description": "Show session model, effort, and system prompt",
        "choices": [],
    },
    "/clear": {
        "parameter": "",
        "hint_text": "",
        "description": "Clear screen buffer",
        "choices": [],
    },
    "/quit": {
        "parameter": "",
        "hint_text": "",
        "description": "Leave REPL session",
        "choices": [],
    },
    "/exit": {
        "parameter": "",
        "hint_text": "",
        "description": "Leave REPL session",
        "choices": [],
    },
    "/q": {
        "parameter": "",
        "hint_text": "",
        "description": "Leave REPL session",
        "choices": [],
    },
    "/help": {
        "parameter": "",
        "hint_text": "",
        "description": "Display help message",
        "choices": [],
    },
    "/shortcuts": {
        "parameter": "",
        "hint_text": "",
        "description": "List registered command shortcuts",
        "choices": [],
    },
}


class ReplParameterHints:
    """
    Parameter hints and command signature provider for interactive Hydra REPL.
    Resolves slash commands, suggests parameter values, and renders inline hints.
    """

    def __init__(self, custom_hints: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._lock = threading.RLock()
        self._hints: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in COMMAND_HINTS.items()}
        if custom_hints:
            for cmd, data in custom_hints.items():
                self._hints[cmd.lower()] = dict(data)
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_queries: int = 0
            self._matched_queries: int = 0
            self._unmatched_queries: int = 0
            self._last_command: str = ""

    def register_hint(
        self,
        command: str,
        parameter: str,
        hint_text: str,
        choices: Optional[List[str]] = None,
        description: str = "",
    ) -> None:
        """Register or override slash command parameter hint specification."""
        cmd = command.strip().lower()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        with self._lock:
            self._hints[cmd] = {
                "parameter": parameter.strip(),
                "hint_text": hint_text.strip(),
                "description": description.strip(),
                "choices": list(choices or []),
            }

    def get_hint(self, line: str) -> Optional[Dict[str, Any]]:
        """Resolve parameter hint for given REPL input buffer."""
        clean = line.strip()
        with self._lock:
            self._total_queries += 1

        if not clean.startswith("/"):
            with self._lock:
                self._unmatched_queries += 1
            return None

        parts = clean.split(maxsplit=1)
        cmd = parts[0].lower()
        arg_part = parts[1] if len(parts) > 1 else ""

        with self._lock:
            spec = self._hints.get(cmd)

        if spec is None:
            with self._lock:
                matching_cmds = [c for c in self._hints if c.startswith(cmd)]
            if len(matching_cmds) == 1:
                cmd = matching_cmds[0]
                with self._lock:
                    spec = self._hints.get(cmd)

        if spec is None:
            with self._lock:
                self._unmatched_queries += 1
            return None

        all_choices = spec.get("choices", [])
        if arg_part and all_choices:
            arg_lower = arg_part.lower()
            filtered_choices = [c for c in all_choices if c.lower().startswith(arg_lower)]
        else:
            filtered_choices = list(all_choices)

        with self._lock:
            self._matched_queries += 1
            self._last_command = cmd

        return {
            "command": cmd,
            "parameter": spec.get("parameter", ""),
            "hint_text": spec.get("hint_text", ""),
            "description": spec.get("description", ""),
            "choices": filtered_choices,
            "has_arg": bool(arg_part),
        }

    def get_arg_suggestions(self, command: str, prefix: str = "") -> List[str]:
        """Return valid completion suggestions matching argument prefix."""
        cmd = command.strip().lower()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        with self._lock:
            spec = self._hints.get(cmd)
        if not spec:
            return []
        choices = spec.get("choices", [])
        if not prefix:
            return list(choices)
        pref_l = prefix.lower()
        return [c for c in choices if c.lower().startswith(pref_l)]

    def format_inline_hint(self, line: str) -> str:
        """Render inline ghost text hint for input line."""
        clean = line.strip()
        if not clean.startswith("/"):
            return ""
        hint = self.get_hint(clean)
        if not hint:
            return ""
        param = hint.get("parameter", "")
        if not param:
            return ""
        if hint.get("has_arg") and hint.get("choices"):
            first_match = hint["choices"][0]
            parts = clean.split(maxsplit=1)
            cur_arg = parts[1] if len(parts) > 1 else ""
            if len(first_match) > len(cur_arg):
                return first_match[len(cur_arg):]
            return ""
        if not hint.get("has_arg"):
            return f" {param}"
        return ""

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "total_queries": self._total_queries,
                "matched_queries": self._matched_queries,
                "unmatched_queries": self._unmatched_queries,
                "last_command": self._last_command,
                "registered_commands": len(self._hints),
            }


_DEFAULT_REPL_PARAMETER_HINTS = ReplParameterHints()


def get_default_repl_parameter_hints() -> ReplParameterHints:
    """Return default singleton parameter hint provider."""
    return _DEFAULT_REPL_PARAMETER_HINTS


def reset_repl_parameter_hints() -> None:
    """Reset global parameter hint provider telemetry."""
    _DEFAULT_REPL_PARAMETER_HINTS.reset_metrics()


def create_repl_parameter_hints(
    custom_hints: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ReplParameterHints:
    """Instantiate a new dedicated parameter hint provider."""
    return ReplParameterHints(custom_hints=custom_hints)



DEFAULT_SHORTCUTS: Dict[str, Dict[str, str]] = {
    "!m": {"expansion": "/models", "description": "List registered models"},
    "!s": {"expansion": "/status", "description": "Show session status"},
    "!c": {"expansion": "/clear", "description": "Clear screen buffer"},
    "!b": {"expansion": "/banner", "description": "Reprint TUI splash banner"},
    "!h": {"expansion": "/help", "description": "Show help documentation"},
    "!q": {"expansion": "/quit", "description": "Quit REPL session"},
    "/m": {"expansion": "/model", "description": "Shortcut for /model"},
    "/e": {"expansion": "/effort", "description": "Shortcut for /effort"},
    "/sys": {"expansion": "/system", "description": "Shortcut for /system"},
}


class ShortcutRegistry:
    """
    Keyboard and command prefix shortcut expansion registry for Hydra REPL.
    Translates abbreviated prefixes and command shortcuts into canonical slash commands.
    """

    def __init__(self, initial_shortcuts: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        self._lock = threading.RLock()
        self._shortcuts: Dict[str, Dict[str, str]] = {k: dict(v) for k, v in DEFAULT_SHORTCUTS.items()}
        if initial_shortcuts:
            for k, v in initial_shortcuts.items():
                self._shortcuts[k.lower()] = dict(v)
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_lookups: int = 0
            self._total_expansions: int = 0
            self._last_expanded: str = ""

    def register_shortcut(self, trigger: str, expansion: str, description: str = "") -> None:
        """Register or update shortcut trigger mapping."""
        clean_trigger = trigger.strip().lower()
        clean_exp = expansion.strip()
        with self._lock:
            self._shortcuts[clean_trigger] = {
                "expansion": clean_exp,
                "description": description.strip() or f"Shortcut for {clean_exp}",
            }

    def unregister_shortcut(self, trigger: str) -> bool:
        """Remove shortcut trigger mapping."""
        clean_trigger = trigger.strip().lower()
        with self._lock:
            if clean_trigger in self._shortcuts:
                del self._shortcuts[clean_trigger]
                return True
            return False

    def has_shortcut(self, trigger: str) -> bool:
        """Return true when trigger exists in registry."""
        with self._lock:
            return trigger.strip().lower() in self._shortcuts

    def get_shortcut(self, trigger: str) -> Optional[Dict[str, str]]:
        """Return shortcut metadata dictionary for trigger."""
        with self._lock:
            return self._shortcuts.get(trigger.strip().lower())

    def expand_shortcut(self, line: str) -> Tuple[str, bool]:
        """Expand command shortcut in input line."""
        clean = line.strip()
        with self._lock:
            self._total_lookups += 1

        if not clean:
            return line, False

        parts = clean.split(maxsplit=1)
        trigger = parts[0].lower()
        trailing = parts[1] if len(parts) > 1 else ""

        with self._lock:
            entry = self._shortcuts.get(trigger)

        if entry is None:
            return line, False

        expansion = entry["expansion"]
        expanded_line = f"{expansion} {trailing}".strip() if trailing else expansion

        with self._lock:
            self._total_expansions += 1
            self._last_expanded = trigger

        return expanded_line, True

    def list_shortcuts(self) -> List[Dict[str, str]]:
        """Return sorted list of registered shortcuts."""
        with self._lock:
            res = []
            for k in sorted(self._shortcuts.keys()):
                v = self._shortcuts[k]
                res.append({
                    "trigger": k,
                    "expansion": v["expansion"],
                    "description": v.get("description", ""),
                })
            return res

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "total_lookups": self._total_lookups,
                "total_expansions": self._total_expansions,
                "last_expanded": self._last_expanded,
                "registered_shortcuts": len(self._shortcuts),
            }


_DEFAULT_SHORTCUT_REGISTRY = ShortcutRegistry()


def get_default_shortcut_registry() -> ShortcutRegistry:
    """Return default singleton shortcut registry."""
    return _DEFAULT_SHORTCUT_REGISTRY


def reset_shortcut_registry() -> None:
    """Reset global shortcut registry telemetry."""
    _DEFAULT_SHORTCUT_REGISTRY.reset_metrics()


def create_shortcut_registry(
    initial_shortcuts: Optional[Dict[str, Dict[str, str]]] = None,
) -> ShortcutRegistry:
    """Instantiate a new dedicated shortcut registry."""
    return ShortcutRegistry(initial_shortcuts=initial_shortcuts)


class ReplSession:
    def __init__(self) -> None:
        self.alias = os.environ.get("HYDRA_DEFAULT_ALIAS", "sonnet 5.5").strip() or "sonnet 5.5"
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.effort: Optional[str] = None
        self.reasoning_mode: Optional[str] = None
        self.hints: ReplParameterHints = get_default_repl_parameter_hints()
        self.shortcuts: ShortcutRegistry = get_default_shortcut_registry()
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
    if cmd in ("/shortcuts", "/keys"):
        items = session.shortcuts.list_shortcuts()
        print("Registered REPL shortcuts:")
        for it in items:
            print(f"  {it['trigger']:<6} -> {it['expansion']:<16} : {it['description']}")
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

        expanded_text, was_expanded = session.shortcuts.expand_shortcut(text)
        if was_expanded:
            text = expanded_text

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
