"""
Interactive Hydra REPL with Claude-Code-style slash commands.

Enter with `hydra`, `hydra chat`, or `hydra tui` on a TTY.
The 3-head TUI splash + wordmark prints once at start.
"""

from __future__ import annotations

import os
import re
import sys
import difflib
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
    "/alias": {
        "parameter": "[<shorthand> = <target>]",
        "hint_text": "shorthand = target",
        "description": "View or register model alias shorthands",
        "choices": [],
    },
    "/history": {
        "parameter": "[limit | clear]",
        "hint_text": "limit | clear",
        "description": "View or manage REPL command history",
        "choices": ["10", "20", "50", "clear"],
    },
    "/page": {
        "parameter": "[next | prev | <number>]",
        "hint_text": "next | prev | number",
        "description": "Navigate paged REPL menu completions",
        "choices": ["next", "prev", "1", "2"],
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



class ReplFuzzySearch:
    """
    Fuzzy string search and similarity scoring engine for Hydra REPL.
    Evaluates prefix, substring, subsequence, and distance metrics for model aliases and commands.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_searches: int = 0
            self._matched_searches: int = 0
            self._last_pattern: str = ""
            self._last_best_match: str = ""

    def fuzzy_score(self, pattern: str, target: str) -> float:
        """Calculate fuzzy similarity score between 0.0 and 100.0."""
        p = pattern.strip().lower()
        t = target.strip().lower()

        if not p:
            return 100.0
        if not t:
            return 0.0
        if p == t:
            return 100.0

        if t.startswith(p):
            ratio = len(p) / len(t)
            return round(80.0 + (ratio * 20.0), 2)

        words = t.split()
        if any(w.startswith(p) for w in words):
            return 75.0

        if p in t:
            ratio = len(p) / len(t)
            return round(60.0 + (ratio * 20.0), 2)

        p_idx = 0
        p_len = len(p)
        matched_chars = 0
        for char in t:
            if p_idx < p_len and char == p[p_idx]:
                p_idx += 1
                matched_chars += 1

        if p_idx == p_len:
            subseq_ratio = matched_chars / len(t)
            return round(40.0 + (subseq_ratio * 20.0), 2)

        matcher = difflib.SequenceMatcher(None, p, t)
        sim = matcher.ratio()
        if sim >= 0.5:
            return round(sim * 100.0, 2)

        common = set(p) & set(t)
        if common:
            overlap = len(common) / max(len(set(p)), len(set(t)))
            return round(overlap * 30.0, 2)

        return 0.0

    def search(
        self,
        pattern: str,
        candidates: Sequence[str],
        limit: Optional[int] = None,
        min_score: float = 30.0,
    ) -> List[Tuple[str, float]]:
        """Rank candidates against pattern by descending fuzzy similarity score."""
        with self._lock:
            self._total_searches += 1
            self._last_pattern = pattern

        scored: List[Tuple[str, float]] = []
        for cand in candidates:
            sc = self.fuzzy_score(pattern, cand)
            if sc >= min_score:
                scored.append((cand, sc))

        scored.sort(key=lambda x: x[1], reverse=True)
        if limit is not None and limit > 0:
            scored = scored[:limit]

        if scored:
            with self._lock:
                self._matched_searches += 1
                self._last_best_match = scored[0][0]

        return scored

    def find_best(
        self,
        pattern: str,
        candidates: Sequence[str],
        min_score: float = 30.0,
    ) -> Optional[str]:
        """Return single highest scoring candidate exceeding minimum score."""
        results = self.search(pattern, candidates, limit=1, min_score=min_score)
        if results:
            return results[0][0]
        return None

    def search_models(self, pattern: str, limit: Optional[int] = 5) -> List[Tuple[str, float]]:
        """Search registered model aliases matching pattern."""
        candidates = list(MODEL_MAP.keys())
        return self.search(pattern, candidates, limit=limit, min_score=25.0)

    def search_commands(
        self,
        pattern: str,
        commands: Optional[Sequence[str]] = None,
        limit: Optional[int] = 5,
    ) -> List[Tuple[str, float]]:
        """Search available slash commands matching pattern."""
        cmds = commands or [
            "/model",
            "/effort",
            "/system",
            "/status",
            "/models",
            "/banner",
            "/clear",
            "/quit",
            "/help",
            "/shortcuts",
        ]
        return self.search(pattern, cmds, limit=limit, min_score=25.0)

    def get_metrics(self) -> Dict[str, Any]:
        """Return search telemetry counters."""
        with self._lock:
            return {
                "total_searches": self._total_searches,
                "matched_searches": self._matched_searches,
                "last_pattern": self._last_pattern,
                "last_best_match": self._last_best_match,
            }


_DEFAULT_REPL_FUZZY_SEARCH = ReplFuzzySearch()


def get_default_repl_fuzzy_search() -> ReplFuzzySearch:
    """Return default singleton fuzzy search engine."""
    return _DEFAULT_REPL_FUZZY_SEARCH


def reset_repl_fuzzy_search() -> None:
    """Reset global fuzzy search engine telemetry."""
    _DEFAULT_REPL_FUZZY_SEARCH.reset_metrics()


def create_repl_fuzzy_search() -> ReplFuzzySearch:
    """Instantiate a new dedicated fuzzy search engine."""
    return ReplFuzzySearch()



DEFAULT_CONTINUATION_GLYPH: str = "··· "
ASCII_CONTINUATION_GLYPH: str = "... "


class ContinuationGlyphManager:
    """
    Multitrack multiline continuation glyph and buffer completeness evaluator for Hydra REPL.
    Analyzes bracket balance, quote closures, and trailing escapes to render aligned continuation glyphs.
    """

    def __init__(self, glyph: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._glyph = glyph if glyph is not None else DEFAULT_CONTINUATION_GLYPH
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_evaluations: int = 0
            self._incomplete_evaluations: int = 0
            self._complete_evaluations: int = 0

    def get_glyph(self, ascii_only: bool = False) -> str:
        """Return active continuation glyph string."""
        with self._lock:
            if ascii_only:
                return ASCII_CONTINUATION_GLYPH
            return self._glyph

    def set_glyph(self, glyph: str) -> None:
        """Configure custom continuation glyph string."""
        with self._lock:
            self._glyph = glyph

    def check_balance(self, text: str) -> Dict[str, Any]:
        """Analyze text buffer for unclosed brackets, unclosed quotes, or trailing escapes."""
        with self._lock:
            self._total_evaluations += 1

        bracket_pairs = {")": "(", "]": "[", "}": "{"}
        opening_brackets = set(bracket_pairs.values())
        stack: List[str] = []
        in_quote: Optional[str] = None
        escaped = False

        i = 0
        n = len(text)
        while i < n:
            c = text[i]

            if escaped:
                escaped = False
                i += 1
                continue

            if c == "\\":
                escaped = True
                i += 1
                continue

            if not in_quote and i + 2 < n and text[i:i+3] in ('"""', "'''"):
                in_quote = text[i:i+3]
                i += 3
                continue
            elif in_quote in ('"""', "'''") and i + 2 < n and text[i:i+3] == in_quote:
                in_quote = None
                i += 3
                continue

            if in_quote is None and c in ('"', "'"):
                in_quote = c
            elif in_quote == c:
                in_quote = None
            elif in_quote is None:
                if c in opening_brackets:
                    stack.append(c)
                elif c in bracket_pairs:
                    if stack and stack[-1] == bracket_pairs[c]:
                        stack.pop()

            i += 1

        trailing_backslash = escaped
        incomplete = bool(stack or in_quote or trailing_backslash)

        with self._lock:
            if incomplete:
                self._incomplete_evaluations += 1
            else:
                self._complete_evaluations += 1

        return {
            "incomplete": incomplete,
            "open_brackets": stack,
            "in_quote": in_quote,
            "trailing_backslash": trailing_backslash,
        }

    def is_incomplete(self, text: str) -> bool:
        """Return true when buffer represents incomplete multiline input."""
        res = self.check_balance(text)
        return bool(res["incomplete"])

    def format_continuation_prompt(self, prefix_len: int = 16, ascii_only: bool = False) -> str:
        """Render aligned continuation prompt string matching prefix width."""
        glyph = self.get_glyph(ascii_only=ascii_only)
        if prefix_len <= len(glyph):
            return glyph
        padding = " " * (prefix_len - len(glyph))
        return f"{padding}{glyph}"

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "total_evaluations": self._total_evaluations,
                "incomplete_evaluations": self._incomplete_evaluations,
                "complete_evaluations": self._complete_evaluations,
                "active_glyph": self._glyph,
            }


_DEFAULT_CONTINUATION_MANAGER = ContinuationGlyphManager()


def get_default_continuation_manager() -> ContinuationGlyphManager:
    """Return default singleton continuation manager."""
    return _DEFAULT_CONTINUATION_MANAGER


def reset_continuation_manager() -> None:
    """Reset global continuation manager telemetry."""
    _DEFAULT_CONTINUATION_MANAGER.reset_metrics()


def create_continuation_manager(
    glyph: Optional[str] = None,
) -> ContinuationGlyphManager:
    """Instantiate a new dedicated continuation manager."""
    return ContinuationGlyphManager(glyph=glyph)



DEFAULT_MODEL_ALIASES: Dict[str, str] = {
    "sonnet": "sonnet 5.5",
    "opus": "opus 5.5",
    "sol": "gpt 6.1 sol",
    "sol pro": "sol 5.6 pro",
    "flash": "flash 2.5",
    "haiku": "haiku 4.5",
    "qwen": "qwen 3.8",
    "deepseek": "deepseek 4.1 flash",
    "glm": "glm 5.3 flash",
    "mimo": "mimo 2.6 flash",
}


class ReplAliasExpander:
    """
    Model alias shorthand resolution and @mention prompt expansion engine for Hydra REPL.
    Translates abbreviated model names and mention triggers to canonical target aliases.
    """

    def __init__(self, initial_aliases: Optional[Dict[str, str]] = None) -> None:
        self._lock = threading.RLock()
        self._aliases: Dict[str, str] = {k.lower(): v for k, v in DEFAULT_MODEL_ALIASES.items()}
        if initial_aliases:
            for k, v in initial_aliases.items():
                self._aliases[k.lower()] = v
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_expansions: int = 0
            self._shorthand_lookups: int = 0
            self._mention_expansions: int = 0
            self._last_expanded: str = ""

    def register_alias(self, shorthand: str, target: str) -> None:
        """Register or update model alias shorthand mapping."""
        with self._lock:
            self._aliases[shorthand.strip().lower()] = target.strip()

    def unregister_alias(self, shorthand: str) -> bool:
        """Remove model alias shorthand mapping."""
        key = shorthand.strip().lower()
        with self._lock:
            if key in self._aliases:
                del self._aliases[key]
                return True
            return False

    def expand_alias(self, alias: str) -> str:
        """Resolve shorthand alias to canonical model alias."""
        key = alias.strip().lower()
        with self._lock:
            self._shorthand_lookups += 1
            if key in self._aliases:
                canonical = self._aliases[key]
                self._total_expansions += 1
                self._last_expanded = f"{alias}->{canonical}"
                return canonical
        return alias.strip()

    def expand_prompt_mentions(self, text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """Expand prompt-embedded @mention triggers into canonical model aliases."""
        with self._lock:
            aliases_copy = dict(self._aliases)

        expansions: List[Tuple[str, str]] = []
        result = text

        sorted_keys = sorted(aliases_copy.keys(), key=len, reverse=True)
        for key in sorted_keys:
            pattern = re.compile(rf"@({re.escape(key)})\b", re.IGNORECASE)
            matches = pattern.findall(result)
            if matches:
                canonical = aliases_copy[key]
                result = pattern.sub(f"@{canonical}", result)
                for m in matches:
                    expansions.append((m, canonical))

        with self._lock:
            if expansions:
                self._mention_expansions += len(expansions)
                self._total_expansions += len(expansions)
                self._last_expanded = f"mentions:{len(expansions)}"

        return result, expansions

    def list_aliases(self) -> List[Dict[str, str]]:
        """Return sorted list of registered model alias shorthands."""
        with self._lock:
            return [{"shorthand": k, "canonical": self._aliases[k]} for k in sorted(self._aliases.keys())]

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "total_expansions": self._total_expansions,
                "shorthand_lookups": self._shorthand_lookups,
                "mention_expansions": self._mention_expansions,
                "last_expanded": self._last_expanded,
                "registered_aliases": len(self._aliases),
            }


_DEFAULT_ALIAS_EXPANDER = ReplAliasExpander()


def get_default_alias_expander() -> ReplAliasExpander:
    """Return default singleton alias expander."""
    return _DEFAULT_ALIAS_EXPANDER


def reset_alias_expander() -> None:
    """Reset global alias expander telemetry."""
    _DEFAULT_ALIAS_EXPANDER.reset_metrics()


def create_alias_expander(
    initial_aliases: Optional[Dict[str, str]] = None,
) -> ReplAliasExpander:
    """Instantiate a new dedicated alias expander."""
    return ReplAliasExpander(initial_aliases=initial_aliases)


class ReplHistoryDedup:
    """
    Command history deduplication and telemetry manager for Hydra REPL.
    Suppresses consecutive duplicates or removes earlier duplicates across sessions.
    """

    def __init__(
        self,
        max_size: int = 1000,
        strategy: str = "consecutive",
    ) -> None:
        self._lock = threading.RLock()
        self._max_size = max(1, max_size)
        self._strategy = strategy if strategy in ("consecutive", "erase", "none") else "consecutive"
        self._history: List[str] = []
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_recorded: int = 0
            self._duplicates_suppressed: int = 0
            self._last_entry: str = ""

    def get_strategy(self) -> str:
        """Return active deduplication strategy."""
        with self._lock:
            return self._strategy

    def set_strategy(self, strategy: str) -> None:
        """Configure deduplication strategy: consecutive, erase, or none."""
        with self._lock:
            if strategy in ("consecutive", "erase", "none"):
                self._strategy = strategy

    def record(self, entry: str) -> bool:
        """Record command entry into history according to deduplication strategy. Return True if stored, False if suppressed."""
        cleaned = entry.strip()
        if not cleaned:
            return False

        with self._lock:
            self._total_recorded += 1
            if self._strategy == "consecutive":
                if self._history and self._history[-1] == cleaned:
                    self._duplicates_suppressed += 1
                    return False
                self._history.append(cleaned)
            elif self._strategy == "erase":
                if cleaned in self._history:
                    self._history.remove(cleaned)
                    self._duplicates_suppressed += 1
                self._history.append(cleaned)
            else:
                self._history.append(cleaned)

            if len(self._history) > self._max_size:
                self._history.pop(0)

            self._last_entry = cleaned
            return True

    def get_history(self, limit: Optional[int] = None) -> List[str]:
        """Return history entries up to optional limit."""
        with self._lock:
            if limit is not None and limit > 0:
                return list(self._history[-limit:])
            return list(self._history)

    def clear(self) -> None:
        """Clear all entries from active history buffer."""
        with self._lock:
            self._history.clear()
            self._last_entry = ""

    def count(self) -> int:
        """Return count of current history entries."""
        with self._lock:
            return len(self._history)

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "total_recorded": self._total_recorded,
                "duplicates_suppressed": self._duplicates_suppressed,
                "history_size": len(self._history),
                "strategy": self._strategy,
                "last_entry": self._last_entry,
                "unique_entries": len(set(self._history)),
            }


_DEFAULT_HISTORY_DEDUP = ReplHistoryDedup()


def get_default_history_dedup() -> ReplHistoryDedup:
    """Return default singleton history deduplication engine."""
    return _DEFAULT_HISTORY_DEDUP


def reset_history_dedup() -> None:
    """Reset global history dedup telemetry and buffer."""
    _DEFAULT_HISTORY_DEDUP.clear()
    _DEFAULT_HISTORY_DEDUP.reset_metrics()


def create_history_dedup(
    max_size: int = 1000,
    strategy: str = "consecutive",
) -> ReplHistoryDedup:
    """Instantiate a new dedicated history deduplication manager."""
    return ReplHistoryDedup(max_size=max_size, strategy=strategy)


class ReplMenuPager:
    """
    Pagination and bounded viewing manager for REPL completions and menu lists.
    Slices candidate entries into fixed-size windows with bidirectional navigation.
    """

    def __init__(self, page_size: int = 10) -> None:
        self._lock = threading.RLock()
        self._page_size = max(1, page_size)
        self._items: List[Any] = []
        self._current_page: int = 0
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._page_advances: int = 0
            self._page_retreats: int = 0
            self._direct_jumps: int = 0

    def get_page_size(self) -> int:
        """Return active page size."""
        with self._lock:
            return self._page_size

    def set_page_size(self, size: int) -> None:
        """Configure page size and reset current page index."""
        with self._lock:
            self._page_size = max(1, size)
            self._current_page = 0

    def set_items(self, items: List[Any]) -> None:
        """Load candidate items and reset page index to first page."""
        with self._lock:
            self._items = list(items)
            self._current_page = 0

    def total_pages(self) -> int:
        """Return total page count for current items."""
        with self._lock:
            if not self._items:
                return 1
            return (len(self._items) + self._page_size - 1) // self._page_size

    def current_page_index(self) -> int:
        """Return zero-indexed active page number."""
        with self._lock:
            return self._current_page

    def next_page(self) -> bool:
        """Advance to next page. Return True on success, False if already on final page."""
        with self._lock:
            tot = self.total_pages()
            if self._current_page < tot - 1:
                self._current_page += 1
                self._page_advances += 1
                return True
            return False

    def prev_page(self) -> bool:
        """Retreat to previous page. Return True on success, False if already on first page."""
        with self._lock:
            if self._current_page > 0:
                self._current_page -= 1
                self._page_retreats += 1
                return True
            return False

    def set_page(self, page_index: int) -> bool:
        """Jump directly to target page index. Return True if valid, False if out of range."""
        with self._lock:
            tot = self.total_pages()
            if 0 <= page_index < tot:
                self._current_page = page_index
                self._direct_jumps += 1
                return True
            return False

    def get_page_slice(self, page_index: Optional[int] = None) -> List[Any]:
        """Return slice of items belonging to specified or current page index."""
        with self._lock:
            idx = page_index if page_index is not None else self._current_page
            tot = self.total_pages()
            if idx < 0 or idx >= tot or not self._items:
                return []
            start = idx * self._page_size
            end = start + self._page_size
            return self._items[start:end]

    def get_page_indicator(self) -> str:
        """Return formatted page indicator string."""
        with self._lock:
            tot = self.total_pages()
            curr = self._current_page + 1
            count = len(self._items)
            return f"Page {curr}/{tot} ({count} items)"

    def format_page(
        self,
        page_index: Optional[int] = None,
        header: Optional[str] = None,
    ) -> str:
        """Format paged items into readable string block with header and navigation indicator."""
        with self._lock:
            items = self.get_page_slice(page_index)
            idx = page_index if page_index is not None else self._current_page
            start_num = idx * self._page_size + 1
            lines: List[str] = []
            if header:
                lines.append(header)
            if not items:
                lines.append("  (no items)")
            else:
                for i, it in enumerate(items, start_num):
                    lines.append(f"  {i:>3}. {it}")
            lines.append(f"--- {self.get_page_indicator()} ---")
            return "\n".join(lines)

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "total_pages": self.total_pages(),
                "current_page": self._current_page,
                "page_size": self._page_size,
                "item_count": len(self._items),
                "page_advances": self._page_advances,
                "page_retreats": self._page_retreats,
                "direct_jumps": self._direct_jumps,
            }


_DEFAULT_MENU_PAGER = ReplMenuPager()


def get_default_menu_pager() -> ReplMenuPager:
    """Return default singleton menu pager engine."""
    return _DEFAULT_MENU_PAGER


def reset_menu_pager() -> None:
    """Reset global menu pager telemetry and state."""
    _DEFAULT_MENU_PAGER.set_items([])
    _DEFAULT_MENU_PAGER.reset_metrics()


def create_menu_pager(page_size: int = 10) -> ReplMenuPager:
    """Instantiate a new dedicated menu pager manager."""
    return ReplMenuPager(page_size=page_size)


class ReplSession:
    def __init__(self) -> None:
        self.history: ReplHistoryDedup = get_default_history_dedup()
        self.menu_pager: ReplMenuPager = get_default_menu_pager()
        self.alias = os.environ.get("HYDRA_DEFAULT_ALIAS", "sonnet 5.5").strip() or "sonnet 5.5"
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.effort: Optional[str] = None
        self.reasoning_mode: Optional[str] = None
        self.hints: ReplParameterHints = get_default_repl_parameter_hints()
        self.shortcuts: ShortcutRegistry = get_default_shortcut_registry()
        self.fuzzy_search: ReplFuzzySearch = get_default_repl_fuzzy_search()
        self.continuation: ContinuationGlyphManager = get_default_continuation_manager()
        self.alias_expander: ReplAliasExpander = get_default_alias_expander()
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
    if cmd == "/alias":
        if not args:
            aliases = session.alias_expander.list_aliases()
            print("Registered model alias shorthands:")
            for item in aliases:
                print(f"  {item['shorthand']:<12} -> {item['canonical']}")
            return None, True
        sub_line = " ".join(args).strip()
        if "=" in sub_line:
            parts_sub = sub_line.split("=", 1)
            sh_key = parts_sub[0].strip()
            sh_target = parts_sub[1].strip()
            session.alias_expander.register_alias(sh_key, sh_target)
            print(f"Alias registered: {sh_key} -> {sh_target}")
        else:
            can = session.alias_expander.expand_alias(sub_line)
            print(f"Alias '{sub_line}' maps to '{can}'")
        return None, True

    if cmd == "/history":
        if args and args[0].lower() in ("clear", "reset"):
            session.history.clear()
            print("REPL history cleared.")
            return None, True
        limit = None
        if args and args[0].isdigit():
            limit = int(args[0])
        entries = session.history.get_history(limit=limit)
        if not entries:
            print("REPL history empty.")
            return None, True
        print(f"REPL history entries:")
        for idx, item in enumerate(entries, 1):
            print(f"  {idx:>3}  {item}")
        return None, True

    if cmd in ("/page", "/pager"):
        if not args:
            print(session.menu_pager.format_page())
            return None, True
        sub = args[0].lower()
        if sub in ("next", "n"):
            advanced = session.menu_pager.next_page()
            if not advanced:
                print("Already on final page.")
            else:
                print(session.menu_pager.format_page())
        elif sub in ("prev", "p", "back"):
            retreated = session.menu_pager.prev_page()
            if not retreated:
                print("Already on first page.")
            else:
                print(session.menu_pager.format_page())
        elif sub.isdigit():
            target_p = int(sub) - 1
            if session.menu_pager.set_page(target_p):
                print(session.menu_pager.format_page())
            else:
                print(f"Invalid page number: {sub}")
        else:
            print("Usage: /page [next | prev | <number>]")
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

        session.history.record(text)

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
