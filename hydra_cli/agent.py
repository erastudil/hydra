"""
Autonomous ReAct agent execution loop with MCP tool calling, native coding tools, and multi-turn state.
Zero external dependencies.
"""
from hydra_cli.context import (
    SessionContextLedger,
    context_label,
    format_context_picker,
    register_label,
    resolve_context_mode,
)
from hydra_cli.ui import StreamWrap, clean_pasted_text, drain_steering, prompt_input, render_prompt_box, wrap_text
from hydra_cli._version import __version__

from datetime import datetime, timezone
import json
import os
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

import copy
import difflib
import re
import subprocess

from hydra_cli.config import (
    DEFAULT_ORCHESTRATOR_MODEL,
    DEFAULT_AGENT_MODEL,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    build_cached_system_prompt,
    MODEL_MAP,
    OUTPUT_RESERVE_TOKENS,
    get_context_window,
    hydra_home,
    input_char_budget,
    resolve_route,
)
from hydra_cli.hands import dispatch_native, native_openai_tools
from hydra_cli.tool_adapter import (
    adapt_messages_for_prompt_tools,
    extract_tool_calls,
    format_tool_observation,
    is_tool_unsupported_error,
)
from hydra_cli.providers import (
    ProviderError,
    UsageError,
    adapt_model_for_url,
    attach_tool_capability,
    completion_timeout,
    describe_endpoint,
    detect_local_endpoint,
    ensure_temperature,
    get_free_candidates,
    get_frontier_providers,
    providers_for_model,
    reasoning_fields,
    redact,
)
from hydra_cli.ui import (
    GREEN_BOLD,
    GREEN_BRIGHT,
    GREEN_MID,
    RESET,
    ThinkingSpinner,
    supports_color,
)


def print_unified_diff(path: str, old_content: str, new_content: str, raw_diff: bool = False) -> None:
    """Print colorized unified diff of file mutations to stderr."""
    color_on = supports_color()
    RED = "\033[31m" if color_on else ""
    GREEN = "\033[32m" if color_on else ""
    CYAN = "\033[36m" if color_on else ""
    RESET_C = "\033[0m" if color_on else ""

    if raw_diff:
        lines = new_content.splitlines()
    else:
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}"))

    if not lines:
        return

    sys.stderr.write(f"\n{CYAN}--- Diff: {path} ---{RESET_C}\n")
    for line in lines[:100]:
        clean_line = line.rstrip("\r\n")
        if clean_line.startswith("+") and not clean_line.startswith("+++"):
            sys.stderr.write(f"{GREEN}{clean_line}{RESET_C}\n")
        elif clean_line.startswith("-") and not clean_line.startswith("---"):
            sys.stderr.write(f"{RED}{clean_line}{RESET_C}\n")
        elif clean_line.startswith("@@"):
            sys.stderr.write(f"{CYAN}{clean_line}{RESET_C}\n")
        else:
            sys.stderr.write(f"{clean_line}\n")
    if len(lines) > 100:
        sys.stderr.write(f"{CYAN}... [{len(lines) - 100} lines truncated] ...{RESET_C}\n")
    sys.stderr.flush()


DEFAULT_AGENT_SYSTEM_PROMPT = os.environ.get(
    "HYDRA_AGENT_SYSTEM_PROMPT",
    (
        f"You are Hydra v{__version__}, an elite autonomous software engineering agent operating with the autonomy, precision, and multi-turn execution rigor of Cursor and Antigravity.\n"
        "Your mission is to solve coding tasks, refactorings, bug fixes, and implementations end-to-end with verifiable empirical proof.\n\n"
        "OPERATIONAL WORKFLOW (Autonomous ReAct Loop):\n"
        "1. Inspect: Systematically explore the workspace before making changes. Use grep_search, find_files, list_dir, and read_file to inspect files, locate symbols, and understand existing patterns.\n"
        "2. Plan: Formulate a minimal, deterministic plan. Identify invariants, dependencies, and potential side effects.\n"
        "3. Implement: Apply surgical changes using edit_file or write_file. Prefer minimal, clean diffs that preserve existing code conventions and formatting.\n"
        "4. Verify: Always run tests, type checks, or build commands using run_command to verify changes before declaring completion. Exit code 0 is passing; unverified assertions carry zero truth value.\n\n"
        "CONTEXT LEDGER & STEERING:\n"
        "- The system maintains a rigorous context ledger of your execution history. Commands like /compact and /retrieve manipulate this state.\n"
        "- The user may interrupt your execution or provide in-flight steering directives. You must immediately realign your plan to these directives.\n\n"
        "TOOL USAGE INSTRUCTIONS:\n"
        "- Proactively invoke tools at every turn to inspect and modify state: read_file, edit_file, write_file, run_command, grep_search, find_files, list_dir.\n"
        "- Never guess or hallucinate file contents or test results. Always read files before modifying them.\n"
        "- Use edit_file for precise, scoped search-and-replace modifications with unique old_text.\n"
        "- Use write_file for creating new files or complete rewrites.\n"
        "- Use run_command to execute test suites, linters, and verification scripts.\n"
        "- Drive tasks to verifiable completion. Do not halt prematurely, do not leave placeholders, stubs, or TODO comments.\n"
        "- If an execution step fails, analyze the error output and iteratively fix the issue until all tests pass."
    ),
)


PROJECT_RULE_FILES = ("AGENTS.md", ".cursorrules", "CLAUDE.md")
_PROGEN_KEYS = ("progen specification", "progen skill specification")
_RULE_CAP = 25_000

DIALECTS = ("progen", "instruct", "gfc", "slack")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
CONTEXT_STRATEGIES = ("recall", "sliding")


def _read_capped(path: str, cap: int = _RULE_CAP) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read()
    if len(content) > cap:
        return content[:cap] + "\n[RULES TRUNCATED: 25k limit]"
    return content


def progen_paths(text: str) -> List[str]:
    """Paths named by an AGENTS.md turn-one read, in file order."""
    found: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() not in _PROGEN_KEYS:
            continue
        path = value.strip().strip(".").strip().strip('"').strip("'")
        if path and os.path.isfile(path) and path not in found:
            found.append(path)
    return found


def detect_project_rules(cwd: Optional[str] = None) -> Optional[str]:
    """
    Search cwd and up to 5 parent directories for project guidance files
    (AGENTS.md, .cursorrules, CLAUDE.md).

    When the file names a progen specification and skill, those files are
    loaded first and the project rules follow.
    """
    curr = os.path.abspath(cwd or os.getcwd())
    for _ in range(6):
        for fname in PROJECT_RULE_FILES:
            target = os.path.join(curr, fname)
            if not os.path.isfile(target):
                continue
            try:
                with open(target, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
            except OSError:
                continue
            if not content.strip():
                continue
            paths = progen_paths(content)
            if len(content) > _RULE_CAP:
                content = content[:_RULE_CAP] + "\n[RULES TRUNCATED: 25k limit]"
            chunks: List[str] = []
            for path in paths:
                try:
                    body = _read_capped(path).strip()
                except OSError:
                    continue
                if body:
                    chunks.append(f"[PROGEN READ FIRST: {os.path.basename(path)}]\n{body}")
            chunks.append(f"[PROJECT CONTEXT & RULES: {fname}]\n{content}")
            return "\n\n".join(chunks)
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return None


def model_status_label(alias: str) -> str:
    """Alias plus the provider model id the next request will post."""
    model_id = resolve_route(alias)["model"]
    if model_id == alias:
        return alias
    return f"{alias} -> {model_id}"


MODEL_PICKER: List[str] = [
    "alice",
    "glm 5.3 flash",
    "glm 5.3",
    "opus 5.5",
    "sonnet 5.5",
    "sol 6.1",
    "gemini 3.8",
    "grok 4.7",
    "qwen 3.8",
    "deepseek",
    "llama 4",
    "qwen2.5-coder:7b",
]

ALIAS_TYPOS = {
    "alise": "alice",
    "alicee": "alice",
    "glm flash": "glm 5.3 flash",
    "opus5.5": "opus 5.5",
    "sonnet5.5": "sonnet 5.5",
}

COMMAND_TYPOS = {
    "/modle": "/model",
    "/modl": "/model",
    "/mdoel": "/model",
    "/cleer": "/clear",
    "/claer": "/clear",
    "/hlep": "/help",
    "/helpp": "/help",
    "/staus": "/status",
    "/stak": "/stack",
    "/skils": "/skills",
    "/summonn": "/summon",
    "/nw": "/new",
    "/neu": "/new",
}


def session_model_label(alias: str, runner: Optional[str], summon: str) -> str:
    """Chrome label. The alias alone. Provider ids belong in the /model picker."""
    del summon
    if runner == "alice":
        return "alice"
    return alias


def resolve_model_choice(arg: str) -> Optional[str]:
    """Map a picker number or catalog alias to a registered alias. Unknown input returns None."""
    text = " ".join(arg.strip().lower().split())
    if not text:
        return None
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(MODEL_PICKER):
            return MODEL_PICKER[index - 1]
        return None
    text = ALIAS_TYPOS.get(text, text)
    if text in MODEL_MAP:
        return text
    return None


def resolve_effort_choice(arg: str) -> Optional[str]:
    """Map a picker number or effort name to a level. Unknown input returns None."""
    text = " ".join(arg.strip().lower().split())
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(EFFORT_LEVELS):
            return EFFORT_LEVELS[index - 1]
        return None
    if text in EFFORT_LEVELS:
        return text
    return None


HEAT_PRESETS: List[Tuple[str, str]] = [
    ("off", "omit temperature"),
    ("0", "deterministic"),
    ("0.2", "tight"),
    ("0.7", "balanced"),
    ("1", "open"),
]


def format_effort_picker(active: Optional[str]) -> str:
    """Numbered effort list. The marker names the active level."""
    lines = ["effort :"]
    for index, name in enumerate(EFFORT_LEVELS, 1):
        mark = ">" if name == active else " "
        lines.append(f"{mark} {index}  {name}")
    lines.append(f"current : {active or 'route default'}")
    lines.append("select : /effort <number or level>")
    lines.append("clear : /effort off")
    return "\n".join(lines)


def format_heat_picker(active: Optional[float]) -> str:
    """Heat presets. The marker names the active value."""
    lines = ["heat :"]
    for token, note in HEAT_PRESETS:
        value = parse_heat(token)
        mark = ">" if value == active else " "
        lines.append(f"{mark} {token:<4} {note}")
    label = "off" if active is None else f"{active:g}"
    lines.append(f"current : {label}")
    lines.append("select : /heat <value or off>")
    return "\n".join(lines)


def format_model_picker(active_alias: str, active_summon: str) -> str:
    """Numbered alias list. The marker names the active brain."""
    lines = ["model picker :"]
    summon_id = resolve_route(active_summon)["model"]
    for index, name in enumerate(MODEL_PICKER, 1):
        route = resolve_route(name)
        mark = ">" if name == active_alias else " "
        if route.get("runner") == "alice":
            detail = f"local brain, summons {summon_id}"
        else:
            detail = route["model"]
        lines.append(f"{mark} {index:>2}  {name:<22} {detail}")
    lines.append("select : /model <number or alias>")
    return "\n".join(lines)


def dialect_instruction(name: str) -> str:
    """Session register block appended after project rules."""
    key = (name or "progen").strip().lower()
    if key == "syntax":
        key = "progen"
    blocks = {
        "progen": (
            "[REGISTER: progen]\n"
            "Every unit is topic : comment.\n"
            "One blank line between units.\n"
            "Zero leading copula."
        ),
        "instruct": (
            "[REGISTER: instruct]\n"
            "Condition words: always, never, if, then, else, until, while, require, assert, emit."
        ),
        "gfc": (
            "[REGISTER: gfc]\n"
            "Greene Feynman Clarity. Plain words, concrete examples, short sentences."
        ),
        "slack": (
            "[REGISTER: slack]\n"
            "Conversational prose permitted."
        ),
    }
    return blocks.get(key, blocks["progen"])


def parse_heat(arg: str) -> Optional[float]:
    """Sampling heat. None omits temperature. Raises ValueError on a bad token."""
    token = arg.strip().lower()
    if token in ("", "off", "omit", "none", "default"):
        return None
    value = float(token)
    if value < 0 or value > 2:
        raise ValueError("heat must be between 0 and 2")
    return value


def parse_window(arg: str) -> int:
    """Context window in tokens. Accepts 131072, 128k, or 1m."""
    token = arg.strip().lower().replace(",", "").replace("_", "")
    mult = 1
    if token.endswith("m"):
        mult = 1024 * 1024
        token = token[:-1]
    elif token.endswith("k"):
        mult = 1024
        token = token[:-1]
    value = int(float(token) * mult)
    if value < 1024:
        raise ValueError("context window must be at least 1024 tokens")
    return value


class HierarchicalScratchpad:
    """Bounded working memory scratchpad with typed sections (WO-06)."""

    def __init__(self, task: str = ""):
        self.task: str = task
        self.status: str = "PLANNING"
        self.hypotheses: List[str] = []
        self.working_notes: List[str] = []
        self.entity_graph: Dict[str, Dict[str, Any]] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "hypotheses": list(self.hypotheses),
            "working_notes": list(self.working_notes),
            "entity_graph": dict(self.entity_graph),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HierarchicalScratchpad":
        pad = cls(task=data.get("task", ""))
        pad.status = data.get("status", "PLANNING")
        pad.hypotheses = list(data.get("hypotheses", []))
        pad.working_notes = list(data.get("working_notes", []))
        pad.entity_graph = dict(data.get("entity_graph", {}))
        return pad

    def record_tool_invocation(self, tool_name: str, args: Dict[str, Any], result: Any, is_error: bool = False) -> None:
        args_summary = json.dumps(args, ensure_ascii=False)
        if len(args_summary) > 80:
            args_summary = args_summary[:77] + "..."
        res_summary = str(result)
        if len(res_summary) > 100:
            res_summary = res_summary[:97] + "..."
        note = f"{tool_name}({args_summary}) -> {'ERR: ' if is_error else ''}{res_summary}"
        self.working_notes.append(note)
        if len(self.working_notes) > 15:
            self.working_notes = self.working_notes[-15:]

        target = args.get("path") or args.get("query") or args.get("url") or args.get("key") or tool_name
        self.entity_graph[str(target)] = {
            "tool": tool_name,
            "status": "error" if is_error else "ok",
            "summary": res_summary,
        }
        if len(self.entity_graph) > 20:
            keys = list(self.entity_graph.keys())
            self.entity_graph = {k: self.entity_graph[k] for k in keys[-20:]}

    def format_for_context(self) -> str:
        lines = [
            "[WORKING MEMORY SCRATCHPAD]",
            f"State: {self.status}",
            f"Active Task: {self.task}",
        ]
        if self.hypotheses:
            lines.append("Active Plan / Hypotheses:")
            for h in self.hypotheses[-4:]:
                lines.append(f"  - {h}")
        if self.entity_graph:
            lines.append("Discovered Entities:")
            for k, v in list(self.entity_graph.items())[-6:]:
                summary = v.get("summary", str(v)) if isinstance(v, dict) else str(v)
                lines.append(f"  - {k} ({v.get('tool', 'entity')}): {summary}")
        if self.working_notes:
            lines.append("Recent Observations:")
            for n in self.working_notes[-5:]:
                lines.append(f"  * {n}")
        return "\n".join(lines)


class SessionCheckpointer:
    """Cyclic statechart checkpointer saving sessions to ~/.hydra/sessions/<session_id>.json (WO-06)."""

    VALID_STATES = ("INIT", "PLANNING", "EXECUTING", "OBSERVING", "VERIFYING", "COMPLETED", "MAX_TURNS", "ERROR")

    def __init__(
        self,
        session_id: str,
        alias: str,
        model: str,
        task: str,
        max_turns: int,
    ):
        self.session_id: str = session_id
        self.alias: str = alias
        self.model: str = model
        self.task: str = task
        self.max_turns: int = max_turns
        self.state: str = "INIT"
        self.turn: int = 0
        now = datetime.now(timezone.utc).isoformat()
        self.created_at: str = now
        self.updated_at: str = now
        self.scratchpad = HierarchicalScratchpad(task=task)
        self.turns: List[Dict[str, Any]] = []
        self.bounded_messages: List[Dict[str, Any]] = []
        self.final_response: Optional[str] = None
        self.sessions_dir = os.path.join(hydra_home(), "sessions")
        self.file_path = os.path.join(self.sessions_dir, f"{self.session_id}.json")

    def transition(self, new_state: str) -> None:
        if new_state in self.VALID_STATES:
            self.state = new_state
            self.scratchpad.status = new_state
            self.save()

    def record_turn(self, turn_number: int, assistant_msg: Dict[str, Any], tool_executions: List[Dict[str, Any]]) -> None:
        self.turn = turn_number
        self.turns.append({
            "turn": turn_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assistant": assistant_msg,
            "tool_executions": tool_executions,
        })
        self.save()

    def save(self) -> None:
        try:
            os.makedirs(self.sessions_dir, exist_ok=True)
            self.updated_at = datetime.now(timezone.utc).isoformat()
            data = {
                "session_id": self.session_id,
                "state": self.state,
                "alias": self.alias,
                "model": self.model,
                "task": self.task,
                "turn": self.turn,
                "max_turns": self.max_turns,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "scratchpad": self.scratchpad.to_dict(),
                "turns_count": len(self.turns),
                "turns": self.turns,
                "bounded_messages": self.bounded_messages,
                "final_response": self.final_response,
            }
            tmp_path = self.file_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            sys.stderr.write(f"[HYDRA CHECKPOINT] Note: session save skipped ({redact(e)})\n")
            sys.stderr.flush()

    @classmethod
    def load(cls, session_id: str) -> Optional["SessionCheckpointer"]:
        sessions_dir = os.path.join(hydra_home(), "sessions")
        file_path = os.path.join(sessions_dir, f"{session_id}.json")
        if not os.path.isfile(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            inst = cls(
                session_id=data.get("session_id", session_id),
                alias=data.get("alias", ""),
                model=data.get("model", ""),
                task=data.get("task", ""),
                max_turns=data.get("max_turns", 15),
            )
            inst.state = data.get("state", "INIT")
            inst.turn = data.get("turn", 0)
            inst.created_at = data.get("created_at", inst.created_at)
            inst.updated_at = data.get("updated_at", inst.updated_at)
            inst.scratchpad = HierarchicalScratchpad.from_dict(data.get("scratchpad", {}))
            inst.turns = data.get("turns", [])
            inst.bounded_messages = data.get("bounded_messages", [])
            inst.final_response = data.get("final_response")
            return inst
        except Exception:
            return None


_CONTEXT_MARKERS = (
    "context window",
    "context length",
    "maximum context",
    "context_length_exceeded",
    "too many tokens",
    "input exceeds",
    "exceeds the context",
    "prompt is too long",
)

_RETRY_AFTER_RE = re.compile(r"retry-after[\"']?\s*[:=]\s*[\"']?(\d+)", re.IGNORECASE)


def wire_chars(messages: List[Dict[str, Any]]) -> int:
    """Serialized size of the messages that ride in the completion payload."""
    return sum(len(json.dumps(message, ensure_ascii=False, default=str)) for message in messages)


def classify_provider_output(exc: BaseException) -> str:
    """Map a provider error body to shrink, retry_same, or failover."""
    text = str(exc).lower()
    if any(marker in text for marker in _CONTEXT_MARKERS):
        return "shrink"
    if "in_flight_budget" in text or "in-flight budget" in text:
        return "retry_same"
    return "failover"


def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Parse a Retry-After hint from a provider error body."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    return float(match.group(1))


def _fit_output_text(text: str, keep: int) -> str:
    if len(text) <= keep:
        return text
    withheld = len(text) - keep
    return (
        text[:keep]
        + f"\n[OUTPUT FITS WINDOW: {withheld} chars withheld. Full text stays on the session ledger.]"
    )


def _shrink_once(messages: List[Dict[str, Any]], frozen: set) -> bool:
    """Shrink the largest reasoning trace, then the largest tool output."""
    ranked = []
    for message in messages:
        if message.get("role") == "system":
            continue
        role = message.get("role")
        for field in ("reasoning_content", "reasoning", "content"):
            value = message.get(field)
            if not isinstance(value, str) or len(value) <= 240:
                continue
            key = (id(message), field)
            if key in frozen:
                continue
            if field != "content":
                priority = 0
            elif role == "tool":
                priority = 1
            else:
                priority = 2
            ranked.append((priority, -len(value), key, message, field, value))
    if not ranked:
        return False
    ranked.sort(key=lambda item: (item[0], item[1]))
    for _priority, _neg, key, message, field, value in ranked:
        keep = 240 if field != "content" else max(240, len(value) // 2)
        fitted = _fit_output_text(value, keep)
        if len(fitted) >= len(value):
            frozen.add(key)
            continue
        message[field] = fitted
        return True
    return False


def _fit_messages(messages: List[Dict[str, Any]], budget: int) -> None:
    frozen: set = set()
    guard = 0
    while wire_chars(messages) > budget and guard < 64:
        guard += 1
        if not _shrink_once(messages, frozen):
            break


def _build_bounded_messages(
    messages: List[Dict[str, Any]],
    turn_groups: List[List[Dict[str, Any]]],
    scratchpad: HierarchicalScratchpad,
    max_history_turns: int = 5,
    max_context_chars: int = 400_000,
) -> List[Dict[str, Any]]:
    """
    Live window for one agent turn.
    The system message stays byte-for-byte. Older tool rounds leave whole.
    Recalled ledger turns come back through the ledger, not through a summary.
    Tool outputs and reasoning traces shrink until the serialized window fits.
    """
    if not messages:
        return []

    system_msg = copy.deepcopy(messages[0])
    user_msg = copy.deepcopy(messages[1]) if len(messages) > 1 else {"role": "user", "content": ""}
    pinned_system = system_msg.get("content")

    recent_groups = [copy.deepcopy(group) for group in turn_groups[-max_history_turns:]]
    while len(recent_groups) > 1:
        probe = [system_msg, user_msg]
        for group in recent_groups:
            probe.extend(group)
        if wire_chars(probe) <= max_context_chars:
            break
        recent_groups.pop(0)

    grouped_ids = {id(message) for group in turn_groups for message in group}
    tail = [copy.deepcopy(message) for message in messages[2:] if id(message) not in grouped_ids]

    unloaded = len(turn_groups) - len(recent_groups)
    bounded: List[Dict[str, Any]] = [system_msg, user_msg]
    if unloaded:
        note = scratchpad.format_for_context()
        bounded.append({
            "role": "assistant",
            "content": (
                f"[UNLOADED TOOL ROUNDS]\n"
                f"{unloaded} earlier tool rounds left the live window.\n"
                f"Their text stays on the session ledger for verbatim recall.\n"
                f"{note}"
            ),
        })
        bounded.append({
            "role": "user",
            "content": "Continue from the live tool rounds. Recall ledger turns when a name or path matters.",
        })
    for group in recent_groups:
        bounded.extend(group)
    bounded.extend(tail)
    _fit_messages(bounded, max_context_chars)
    if pinned_system is not None:
        bounded[0]["content"] = pinned_system
    return bounded


def _fetch_raw_completion(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    """Execute raw HTTP POST request returning parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    where = describe_endpoint(url)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ProviderError(redact(f"HTTP {e.code} from {where}: {error_body}")) from e
    except URLError as e:
        raise ProviderError(redact(f"Connection failed to {where}: {e.reason}")) from e
    except json.JSONDecodeError as e:
        raise ProviderError(f"Invalid JSON from {where}: {e}") from e
    if isinstance(parsed, dict) and parsed.get("error"):
        err = parsed["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise ProviderError(redact(f"Provider error from {where}: {message}"))
    return parsed


def _emit_message_content(message: Dict[str, Any], on_token: Optional[Callable[[str], None]]) -> None:
    content = message.get("content")
    if on_token and isinstance(content, str) and content:
        on_token(content)


def _append_text(state: Dict[str, Any], text: str, on_token: Optional[Callable[[str], None]]) -> None:
    if not text:
        return
    state["content"].append(text)
    if on_token:
        on_token(text)


def _absorb_content(state: Dict[str, Any], content: Any, on_token: Optional[Callable[[str], None]]) -> None:
    if isinstance(content, str):
        _append_text(state, content, on_token)
        return
    if not isinstance(content, list):
        return
    for part in content:
        if isinstance(part, str):
            _append_text(state, part, on_token)
            continue
        if not isinstance(part, dict):
            continue
        block_type = str(part.get("type") or "")
        if block_type in ("tool_use", "tool_call", "function_call"):
            function = part.get("function") if isinstance(part.get("function"), dict) else {}
            _merge_tool_call(state, {
                "id": part.get("id") or part.get("tool_use_id") or function.get("id"),
                "type": "function",
                "function": {
                    "name": part.get("name") or function.get("name") or "",
                    "arguments": part.get("input", part.get("arguments", function.get("arguments"))),
                },
            })
            continue
        text = part.get("text") or part.get("content") or ""
        if isinstance(text, str):
            _append_text(state, text, on_token)


def _tool_slot_index(state: Dict[str, Any], tc: Dict[str, Any]) -> int:
    if tc.get("index") is not None:
        return int(tc["index"])
    tid = tc.get("id")
    if tid:
        for idx, slot in state["tools"].items():
            if slot.get("id") == tid:
                return int(idx)
    if state["tools"]:
        return max(int(idx) for idx in state["tools"])
    return 0


def _merge_tool_call(state: Dict[str, Any], tc: Dict[str, Any]) -> None:
    if not isinstance(tc, dict):
        return
    idx = _tool_slot_index(state, tc)
    slot = state["tools"].setdefault(idx, {
        "id": "",
        "type": "function",
        "function": {"name": "", "arguments": ""},
    })
    if tc.get("id"):
        slot["id"] = tc["id"]
    if tc.get("type"):
        slot["type"] = tc["type"]
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = fn.get("name") or tc.get("name") or ""
    if name:
        current = slot["function"]["name"]
        if not current:
            slot["function"]["name"] = name
        elif name != current and not current.endswith(name):
            slot["function"]["name"] = current + name
    _merge_arguments(slot, fn.get("arguments", tc.get("arguments", tc.get("input"))))


def _merge_arguments(slot: Dict[str, Any], arguments: Any) -> None:
    if arguments is None or arguments == "":
        return
    if isinstance(arguments, (dict, list)):
        slot["function"]["arguments"] = json.dumps(arguments, ensure_ascii=False)
        return
    slot["function"]["arguments"] += str(arguments)


def _merge_reasoning_details(state: Dict[str, Any], details: Any) -> None:
    if not isinstance(details, list):
        return
    slots = state.setdefault("reasoning_details", {})
    for position, block in enumerate(details):
        if not isinstance(block, dict):
            continue
        idx = block.get("index", position)
        slots[idx] = dict(block)


def _apply_stream_delta(delta: Dict[str, Any], state: Dict[str, Any], on_token: Optional[Callable[[str], None]]) -> None:
    _absorb_content(state, delta.get("content") or "", on_token)
    reasoning = delta.get("reasoning_content")
    if reasoning is None:
        reasoning = delta.get("reasoning") or ""
    if isinstance(reasoning, str) and reasoning:
        state["reasoning"].append(reasoning)
    _merge_reasoning_details(state, delta.get("reasoning_details"))
    for tc in delta.get("tool_calls") or []:
        _merge_tool_call(state, tc)


def _message_from_stream_state(state: Dict[str, Any]) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": "".join(state["content"]) or None,
    }
    if state["tools"]:
        calls = []
        for idx in sorted(state["tools"]):
            slot = state["tools"][idx]
            if not slot["id"]:
                slot["id"] = f"call_{idx}"
            calls.append(slot)
        message["tool_calls"] = calls
    if state["reasoning"]:
        message["reasoning_content"] = "".join(state["reasoning"])
    details = state.get("reasoning_details") or {}
    if details:
        message["reasoning_details"] = [details[idx] for idx in sorted(details, key=lambda item: str(item))]
    return message


def _empty_stream_state() -> Dict[str, Any]:
    return {"content": [], "reasoning": [], "tools": {}}


def _consume_completion_body(resp: Any, on_token: Optional[Callable[[str], None]], where: str) -> Dict[str, Any]:
    """Read an SSE stream, or one JSON body, and return an OpenAI chat completion object."""
    state = _empty_stream_state()
    saw_done = False
    buffered: List[str] = []
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("{") and not line.startswith("data:"):
            parsed = json.loads(line)
            if isinstance(parsed, dict) and parsed.get("error"):
                err = parsed["error"]
                message = err.get("message") if isinstance(err, dict) else str(err)
                raise ProviderError(redact(f"Provider error from {where}: {message}"))
            choices = parsed.get("choices") or []
            if not choices:
                raise ProviderError(f"Empty completion from {where}")
            message = choices[0].get("message") or {}
            _emit_message_content(message, on_token)
            return parsed
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and chunk.get("error"):
                err = chunk["error"]
                message = err.get("message") if isinstance(err, dict) else str(err)
                raise ProviderError(redact(f"Provider error from {where}: {message}"))
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            message = choice.get("message") or {}
            delta = choice.get("delta") or {}
            if delta:
                _apply_stream_delta(delta, state, on_token)
            elif message:
                _absorb_content(state, message.get("content") or "", on_token)
                for tc in message.get("tool_calls") or []:
                    _merge_tool_call(state, tc)
                _merge_reasoning_details(state, message.get("reasoning_details"))
                reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
                if isinstance(reasoning, str) and reasoning:
                    state["reasoning"].append(reasoning)
            continue
        buffered.append(line)
    if buffered and not state["content"] and not state["tools"]:
        blob = "\n".join(buffered)
        if blob.startswith("{"):
            parsed = json.loads(blob)
            choices = parsed.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                _emit_message_content(message, on_token)
                return parsed
    if not saw_done and not state["content"] and not state["tools"] and not state["reasoning"] and not state.get("reasoning_details"):
        raise ProviderError(f"Stream from {where} ended before data: [DONE]")
    finish = "tool_calls" if state["tools"] else "stop"
    return {"choices": [{"message": _message_from_stream_state(state), "finish_reason": finish}]}


def _fetch_streaming_completion(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: int = 120,
    on_token: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Stream one chat completion and return the assembled message JSON."""
    body = dict(payload)
    body["stream"] = True
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    where = describe_endpoint(url)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return _consume_completion_body(resp, on_token, where)
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ProviderError(redact(f"HTTP {e.code} from {where}: {error_body}")) from e
    except URLError as e:
        raise ProviderError(redact(f"Connection failed to {where}: {e.reason}")) from e
    except json.JSONDecodeError as e:
        raise ProviderError(f"Invalid JSON from {where}: {e}") from e


def tool_result_text(result: Any) -> str:
    """Serialize an MCP tool result for the model. Strings pass through; anything else is JSON."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(result), ensure_ascii=False)


def run_agent_loop(
    alias: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    registry: Optional[Any] = None,
    max_turns: int = 15,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    session_id: Optional[str] = None,
    resume_session: bool = False,
    max_history_turns: int = 5,
    tier: Optional[str] = None,
    enable_native_tools: bool = True,
    subagent_depth: int = 0,
    cwd: Optional[str] = None,
    use_prompt_adapter: bool = False,
    steer_mode: bool = False,
    skip_next_tool: bool = False,
    ledger: Optional[SessionContextLedger] = None,
    effort: Optional[str] = None,
    dialect: Optional[str] = None,
    strategy: str = "recall",
    on_token: Optional[Callable[[str], None]] = None,
    max_context_chars: Optional[int] = None,
) -> str:
    """
    Execute autonomous agent loop with native coding tools and MCP tool invocation until completion or max turns.
    Includes bounded working memory, hierarchical scratchpads, and persistent session
    statechart checkpointing saved to ~/.hydra/sessions/<session_id>.json (WO-04, WO-06).
    """
    target_cwd = os.path.abspath(cwd or os.getcwd())
    resolved_tier = (tier or "").lower().strip()
    if not resolved_tier and alias in ("free", "local"):
        resolved_tier = alias

    actual_alias = DEFAULT_ORCHESTRATOR_MODEL if alias in ("agent", "auto", "paid", "frontier", "orchestrator") else alias

    if resolved_tier == "free":
        candidates = get_free_candidates()
        providers = [c[0] for c in candidates]
        requested_model = candidates[0][1]
        route = {"model": requested_model, "effort": None, "reasoning_mode": None}
    elif resolved_tier == "local":
        local_url, local_name = detect_local_endpoint()
        requested_model = actual_alias if actual_alias not in ("local", "agent", "auto", "paid", "frontier", "free") else DEFAULT_LOCAL_MODEL
        providers = [{"name": local_name, "url": local_url, "headers": {"Content-Type": "application/json"}}]
        route = {"model": requested_model, "effort": None, "reasoning_mode": None}
    else:
        route = resolve_route(actual_alias)
        requested_model = route["model"]
        ensure_temperature(requested_model, temperature)
        providers = providers_for_model(requested_model, get_frontier_providers())
        if not providers:
            raise ProviderError("No frontier provider credentials found in ~/.hydra/.env or environment.")

    # Initialize or resume session checkpointer
    if session_id:
        clean_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_", "."))
    else:
        clean_id = f"session_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    checkpointer: Optional[SessionCheckpointer] = None
    if resume_session and clean_id:
        checkpointer = SessionCheckpointer.load(clean_id)

    if ledger is None:
        ledger = SessionContextLedger(session_id=clean_id)

    if not checkpointer:
        checkpointer = SessionCheckpointer(
            session_id=clean_id,
            alias=actual_alias,
            model=requested_model,
            task=prompt,
            max_turns=max_turns,
        )

    checkpointer.transition("PLANNING")

    # Project context rules auto-detection (AGENTS.md, .cursorrules, CLAUDE.md)
    rules_context = detect_project_rules(target_cwd)
    base_sys = (
        DEFAULT_AGENT_SYSTEM_PROMPT
        if (not system_prompt or system_prompt == DEFAULT_SYSTEM_PROMPT)
        else system_prompt
    )
    if rules_context and rules_context not in base_sys:
        effective_sys = f"{base_sys}\n\n{rules_context}"
    else:
        effective_sys = base_sys
    register = dialect_instruction(dialect) if dialect else ""
    if register and register not in effective_sys:
        effective_sys = f"{effective_sys}\n\n{register}"

    original_prompt = prompt
    prior = ledger.render_prior(original_prompt, strategy) if ledger.turns else ""
    if prior:
        prompt = prior + "\n\n" + original_prompt

    sys_text = build_cached_system_prompt(effective_sys)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": prompt},
    ]
    ledger.append_turn(role="user", content=original_prompt)

    # Native tools and MCP registry setup
    native_reg = None
    mcp_reg = None
    if enable_native_tools:
        from hydra_cli.native_tools import NativeToolRegistry
        if isinstance(registry, NativeToolRegistry):
            native_reg = registry
            mcp_reg = None
        else:
            native_reg = NativeToolRegistry(
                cwd=target_cwd,
                subagent_depth=subagent_depth,
                tier=resolved_tier or None,
                alias=actual_alias,
            )
            if hasattr(native_reg, "ledger"):
                native_reg.ledger = ledger
            mcp_reg = registry
    else:
        mcp_reg = registry

    tools: List[Dict[str, Any]] = []
    if native_reg:
        tools.extend(native_reg.get_openai_tools())
    tools.extend(native_openai_tools())
    if mcp_reg:
        tools.extend(mcp_reg.get_openai_tools())

    route_effort = effort if effort else route.get("effort")
    reasoning = reasoning_fields(route_effort, route.get("reasoning_mode"))
    timeout = completion_timeout(reasoning)
    history_turns = max_history_turns
    reserve_tokens = 16384 if (route_effort or "").lower() == "high" else OUTPUT_RESERVE_TOKENS
    if max_context_chars is None:
        window_tokens = get_context_window(requested_model)
    else:
        window_tokens = max(1024, max_context_chars // 4)
    context_chars = input_char_budget(window_tokens, tools, reserve_tokens)

    color_on = supports_color()
    c_bright = GREEN_BRIGHT if color_on else ""
    c_mid = GREEN_MID if color_on else ""
    c_reset = RESET if color_on else ""
    c_tool_head = f"{c_bright}[HYDRA AGENT]{c_reset}"
    c_tool_call = c_mid

    order = list(providers)
    turn_groups: List[List[Dict[str, Any]]] = []
    use_prompt_tool_adapter = use_prompt_adapter

    def complete_turn() -> Dict[str, Any]:
        nonlocal use_prompt_tool_adapter, context_chars
        last_error: Optional[Exception] = None

        for index, provider in enumerate(order):
            attempts = 0
            while True:
                bounded_messages = _build_bounded_messages(
                    messages=messages,
                    turn_groups=turn_groups,
                    scratchpad=checkpointer.scratchpad,
                    max_history_turns=history_turns,
                    max_context_chars=context_chars,
                )
                checkpointer.bounded_messages = list(bounded_messages)
                if use_prompt_tool_adapter and tools:
                    effective_messages = adapt_messages_for_prompt_tools(bounded_messages, tools)
                else:
                    effective_messages = bounded_messages

                payload: Dict[str, Any] = {
                    "model": adapt_model_for_url(provider["url"], requested_model),
                    "messages": effective_messages,
                    "stream": False,
                }
                if tools and not use_prompt_tool_adapter:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                if reasoning:
                    payload["reasoning"] = reasoning
                if temperature is not None:
                    payload["temperature"] = temperature
                if max_tokens is not None:
                    payload["max_tokens"] = max_tokens
                attach_tool_capability(provider["url"], payload)
                host = provider["url"].lower()
                if "openrouter.ai" not in host:
                    effective_messages = [
                        {key: value for key, value in message.items() if key != "reasoning_details"}
                        if isinstance(message, dict) else message
                        for message in effective_messages
                    ]
                    payload["messages"] = effective_messages
                try:
                    if on_token is not None:
                        payload["stream"] = True
                        res_json = _fetch_streaming_completion(
                            provider["url"],
                            provider["headers"],
                            payload,
                            timeout=timeout,
                            on_token=on_token,
                        )
                    else:
                        res_json = _fetch_raw_completion(provider["url"], provider["headers"], payload, timeout=timeout)
                    if not res_json.get("choices"):
                        raise ProviderError(f"{provider.get('name', 'Provider')} returned an empty choices array.")
                except UsageError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if not use_prompt_tool_adapter and tools and is_tool_unsupported_error(exc):
                        sys.stderr.write(
                            f"{c_tool_head} Provider '{provider.get('name', 'Provider')}' rejected native tool schema ({redact(exc)}). "
                            f"Switching to prompt-based tool calling adapter.\n"
                        )
                        sys.stderr.flush()
                        use_prompt_tool_adapter = True
                        return complete_turn()

                    action = classify_provider_output(exc)
                    provider_name = provider.get("name", "Provider")
                    if action == "shrink" and attempts < 3:
                        attempts += 1
                        context_chars = max(4096, context_chars // 2)
                        sys.stderr.write(
                            f"{c_tool_head} {provider_name} rejected the payload: context window exceeded. "
                            f"Fitting the live window to {context_chars} chars and retrying {provider_name}.\n"
                        )
                        sys.stderr.flush()
                        continue
                    if action == "retry_same" and attempts < 2:
                        attempts += 1
                        context_chars = max(4096, context_chars // 2)
                        wait = min(retry_after_seconds(exc) or 5.0, 20.0)
                        sys.stderr.write(
                            f"{c_tool_head} {provider_name} rejected the payload: in-flight credit budget. "
                            f"Fitting the live window to {context_chars} chars and retrying {provider_name} after {wait:g}s.\n"
                        )
                        sys.stderr.flush()
                        time.sleep(wait)
                        continue

                    if index + 1 < len(order):
                        sys.stderr.write(
                            f"{c_tool_head} {provider_name} failed ({redact(exc)}). "
                            f"Trying {order[index + 1].get('name', 'next provider')}.\n"
                        )
                        sys.stderr.flush()
                    break
                if index:
                    order.insert(0, order.pop(index))
                return res_json
        checkpointer.transition("ERROR")
        raise ProviderError(redact(f"All configured providers failed for '{requested_model}'. Last error: {last_error}"))

    turn_idx = 0
    active_skip_next = skip_next_tool

    while turn_idx < max_turns:
        for directive in drain_steering():
            directive_msg = {
                "role": "user",
                "content": f"[IN-FLIGHT STEERING DIRECTIVE]: {directive}",
            }
            messages.append(directive_msg)
            if turn_groups:
                turn_groups[-1].append(directive_msg)
            ledger.append_turn(role="user", content=f"[IN-FLIGHT STEERING DIRECTIVE]: {directive}")
            sys.stderr.write(f"{c_tool_head} Steering directive received: {directive}\n")
            sys.stderr.flush()
        try:
            with ThinkingSpinner(
                message="Thinking...",
                status_messages=[
                    "Reasoning...",
                    "Synthesizing plan...",
                    "Evaluating invariants...",
                    "Inspecting context...",
                    "Planning next action...",
                ],
            ):
                res_json = complete_turn()
        except KeyboardInterrupt:
            try:
                steer_input = prompt_input(
                    "\n[Interrupted] Steer in-flight (enter instruction to redirect agent, or press Enter to cancel turn): "
                ).strip()
            except (KeyboardInterrupt, EOFError):
                steer_input = ""

            if steer_input:
                directive_msg = {
                    "role": "user",
                    "content": f"[IN-FLIGHT STEERING DIRECTIVE]: {steer_input}",
                }
                messages.append(directive_msg)
                turn_idx += 1
                continue
            else:
                checkpointer.transition("COMPLETED")
                checkpointer.final_response = "[Turn aborted by developer]"
                checkpointer.save()
                return "[Turn aborted by developer]"

        choice = res_json["choices"][0]
        message = choice.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls and (use_prompt_tool_adapter or tools):
            content_str = message.get("content", "") or ""
            parsed_calls = extract_tool_calls(content_str)
            if parsed_calls:
                tool_calls = parsed_calls
                message["tool_calls"] = tool_calls

        if tool_calls:
            ledger.append_turn(
                role="assistant",
                content=message.get("content") or "",
                thought=message.get("reasoning_content") or message.get("thought"),
                tool_calls=tool_calls
            )

        if not tool_calls:
            # Model emitted final answer -> statechart transition to COMPLETED
            final_content = message.get("content", "") or ""
            checkpointer.final_response = final_content
            checkpointer.transition("COMPLETED")
            ledger.append_turn(
                role="assistant",
                content=final_content,
                thought=message.get("reasoning_content") or message.get("thought")
            )
            return final_content

        # Statechart transition to EXECUTING
        checkpointer.transition("EXECUTING")
        current_turn_msgs = [message]
        tool_executions: List[Dict[str, Any]] = []

        skip_remaining_tools = False
        steering_directive: Optional[str] = None
        turn_aborted = False

        # Telemetry: print model thought/preamble if present before tool calls
        thought_content = message.get("reasoning_content") or message.get("thought")
        if thought_content and str(thought_content).strip():
            wrapped = wrap_text(str(thought_content).strip())
            sys.stderr.write(f"\n{c_mid}[Thought]{c_reset} {wrapped}\n")
            sys.stderr.flush()

        msg_content = message.get("content") or ""
        if msg_content and on_token is None:
            preamble = re.sub(r"<tool_call>.*?</tool_call>", "", msg_content, flags=re.DOTALL | re.IGNORECASE).strip()
            preamble = re.sub(r"```(?:tool_call|tool).*?```", "", preamble, flags=re.DOTALL | re.IGNORECASE).strip()
            if preamble:
                wrapped_pre = wrap_text(preamble)
                sys.stderr.write(f"\n{c_mid}{wrapped_pre}{c_reset}\n")
                sys.stderr.flush()

        # Execute each tool call
        for tc_idx, tc in enumerate(tool_calls):
            call_id = tc.get("id", "")
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
            except Exception:
                args = {}

            if skip_remaining_tools:
                obs_text = "[Tool execution skipped by developer for steering]"
                if use_prompt_tool_adapter:
                    tool_msg = {
                        "role": "user",
                        "content": format_tool_observation(fn_name, obs_text, call_id=call_id),
                    }
                else:
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": obs_text,
                    }
                messages.append(tool_msg)
                current_turn_msgs.append(tool_msg)
                tool_executions.append({
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": args,
                    "result": obs_text,
                    "is_error": False,
                })
                continue

            tool_skipped = False
            if active_skip_next:
                active_skip_next = False
                tool_skipped = True

            args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)

            if steer_mode and not tool_skipped:
                try:
                    c_prompt = f"[HYDRA STEER] Tool: {fn_name}({args_str}). Execute? [y/n/s(teer)/q]: "
                    steer_choice = prompt_input(c_prompt).strip()
                except KeyboardInterrupt:
                    try:
                        steer_input = prompt_input(
                            "\n[Interrupted] Steer in-flight (enter instruction to redirect agent, or press Enter to cancel turn): "
                        ).strip()
                    except (KeyboardInterrupt, EOFError):
                        steer_input = ""
                    if steer_input:
                        steer_choice = "s"
                        steering_directive = steer_input
                    else:
                        steer_choice = "q"
                except EOFError:
                    steer_choice = "q"

                s_lower = steer_choice.lower()
                if s_lower in ("", "y", "yes"):
                    pass
                elif s_lower in ("n", "no", "skip"):
                    tool_skipped = True
                elif s_lower in ("s", "steer"):
                    if not steering_directive:
                        try:
                            prompt_text = prompt_input("[HYDRA STEER] Enter steering instruction: ").strip()
                        except (KeyboardInterrupt, EOFError):
                            prompt_text = ""
                        steering_directive = prompt_text or "Re-evaluate plan and proceed."
                    skip_remaining_tools = True
                    tool_skipped = True
                elif s_lower in ("q", "quit", "abort"):
                    turn_aborted = True
                    break
                else:
                    if s_lower.startswith("n"):
                        tool_skipped = True
                    elif s_lower.startswith("s"):
                        if not steering_directive:
                            try:
                                prompt_text = prompt_input("[HYDRA STEER] Enter steering instruction: ").strip()
                            except (KeyboardInterrupt, EOFError):
                                prompt_text = ""
                            steering_directive = prompt_text or "Re-evaluate plan and proceed."
                        skip_remaining_tools = True
                        tool_skipped = True
                    elif s_lower.startswith("q"):
                        turn_aborted = True
                        break

            if turn_aborted:
                break

            if tool_skipped:
                obs_text = "[Tool execution skipped by developer]"
                if use_prompt_tool_adapter:
                    tool_msg = {
                        "role": "user",
                        "content": format_tool_observation(fn_name, obs_text, call_id=call_id),
                    }
                else:
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": obs_text,
                    }
                messages.append(tool_msg)
                current_turn_msgs.append(tool_msg)
                tool_executions.append({
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": args,
                    "result": obs_text,
                    "is_error": False,
                })
                checkpointer.scratchpad.record_tool_invocation(fn_name, args, obs_text, is_error=False)
                continue

            orig_file_content: Optional[str] = None
            diff_path: Optional[str] = None

            if fn_name == "read_file":
                fpath = args.get("path", "") if isinstance(args, dict) else ""
                s_line = args.get("start_line") if isinstance(args, dict) else None
                e_line = args.get("end_line") if isinstance(args, dict) else None
                s_val = int(s_line) if s_line is not None else 1
                if e_line is not None:
                    e_val = int(e_line)
                else:
                    abs_p = os.path.abspath(os.path.join(target_cwd, fpath)) if fpath else ""
                    if abs_p and os.path.isfile(abs_p):
                        try:
                            with open(abs_p, "r", encoding="utf-8", errors="replace") as _f:
                                e_val = sum(1 for _ in _f)
                        except Exception:
                            e_val = "end"
                    else:
                        e_val = "end"
                sys.stderr.write(f"\n{c_tool_head} Read: {fpath} (lines {s_val}-{e_val})\n")
                sys.stderr.flush()

            elif fn_name == "write_file":
                fpath = args.get("path", "") if isinstance(args, dict) else ""
                fcontent = args.get("content", "") if isinstance(args, dict) else ""
                byte_count = len(fcontent.encode("utf-8"))
                sys.stderr.write(f"\n{c_tool_head} Write: {fpath} ({byte_count} bytes)\n")
                sys.stderr.flush()
                diff_path = fpath
                abs_p = os.path.abspath(os.path.join(target_cwd, fpath))
                if os.path.isfile(abs_p):
                    try:
                        with open(abs_p, "r", encoding="utf-8", errors="replace") as f_orig:
                            orig_file_content = f_orig.read()
                    except Exception:
                        orig_file_content = None
                else:
                    orig_file_content = ""

            elif fn_name == "edit_file":
                fpath = args.get("path", "") if isinstance(args, dict) else ""
                sys.stderr.write(f"\n{c_tool_head} Edit: {fpath}\n")
                sys.stderr.flush()
                diff_path = fpath
                abs_p = os.path.abspath(os.path.join(target_cwd, fpath))
                if os.path.isfile(abs_p):
                    try:
                        with open(abs_p, "r", encoding="utf-8", errors="replace") as f_orig:
                            orig_file_content = f_orig.read()
                    except Exception:
                        orig_file_content = None
                else:
                    orig_file_content = ""

            elif fn_name == "run_command":
                cmd_str = args.get("command", "") if isinstance(args, dict) else str(args)
                sys.stderr.write(f"\n{c_tool_head} $ {cmd_str}\n")
                sys.stderr.flush()

            elif fn_name == "grep_search":
                q_str = args.get("query", "") if isinstance(args, dict) else str(args)
                p_str = args.get("path", ".") if isinstance(args, dict) else "."
                sys.stderr.write(f"\n{c_tool_head} Grep: '{q_str}' in {p_str}\n")
                sys.stderr.flush()

            elif fn_name == "find_files":
                pat_str = args.get("pattern", "*") if isinstance(args, dict) else "*"
                p_str = args.get("path", ".") if isinstance(args, dict) else "."
                sys.stderr.write(f"\n{c_tool_head} Find: '{pat_str}' in {p_str}\n")
                sys.stderr.flush()

            elif fn_name == "list_dir":
                p_str = args.get("path", ".") if isinstance(args, dict) else "."
                sys.stderr.write(f"\n{c_tool_head} List: {p_str}\n")
                sys.stderr.flush()

            elif fn_name == "invoke_subagent":
                sub_role = (args.get("alias") or args.get("role") or actual_alias) if isinstance(args, dict) else actual_alias
                sub_task = (args.get("prompt") or args.get("task") or "") if isinstance(args, dict) else ""
                sys.stderr.write(f"\n{c_tool_head} Subagent [{sub_role}]: {sub_task}\n")
                sys.stderr.flush()

            elif fn_name == "swarm_fanout":
                task_str = (args.get("prompt") or args.get("task") or "") if isinstance(args, dict) else ""
                sys.stderr.write(f"\n{c_tool_head} Swarm Fanout: {task_str}\n")
                sys.stderr.flush()

            else:
                sys.stderr.write(f"\n{c_tool_head} Invoking tool: {c_tool_call}{fn_name}{c_reset}\n")
                sys.stderr.flush()

            is_error = False
            result_content: Any = None
            interrupted_during_tool = False

            # Configure dynamic status spinner for long running tools
            tool_sp_msg: Optional[str] = None
            tool_sp_statuses: Optional[List[str]] = None

            if fn_name == "run_command":
                cmd_str = args.get("command", "") if isinstance(args, dict) else str(args)
                cmd_display = cmd_str if len(cmd_str) <= 40 else cmd_str[:37] + "..."
                tool_sp_msg = f"Executing: {cmd_display}"
                tool_sp_statuses = [
                    "Running subprocess...",
                    "Executing command...",
                    "Streaming buffer...",
                ]
            elif fn_name == "invoke_subagent":
                sub_role = (args.get("alias") or args.get("role") or actual_alias) if isinstance(args, dict) else actual_alias
                sub_task = (args.get("prompt") or args.get("task") or "") if isinstance(args, dict) else ""
                sub_display = sub_task if len(sub_task) <= 35 else sub_task[:32] + "..."
                tool_sp_msg = f"Subagent [{sub_role}]"
                tool_sp_statuses = [
                    f"Dispatching subagent ({sub_display})...",
                    "Running subagent loop...",
                    "Evaluating subagent results...",
                    "Awaiting completion...",
                ]
            elif fn_name == "swarm_fanout":
                tool_sp_msg = "Swarm coordinating"
                tool_sp_statuses = [
                    "Architect designing...",
                    "Coder implementing...",
                    "Auditor reviewing...",
                    "Synthesizing consensus...",
                ]
            elif mcp_reg and mcp_reg.has_tool(fn_name):
                tool_sp_msg = f"Tool {fn_name}"
                tool_sp_statuses = [
                    "Executing remote MCP tool...",
                    "Awaiting response...",
                ]

            def _dispatch_tool_action() -> Tuple[Any, bool]:
                native = dispatch_native(fn_name, args if isinstance(args, dict) else {})
                if native is not None:
                    return native, False
                if native_reg and native_reg.has_tool(fn_name):
                    tool_context = {
                        "subagent_depth": subagent_depth,
                        "cwd": target_cwd,
                        "tier": resolved_tier or None,
                        "alias": actual_alias,
                    }
                    try:
                        res = native_reg.dispatch(fn_name, args, context=tool_context)
                        err = isinstance(res, dict) and bool(res.get("isError"))
                        return res, err
                    except Exception as e:
                        return {"isError": True, "error": f"Error executing {fn_name}: {redact(e)}"}, True
                elif mcp_reg:
                    try:
                        res = mcp_reg.dispatch(fn_name, args)
                        err = isinstance(res, dict) and bool(res.get("isError"))
                        return res, err
                    except Exception as e:
                        return {"isError": True, "error": f"Error executing {fn_name}: {redact(e)}"}, True
                else:
                    return {"isError": True, "error": f"Tool '{fn_name}' not available to execute"}, True

            try:
                if tool_sp_msg is not None:
                    with ThinkingSpinner(message=tool_sp_msg, status_messages=tool_sp_statuses):
                        result_content, is_error = _dispatch_tool_action()
                else:
                    result_content, is_error = _dispatch_tool_action()
            except KeyboardInterrupt:
                interrupted_during_tool = True

            if interrupted_during_tool:
                try:
                    steer_input = prompt_input(
                        "\n[Interrupted] Steer in-flight (enter instruction to redirect agent, or press Enter to cancel turn): "
                    ).strip()
                except (KeyboardInterrupt, EOFError):
                    steer_input = ""

                obs_text = "[Tool execution interrupted by developer]"
                if use_prompt_tool_adapter:
                    tool_msg = {
                        "role": "user",
                        "content": format_tool_observation(fn_name, obs_text, call_id=call_id),
                    }
                else:
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": obs_text,
                    }
                messages.append(tool_msg)
                current_turn_msgs.append(tool_msg)
                tool_executions.append({
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": args,
                    "result": obs_text,
                    "is_error": True,
                })

                if steer_input:
                    steering_directive = steer_input
                    for rem_tc in tool_calls[tc_idx + 1:]:
                        r_id = rem_tc.get("id", "")
                        r_fn = rem_tc.get("function", {}).get("name", "")
                        r_args = rem_tc.get("function", {}).get("arguments", "{}")
                        r_obs = "[Tool execution skipped by developer for steering]"
                        if use_prompt_tool_adapter:
                            r_msg = {"role": "user", "content": format_tool_observation(r_fn, r_obs, call_id=r_id)}
                        else:
                            r_msg = {"role": "tool", "tool_call_id": r_id, "name": r_fn, "content": r_obs}
                        messages.append(r_msg)
                        current_turn_msgs.append(r_msg)
                        tool_executions.append({
                            "call_id": r_id,
                            "name": r_fn,
                            "arguments": r_args,
                            "result": r_obs,
                            "is_error": False,
                        })
                    break
                else:
                    turn_aborted = True
                    break

            if not is_error and diff_path is not None:
                abs_path_target = os.path.abspath(os.path.join(target_cwd, diff_path))
                if os.path.isfile(abs_path_target):
                    try:
                        with open(abs_path_target, "r", encoding="utf-8", errors="replace") as f_new:
                            new_file_content = f_new.read()
                        if orig_file_content != new_file_content:
                            print_unified_diff(diff_path, orig_file_content or "", new_file_content)
                    except Exception:
                        pass

            if fn_name == "run_command":
                exit_code = 0
                out_text = ""
                if isinstance(result_content, dict):
                    exit_code = result_content.get("exit_code", 1 if is_error else 0)
                    out_text = (result_content.get("stdout") or result_content.get("stderr") or result_content.get("error") or "").strip()
                else:
                    out_text = str(result_content).strip()
                    if is_error:
                        exit_code = 1

                if out_text:
                    out_lines = out_text.splitlines()
                    if len(out_lines) > 8:
                        summary = "\n".join("  " + l for l in out_lines[:6]) + f"\n  ... [{len(out_lines) - 6} lines truncated]"
                    else:
                        summary = "\n".join("  " + l for l in out_lines)
                    sys.stderr.write(f"{summary}\n")
                sys.stderr.write(f"  Exit code: {exit_code}\n")
                sys.stderr.flush()

            elif fn_name == "grep_search":
                match_count = 0
                if isinstance(result_content, str):
                    if not result_content.startswith("No matches") and not result_content.startswith("Path not found"):
                        match_count = len([l for l in result_content.splitlines() if not l.startswith("[TRUNCATED") and ":" in l])
                sys.stderr.write(f"  Match count: {match_count}\n")
                sys.stderr.flush()

            elif fn_name == "find_files":
                match_count = 0
                if isinstance(result_content, str):
                    if not result_content.startswith("No files matching") and not result_content.startswith("Path not found"):
                        match_count = len([l for l in result_content.splitlines() if not l.startswith("[TRUNCATED") and l.strip()])
                sys.stderr.write(f"  Match count: {match_count}\n")
                sys.stderr.flush()

            elif fn_name == "list_dir":
                entry_count = 0
                if isinstance(result_content, str):
                    if not result_content.endswith("(empty directory)") and not result_content.startswith("Directory not found") and not result_content.startswith("Path is not a directory"):
                        lines_out = [l for l in result_content.splitlines() if l.strip()]
                        entry_count = max(0, len(lines_out) - 1)
                sys.stderr.write(f"  Match count: {entry_count}\n")
                sys.stderr.flush()

            checkpointer.scratchpad.record_tool_invocation(fn_name, args, result_content, is_error=is_error)

            if use_prompt_tool_adapter:
                tool_msg = {
                    "role": "user",
                    "content": format_tool_observation(fn_name, result_content, call_id=call_id),
                }
            else:
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": tool_result_text(result_content),
                }
            messages.append(tool_msg)
            current_turn_msgs.append(tool_msg)

            tool_executions.append({
                "call_id": call_id,
                "name": fn_name,
                "arguments": args,
                "result": result_content,
                "is_error": is_error,
            })

        if turn_aborted:
            checkpointer.transition("COMPLETED")
            checkpointer.final_response = "[Turn aborted by developer]"
            checkpointer.save()
            return "[Turn aborted by developer]"

        if steering_directive:
            directive_msg = {
                "role": "user",
                "content": f"[IN-FLIGHT STEERING DIRECTIVE]: {steering_directive}",
            }
            messages.append(directive_msg)
            current_turn_msgs.append(directive_msg)

        if tool_executions:
            ledger.append_turn(
                role="tool",
                content="",
                tool_results=tool_executions
            )
        if steering_directive:
            ledger.append_turn(
                role="user",
                content=f"[IN-FLIGHT STEERING DIRECTIVE]: {steering_directive}"
            )

        turn_groups.append(current_turn_msgs)
        checkpointer.transition("OBSERVING")
        checkpointer.record_turn(turn_idx + 1, message, tool_executions)
        turn_idx += 1

    sys.stderr.write(f"\n{c_tool_head} Reached maximum iterations ({max_turns}).\n")
    checkpointer.transition("MAX_TURNS")
    last_content = messages[-1].get("content", "Agent loop reached maximum turns without termination.")
    checkpointer.final_response = last_content
    checkpointer.save()
    return last_content


class HydraReplCompleter:
    """Tab autocompleter for Hydra REPL (WO-08)."""

    COMMANDS = [
        "/model",
        "/tier",
        "/steer",
        "/retry",
        "/skip",
        "/context",
        "/effort",
        "/heat",
        "/window",
        "/strategy",
        "/swarm",
        "/auth",
        "/diff",
        "/tokens",
        "/undo",
        "/compact",
        "/retrieve",
        "/status",
        "/history",
        "/clear",
        "/new",
        "/summon",
        "/stack",
        "/skills",
        "/route",
        "/help",
        "/exit",
        "/quit",
        "/q",
    ]
    TIERS = ["free", "local", "paid", "frontier", "none"]
    STEER_OPTS = ["on", "off", "status"]

    def __init__(self, model_aliases: Optional[List[str]] = None):
        if model_aliases is None:
            from hydra_cli.config import MODEL_MAP
            self.model_aliases = sorted(list(MODEL_MAP.keys()))
        else:
            self.model_aliases = sorted(list(model_aliases))

    def complete(self, text: str, state: int) -> Optional[str]:
        line_buffer = ""
        try:
            import readline
            line_buffer = readline.get_line_buffer()
        except Exception:
            pass

        candidates = self.get_candidates(text, line_buffer)
        if state < len(candidates):
            return candidates[state]
        return None

    def get_candidates(self, text: str, line_buffer: str = "") -> List[str]:
        raw_line = line_buffer if line_buffer else text
        line = raw_line.strip()
        tokens = line.split()

        if not line or (len(tokens) <= 1 and not raw_line.endswith(" ")):
            prefix = tokens[0] if tokens else text
            return [c for c in self.COMMANDS if c.startswith(prefix)]

        first = tokens[0].lower()
        if first in ("/model", "/summon"):
            marker = "/model" if first == "/model" else "/summon"
            remainder = raw_line[raw_line.find(marker) + len(marker):].lstrip()
            pool = self.model_aliases
            pool_lower = {item.lower() for item in pool}
            heads = [name for name in MODEL_PICKER if name.lower() in pool_lower]
            if not remainder:
                return heads or list(pool)
            hits = [name for name in heads if name.lower().startswith(remainder.lower())]
            extra = [
                name for name in pool
                if name.lower().startswith(remainder.lower()) and name not in hits
            ]
            return hits + extra
        elif first == "/tier":
            remainder = raw_line[raw_line.find("/tier") + len("/tier"):].lstrip()
            return [t for t in self.TIERS if t.lower().startswith(remainder.lower())]
        elif first == "/steer":
            remainder = raw_line[raw_line.find("/steer") + len("/steer"):].lstrip()
            return [s for s in self.STEER_OPTS if s.lower().startswith(remainder.lower())]
        elif first == "/auth":
            remainder = raw_line[raw_line.find("/auth") + len("/auth"):].lstrip()
            if "status".startswith(remainder.lower()):
                return ["status"]
        elif first == "/context":
            remainder = raw_line[raw_line.find("/context") + len("/context"):].lstrip()
            if "dump".startswith(remainder.lower()):
                return ["dump"]
        elif first == "/dialect":
            remainder = raw_line[raw_line.find("/dialect") + len("/dialect"):].lstrip()
            return [d for d in DIALECTS if d.startswith(remainder.lower())]
        elif first == "/effort":
            remainder = raw_line[raw_line.find("/effort") + len("/effort"):].lstrip()
            return [e for e in EFFORT_LEVELS if e.startswith(remainder.lower())]
        elif first == "/strategy":
            remainder = raw_line[raw_line.find("/strategy") + len("/strategy"):].lstrip()
            return [s for s in CONTEXT_STRATEGIES if s.startswith(remainder.lower())]
        elif first == "/heat":
            remainder = raw_line[raw_line.find("/heat") + len("/heat"):].lstrip()
            return [h for h in ("off", "0", "0.2", "0.7", "1") if h.startswith(remainder.lower())]
        elif first == "/window":
            remainder = raw_line[raw_line.find("/window") + len("/window"):].lstrip()
            return [w for w in ("128k", "200k", "1m") if w.startswith(remainder.lower())]

        all_options = self.COMMANDS + self.model_aliases + self.TIERS
        return [opt for opt in all_options if opt.lower().startswith(text.lower())]


def setup_readline_completer(completer: Optional[HydraReplCompleter] = None) -> Optional[Any]:
    """Setup standard library readline completer with graceful fallback."""
    try:
        import readline
    except ImportError:
        try:
            import pyreadline3 as readline
        except ImportError:
            return None

    comp = completer or HydraReplCompleter()
    try:
        readline.set_completer(comp.complete)
        if "libedit" in getattr(readline, "__doc__", ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        return comp
    except Exception:
        return None


REPL_COMMAND_HELP: List[Tuple[str, str]] = [
    ("/model", "alias picker; provider id listed here"),
    ("/models", "same picker as /model"),
    ("/banner", "reprint the hydra splash once"),
    ("/summon", "inference alias Alice calls on a gap"),
    ("/stack", "live paths for hydra, alice, easylm, progen"),
    ("/skills", "written skill index"),
    ("/route", "last local gate action and summon target"),
    ("/tier", "switch tier: free, local, paid, frontier, none"),
    ("/steer", "toggle step-by-step tool confirmation"),
    ("/skip", "skip the next tool call"),
    ("/retry", "retry the previous turn with guidance"),
    ("/context", "context token usage; dump shows the payload"),
    ("/effort", "reasoning effort: low, medium, high, xhigh, max"),
    ("/heat", "sampling heat; off omits temperature"),
    ("/system", "show or set the session system prompt"),
    ("/window", "context window in tokens, or 128k / 1m"),
    ("/strategy", "recall or sliding"),
    ("/compact", "flush the ledger and unload the live window"),
    ("/retrieve", "pull matching ledger turns by keyword"),
    ("/diff", "colorized git diff of the worktree"),
    ("/undo", "revert the last modified file"),
    ("/auth", "provider key wizard; status shows keys"),
    ("/swarm", "parallel Architect, Coder, Auditor swarm"),
    ("/tokens", "alias for /context"),
    ("/history", "turn-by-turn history"),
    ("/status", "session, model, tier, rules"),
    ("/clear", "reset session memory"),
    ("/new", "reset session memory"),
    ("/help", "slash command and key reference"),
    ("/exit", "leave hydra"),
]


def _create_tui() -> Optional[Any]:
    """Composer TUI for real terminals; None under pipes, tests, HYDRA_CLASSIC_UI=1, or without prompt_toolkit."""
    if os.environ.get("HYDRA_CLASSIC_UI", "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (AttributeError, ValueError):
        return None
    try:
        from hydra_cli.tui import HydraTUI
    except ImportError:
        return None
    completer = HydraReplCompleter()
    return HydraTUI(
        commands=REPL_COMMAND_HELP,
        complete=completer.get_candidates,
        history_path=os.path.join(hydra_home(), "agent_history"),
    )


def run_interactive_agent(
    alias: str = DEFAULT_ORCHESTRATOR_MODEL,
    tier: Optional[str] = None,
    system_prompt: Optional[str] = None,
    registry: Optional[Any] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    steer_mode: bool = False,
) -> int:
    """
    Launch interactive terminal coding REPL (Cursor & Antigravity style) with slash commands.
    """
    from hydra_cli._version import __version__

    setup_readline_completer()

    color_on = supports_color()
    c_head = GREEN_BOLD if color_on else ""
    c_bright = GREEN_BRIGHT if color_on else ""
    c_mid = GREEN_MID if color_on else ""
    c_reset = RESET if color_on else ""

    target_cwd = os.path.abspath(cwd or os.getcwd())
    active_alias = DEFAULT_ORCHESTRATOR_MODEL if alias in ("agent", "auto", "orchestrator") else alias
    active_tier = tier
    active_session_id = session_id or f"session_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    active_steer_mode = steer_mode
    active_dialect = "progen"
    active_effort: Optional[str] = None
    boot_route = resolve_route(active_alias)
    active_runner = boot_route.get("runner")
    if active_runner == "alice" and os.environ.get("ALICE_NO_FALLBACK") == "1":
        active_summon = "none"
    else:
        active_summon = DEFAULT_AGENT_MODEL if active_runner == "alice" else active_alias
    if boot_route.get("effort"):
        active_effort = boot_route["effort"]
    last_gate = {"action": "", "route": ""}
    active_heat: Optional[float] = temperature
    active_window: Optional[int] = None
    active_strategy = "recall"
    active_system_prompt = (
        DEFAULT_AGENT_SYSTEM_PROMPT
        if (not system_prompt or system_prompt == DEFAULT_SYSTEM_PROMPT)
        else system_prompt
    )
    skip_next_tool = False
    turns_history: List[Tuple[str, str]] = []
    active_ledger = SessionContextLedger(session_id=active_session_id)

    rules_msg = detect_project_rules(target_cwd)
    rules_label = "Detected (AGENTS.md / .cursorrules / CLAUDE.md)" if rules_msg else "None"

    tui = _create_tui()
    if tui is not None:
        from hydra_cli.ui import play_launch_banner

        play_launch_banner(
            version=__version__,
            info=[
                ("model", session_model_label(active_alias, active_runner, active_summon)),
                ("cwd", target_cwd),
                ("rules", rules_label),
                ("session", active_session_id),
            ],
        )
        sys.stdout.write("\n")
        sys.stdout.flush()
        tui.start()
    else:
        sys.stdout.write(f"\n{c_head}================================================================================{c_reset}\n")
        sys.stdout.write(f"  {c_bright}HYDRA CODING AGENT{c_reset} // Sovereign Autonomous Shell (v{__version__})\n")
        sys.stdout.write(f"  Model: {c_mid}{session_model_label(active_alias, active_runner, active_summon)}{c_reset} | CWD: {target_cwd}\n")
        sys.stdout.write(f"  Project Rules: {rules_label}\n")
        sys.stdout.write(f"  Type {c_bright}/help{c_reset} for slash commands or enter your instruction to begin.\n")
        sys.stdout.write(f"{c_head}================================================================================{c_reset}\n\n")
        sys.stdout.flush()

    last_interrupt_time = 0.0

    def _run_turn(prompt_text: str) -> str:
        streamed = {"open": False}

        def emit(text: str) -> None:
            if not text:
                return
            if not streamed["open"]:
                streamed["open"] = True
                if tui is not None:
                    tui.begin_stream()
            if tui is not None:
                tui.write_token(text)
            else:
                sys.stdout.write(text)
                sys.stdout.flush()

        def _stream_width() -> int:
            if tui is not None:
                return tui._width()
            return shutil.get_terminal_size((100, 40)).columns

        wrapper = StreamWrap(emit, width_fn=_stream_width)

        def on_token(text: str) -> None:
            wrapper.feed(text)

        loop_alias = active_alias
        loop_system = active_system_prompt
        loop_effort = active_effort
        if active_runner == "alice":
            from hydra_cli.alice_gate import consult

            prior = turns_history[-1] if turns_history else None
            decision = consult(
                prompt_text,
                summon_alias=active_summon,
                has_referent=prior is not None,
                prior=prior,
            )
            last_gate["action"] = decision.action
            last_gate["route"] = decision.route
            if decision.action != "summon":
                text = decision.text
                active_ledger.append_turn("user", prompt_text)
                active_ledger.append_turn("assistant", text)
                if tui is not None:
                    tui.print_answer(text)
                else:
                    sys.stdout.write(f"\n{text}\n\n")
                    sys.stdout.flush()
                return text
            loop_alias = active_summon
            loop_effort = resolve_route(active_summon).get("effort") or active_effort
            if decision.discovery:
                loop_system = active_system_prompt + "\n\n" + decision.discovery

        try:
            answer = run_agent_loop(
                alias=loop_alias,
                prompt=prompt_text,
                system_prompt=loop_system,
                registry=registry,
                temperature=active_heat,
                max_tokens=max_tokens,
                session_id=active_session_id,
                resume_session=True,
                tier=active_tier,
                cwd=target_cwd,
                steer_mode=active_steer_mode,
                skip_next_tool=skip_next_tool,
                ledger=active_ledger,
                effort=loop_effort,
                dialect=active_dialect,
                strategy=active_strategy,
                on_token=on_token,
                max_context_chars=(active_window or get_context_window(loop_alias)) * 4,
            )
        finally:
            wrapper.finish()
            if streamed["open"]:
                if tui is not None:
                    tui.end_stream()
                else:
                    sys.stdout.write("\n\n")
                    sys.stdout.flush()
        if not streamed["open"]:
            if tui is not None:
                tui.print_answer(answer)
            else:
                sys.stdout.write(f"\n{answer}\n\n")
                sys.stdout.flush()
        return answer

    def _budget_alias() -> str:
        return active_summon if active_runner == "alice" else active_alias

    def _reset_session(verb: str) -> None:
        nonlocal active_session_id, active_ledger
        active_session_id = f"session_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        turns_history.clear()
        active_ledger = SessionContextLedger(session_id=active_session_id)
        last_gate["action"] = ""
        last_gate["route"] = ""
        sys.stdout.write(f"{c_mid}[{verb} session history. New session: {active_session_id}]{c_reset}\n")
        sys.stdout.flush()

    while True:
        if sys.stdin.isatty():
            sys_len = len(active_system_prompt)
            rules_len = len(rules_msg or "")
            turns_chars = sum(len(p) + len(a) for p, a in turns_history)
            cp = SessionCheckpointer.load(active_session_id)
            scratch_chars = len(cp.scratchpad.format_for_context()) if cp else 0
            total_chars = sys_len + rules_len + turns_chars + scratch_chars
            est_tokens = total_chars // 4
            max_budget = active_window or get_context_window(_budget_alias())
            if tui is not None:
                tui.update_status(
                    model=session_model_label(active_alias, active_runner, active_summon),
                    tier="",
                    tokens=est_tokens,
                    budget=max_budget,
                    turns=len(turns_history),
                    step_mode=active_steer_mode,
                    dialect=register_label(active_dialect),
                    effort="",
                    heat="",
                    strategy=context_label(active_strategy),
                )
            else:
                box = render_prompt_box(model=session_model_label(active_alias, active_runner, active_summon), version=__version__, est_tokens=est_tokens, max_budget=max_budget, turns=len(turns_history))
                sys.stdout.write(f"\n{box}\n")
                sys.stdout.flush()

        shown = "alice" if active_runner == "alice" else active_alias
        prompt_label = f"{c_bright}hydra-agent [{shown}:{active_tier or 'frontier'}]{c_reset}> "
        try:
            if tui is not None:
                try:
                    line = tui.read_line()
                except EOFError:
                    sys.stdout.write(f"\n{c_mid}Exiting Hydra agent session.{c_reset}\n")
                    sys.stdout.flush()
                    break
            else:
                sys.stdout.write(prompt_label)
                sys.stdout.flush()
                try:
                    line = input('')
                except (EOFError, StopIteration):
                    sys.stdout.write(f"\n{c_mid}Exiting Hydra agent session.{c_reset}\n")
                    sys.stdout.flush()
                    break
                
            line = clean_pasted_text(line).strip()
            
            if not line and tui is None:
                try:
                    second = input("... ")
                    if not second.strip():
                        active_steer_mode = not active_steer_mode
                        state_str = "active" if active_steer_mode else "disabled"
                        sys.stdout.write(f"\n{c_bright}[Steering directive mode toggled: step-by-step confirmation {state_str}]{c_reset}\n")
                        sys.stdout.flush()
                except (EOFError, StopIteration):
                    break
                continue
                
            last_interrupt_time = 0.0
        except KeyboardInterrupt:
            now = time.time()
            if now - last_interrupt_time < 1.5:
                sys.stdout.write(f"\n{c_mid}Exiting Hydra agent session.{c_reset}\n")
                sys.stdout.flush()
                break
            last_interrupt_time = now
            key_name = "Escape" if tui is not None else "Ctrl+C"
            sys.stdout.write(f"\n{c_mid}{key_name} interrupted. Press {key_name} again within 1.5s to exit REPL.{c_reset}\n")
            sys.stdout.flush()
            continue
        except EOFError:
            sys.stdout.write(f"\n{c_mid}Exiting Hydra agent session.{c_reset}\n")
            sys.stdout.flush()
            break

        if not line:
            continue

        # Handle slash commands
        if line.startswith("/"):
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            raw_cmd = cmd
            cmd = COMMAND_TYPOS.get(cmd, cmd)
            if cmd != raw_cmd:
                sys.stdout.write(f"{c_mid}[normalized : {raw_cmd} -> {cmd}]{c_reset}\n")

            if cmd in ("/exit", "/quit", "/q"):
                sys.stdout.write(f"\n{c_mid}Exiting Hydra agent session.{c_reset}\n")
                sys.stdout.flush()
                break

            elif cmd == "/clear":
                _reset_session("Cleared")

            elif cmd == "/new":
                _reset_session("New")

            elif cmd == "/banner":
                from hydra_cli.ui import print_banner
                print_banner(detailed=True, version=__version__)
                sys.stdout.flush()

            elif cmd == "/models":
                # Same picker as bare /model — keep slash surface familiar.
                sys.stdout.write(format_model_picker(active_alias, active_summon) + "\n")
                sys.stdout.write(
                    f"{c_mid}[Current active model: {session_model_label(active_alias, active_runner, active_summon)}]{c_reset}\n"
                )
                sys.stdout.flush()

            elif cmd == "/system":
                if not arg:
                    preview = active_system_prompt.replace("\n", " ")
                    if len(preview) > 160:
                        preview = preview[:157] + "..."
                    sys.stdout.write(f"{c_mid}[System prompt: {preview}]{c_reset}\n")
                    sys.stdout.write(f"{c_mid}[Usage: /system <text>  or  /system reset]{c_reset}\n")
                elif arg.lower() in ("reset", "default", "clear"):
                    active_system_prompt = DEFAULT_AGENT_SYSTEM_PROMPT
                    sys.stdout.write(f"{c_mid}[System prompt reset to agent default]{c_reset}\n")
                else:
                    active_system_prompt = arg
                    sys.stdout.write(f"{c_mid}[System prompt updated]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/auth":
                if arg.lower() == "status":
                    from hydra_cli.auth import print_auth_status
                    print_auth_status()
                else:
                    from hydra_cli.auth import auth_wizard
                    if tui is not None:
                        with tui.suspended():
                            auth_wizard()
                    else:
                        auth_wizard()
                sys.stdout.flush()

            elif cmd == "/diff":
                try:
                    proc = subprocess.run(["git", "diff"], cwd=target_cwd, capture_output=True, text=True, timeout=10)
                    diff_text = proc.stdout.strip()
                    if diff_text:
                        sys.stdout.write(f"\n{c_head}--- Git Diff ({target_cwd}) ---{c_reset}\n")
                        print_unified_diff("worktree", "", diff_text, raw_diff=True)
                        sys.stdout.write("\n")
                    else:
                        sys.stdout.write(f"{c_mid}[No uncommitted changes in git worktree]{c_reset}\n")
                except Exception as exc:
                    sys.stdout.write(f"{c_mid}[git diff unavailable: {exc}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd in ("/tokens", "/context"):
                sys_len = len(active_system_prompt)
                rules_len = len(rules_msg or "")
                turns_chars = sum(len(p) + len(a) for p, a in turns_history)
                cp = SessionCheckpointer.load(active_session_id)
                scratch_chars = len(cp.scratchpad.format_for_context()) if cp else 0
                total_chars = sys_len + rules_len + turns_chars + scratch_chars
                est_tokens = total_chars // 4
                max_budget = active_window or get_context_window(_budget_alias())
                pct = (est_tokens / max_budget) * 100
                heat_label = "off" if active_heat is None else f"{active_heat:g}"
                sys.stdout.write(f"\n{c_head}--- Active Session Context Size ---{c_reset}\n")
                sys.stdout.write(f"  Estimated Tokens : ~{est_tokens:,} / {max_budget:,} ({pct:.2f}%)\n")
                sys.stdout.write(f"  Context Window   : {max_budget:,} tokens\n")
                sys.stdout.write(f"  Effort           : {active_effort or 'route default'}\n")
                sys.stdout.write(f"  Sampling Heat    : {heat_label}\n")
                sys.stdout.write(f"  Turns Count      : {len(turns_history)}\n")
                sys.stdout.write(f"  System & Rules   : ~{(sys_len + rules_len) // 4:,} tokens\n")
                sys.stdout.write(f"  Scratchpad       : ~{scratch_chars // 4:,} tokens\n")
                if cp and getattr(cp, "bounded_messages", None):
                    sys.stdout.write(f"  Bounded Messages : {len(cp.bounded_messages)} active message items\n")
                sys.stdout.write("\n")
                if arg.lower() == "dump":
                    sys.stdout.write(f"{c_head}--- Current Bounded Messages Payload Dump ---{c_reset}\n")
                    if cp and getattr(cp, "bounded_messages", None):
                        dump_text = json.dumps(cp.bounded_messages, indent=2, ensure_ascii=False)
                    else:
                        dump_text = json.dumps([
                            {"role": "system", "content": build_cached_system_prompt(active_system_prompt)},
                        ], indent=2, ensure_ascii=False)
                    sys.stdout.write(dump_text + "\n\n")
                sys.stdout.flush()

            elif cmd == "/steer":
                if not arg:
                    active_steer_mode = not active_steer_mode
                    state_lbl = "enabled" if active_steer_mode else "disabled"
                    sys.stdout.write(f"{c_mid}[Steer mode toggled: {state_lbl}]{c_reset}\n")
                elif arg.lower() == "status":
                    state_lbl = "enabled" if active_steer_mode else "disabled"
                    sys.stdout.write(f"{c_mid}[Steer mode is currently {state_lbl}]{c_reset}\n")
                elif arg.lower() == "on":
                    active_steer_mode = True
                    sys.stdout.write(f"{c_mid}[Steer mode enabled (step-by-step confirmation on)]{c_reset}\n")
                elif arg.lower() == "off":
                    active_steer_mode = False
                    sys.stdout.write(f"{c_mid}[Steer mode disabled]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/skip":
                skip_next_tool = True
                sys.stdout.write(f"{c_mid}[Next tool execution set to skipped]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/retry":
                if not turns_history:
                    sys.stdout.write(f"{c_mid}[No previous turn to retry in active session]{c_reset}\n")
                    sys.stdout.flush()
                    continue
                last_p, _ = turns_history[-1]
                retry_p = f"{last_p}\n[Additional Guidance]: {arg}" if arg else last_p
                sys.stdout.write(f"{c_mid}[Retrying previous turn with: {retry_p}]{c_reset}\n")
                sys.stdout.flush()
                # Run retry directly
                try:
                    answer = _run_turn(retry_p)
                    skip_next_tool = False
                    turns_history.append((retry_p, answer))
                    sys.stdout.flush()
                except KeyboardInterrupt:
                    sys.stderr.write(f"\n{c_mid}^C Agent turn aborted by user.{c_reset}\n\n")
                    sys.stderr.flush()
                except Exception as exc:
                    sys.stderr.write(f"\n[Agent Error]: {redact(exc)}\n\n")
                    sys.stderr.flush()
                continue

            elif cmd == "/swarm":
                if not arg:
                    sys.stdout.write(f"{c_mid}[Usage: /swarm <task prompt>]{c_reset}\n")
                    sys.stdout.flush()
                    continue
                from hydra_cli.swarm import execute_swarm
                sys.stdout.write(f"\n{c_head}--- Swarm Consensus Execution ---{c_reset}\n")
                sys.stdout.flush()
                try:
                    s_results = execute_swarm(task=arg, tier=active_tier, json_output=False)
                    for r in s_results:
                        st = "ok" if not r.error else f"error ({r.error})"
                        sys.stdout.write(f"\n### HEAD: {r.title} ({r.role}) Â· [{st}]\n{r.content if not r.error else r.error}\n")
                    sys.stdout.write("\n")
                except Exception as exc:
                    sys.stdout.write(f"[Swarm Error]: {exc}\n")
                sys.stdout.flush()
                continue

            elif cmd == "/undo":
                if arg:
                    target_file = arg
                    try:
                        subprocess.run(["git", "checkout", "--", target_file], cwd=target_cwd, capture_output=True, text=True, check=True)
                        sys.stdout.write(f"{c_mid}[Reverted file change via git checkout: {target_file}]{c_reset}\n")
                    except Exception as exc:
                        sys.stdout.write(f"{c_mid}[Failed to revert {target_file}: {exc}]{c_reset}\n")
                else:
                    try:
                        st_proc = subprocess.run(["git", "status", "--porcelain"], cwd=target_cwd, capture_output=True, text=True)
                        lines = [l.strip() for l in st_proc.stdout.splitlines() if l.strip()]
                        if not lines:
                            sys.stdout.write(f"{c_mid}[No uncommitted file changes to revert]{c_reset}\n")
                        else:
                            last_file = lines[-1].split()[-1]
                            subprocess.run(["git", "checkout", "--", last_file], cwd=target_cwd, capture_output=True, text=True, check=True)
                            sys.stdout.write(f"{c_mid}[Reverted last file change via git checkout: {last_file}]{c_reset}\n")
                    except Exception as exc:
                        sys.stdout.write(f"{c_mid}[Failed to revert changes: {exc}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/history":
                if not turns_history:
                    sys.stdout.write(f"{c_mid}[No turns recorded in active session]{c_reset}\n")
                else:
                    sys.stdout.write(f"\n{c_head}--- Active Session History ({len(turns_history)} turns) ---{c_reset}\n")
                    for i, (p, a) in enumerate(turns_history, 1):
                        p_trunc = p[:80] + ("..." if len(p) > 80 else "")
                        a_trunc = a[:120] + ("..." if len(a) > 120 else "")
                        sys.stdout.write(f"  [{i}] Instruction: {p_trunc}\n      Outcome: {a_trunc}\n")
                    sys.stdout.write("\n")
                sys.stdout.flush()

            elif cmd == "/status":
                heat_label = "off" if active_heat is None else f"{active_heat:g}"
                window_label = active_window or get_context_window(_budget_alias())
                sys.stdout.write(f"\n{c_head}--- Hydra ---{c_reset}\n")
                sys.stdout.write(f"  alias   {session_model_label(active_alias, active_runner, active_summon)}\n")
                sys.stdout.write(f"  effort  {active_effort or 'route default'}\n")
                sys.stdout.write(f"  heat    {heat_label}\n")
                sys.stdout.write(f"  window  {window_label:,} tokens\n")
                sys.stdout.write(f"  steer   {'on' if active_steer_mode else 'off'}\n")
                sys.stdout.write(f"  turns   {len(turns_history)}\n")
                sys.stdout.write(f"  cwd     {target_cwd}\n\n")
                sys.stdout.flush()

            elif cmd == "/model":
                if not arg:
                    sys.stdout.write(format_model_picker(active_alias, active_summon) + "\n")
                    sys.stdout.write(
                        f"{c_mid}[Current active model: {session_model_label(active_alias, active_runner, active_summon)}]{c_reset}\n"
                    )
                else:
                    choice = resolve_model_choice(arg)
                    if choice is None:
                        sys.stdout.write(
                            f"{c_mid}[Unknown alias '{arg}'. Select a number or a catalog alias.]{c_reset}\n"
                        )
                        sys.stdout.write(format_model_picker(active_alias, active_summon) + "\n")
                    else:
                        route = resolve_route(choice)
                        active_alias = choice
                        if route.get("runner") == "alice":
                            active_runner = "alice"
                            summon_id = "disabled (standalone)" if active_summon in ("none", "off", "disabled", "alone") else resolve_route(active_summon)["model"]
                            sys.stdout.write(f"{c_mid}[Active model set to: alice]{c_reset}\n")
                            sys.stdout.write(f"{c_mid}[Local brain. Summon model: {summon_id}]{c_reset}\n")
                            sys.stdout.write(
                                f"{c_mid}[The next turn searches local rules and Alice before any provider call.]{c_reset}\n"
                            )
                        else:
                            active_runner = None
                            active_summon = choice
                            if route.get("effort"):
                                active_effort = route["effort"]
                            sys.stdout.write(f"{c_mid}[Active model set to: {choice}]{c_reset}\n")
                            sys.stdout.write(f"{c_mid}[Request model: {route['model']}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/summon":
                if not arg:
                    if active_summon in ("none", "off", "disabled", "alone"):
                        sys.stdout.write(f"{c_mid}[Summon model: disabled (standalone Alice)]{c_reset}\n")
                    else:
                        summon_id = resolve_route(active_summon)["model"]
                        sys.stdout.write(f"{c_mid}[Summon model: {active_summon} -> {summon_id}]{c_reset}\n")
                    sys.stdout.write(f"{c_mid}[Usage: /summon <number or alias | none>]{c_reset}\n")
                elif arg.lower().strip() in ("none", "off", "disabled", "alone"):
                    active_summon = "none"
                    sys.stdout.write(
                        f"{c_mid}[Summon model disabled. Alice operates standalone with zero fallback.]{c_reset}\n"
                    )
                else:
                    choice = resolve_model_choice(arg)
                    route = resolve_route(choice) if choice else None
                    if choice is None or (route and route.get("runner") == "alice"):
                        sys.stdout.write(
                            f"{c_mid}[Summon target '{arg}' refused. Pick an inference alias or 'none'.]{c_reset}\n"
                        )
                    else:
                        active_summon = choice
                        if active_runner != "alice":
                            active_alias = choice
                            active_runner = None
                            if route.get("effort"):
                                active_effort = route["effort"]
                        sys.stdout.write(
                            f"{c_mid}[Summon model set to: {choice} -> {route['model']}]{c_reset}\n"
                        )
                sys.stdout.flush()

            elif cmd == "/stack":
                from hydra_cli.alice_gate import stack_report
                sys.stdout.write(stack_report() + "\n")
                sys.stdout.flush()

            elif cmd == "/skills":
                from hydra_cli.alice_gate import skill_index_report
                sys.stdout.write(skill_index_report() + "\n")
                sys.stdout.flush()

            elif cmd == "/route":
                sys.stdout.write(
                    f"{c_mid}[Last gate: {last_gate['action'] or 'none'} {last_gate['route']}]{c_reset}\n"
                )
                sys.stdout.write(f"{c_mid}[Brain: {active_runner or 'direct'}]{c_reset}\n")
                sys.stdout.write(
                    f"{c_mid}[Summon: {active_summon} -> {resolve_route(active_summon)['model']}]{c_reset}\n"
                )
                sys.stdout.flush()

            elif cmd == "/tier":
                if arg:
                    t_val = arg.lower()
                    if t_val in ("free", "local", "paid", "frontier", "auto", "none"):
                        active_tier = None if t_val in ("none", "auto") else t_val
                        sys.stdout.write(f"{c_mid}[Active tier set to: {active_tier or 'frontier'}]{c_reset}\n")
                    else:
                        sys.stdout.write(f"{c_mid}[Invalid tier '{arg}'. Valid: free, local, paid, frontier, none]{c_reset}\n")
                else:
                    sys.stdout.write(f"{c_mid}[Current active tier: {active_tier or 'frontier (paid)'}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/dialect":
                if not arg:
                    sys.stdout.write(f"{c_mid}[Dialect: {active_dialect}. Valid: {', '.join(DIALECTS)}]{c_reset}\n")
                elif arg.lower() in DIALECTS or arg.lower() == "syntax":
                    active_dialect = "progen" if arg.lower() in ("syntax", "progen") else arg.lower()
                    sys.stdout.write(f"{c_mid}[Dialect set to: {active_dialect}]{c_reset}\n")
                else:
                    sys.stdout.write(f"{c_mid}[Invalid dialect '{arg}'. Valid: {', '.join(DIALECTS)}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/effort":
                if not arg:
                    sys.stdout.write(format_effort_picker(active_effort) + "\n")
                elif arg.lower() in ("off", "none", "default"):
                    active_effort = None
                    sys.stdout.write(f"{c_mid}[Effort set to route default]{c_reset}\n")
                else:
                    choice = resolve_effort_choice(arg)
                    if choice is None:
                        sys.stdout.write(f"{c_mid}[Unknown effort '{arg}'.]{c_reset}\n")
                        sys.stdout.write(format_effort_picker(active_effort) + "\n")
                    else:
                        active_effort = choice
                        sys.stdout.write(f"{c_mid}[Effort set to: {active_effort}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/heat":
                if not arg:
                    sys.stdout.write(format_heat_picker(active_heat) + "\n")
                else:
                    try:
                        active_heat = parse_heat(arg)
                    except ValueError as exc:
                        sys.stdout.write(f"{c_mid}[{exc}]{c_reset}\n")
                        sys.stdout.write(format_heat_picker(active_heat) + "\n")
                    else:
                        label = "off" if active_heat is None else f"{active_heat:g}"
                        sys.stdout.write(f"{c_mid}[Sampling heat set to: {label}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/window":
                if not arg:
                    current = active_window or get_context_window(_budget_alias())
                    sys.stdout.write(f"{c_mid}[Context window: {current:,} tokens]{c_reset}\n")
                elif arg.lower() in ("off", "default", "catalog"):
                    active_window = None
                    sys.stdout.write(f"{c_mid}[Context window set to catalog default]{c_reset}\n")
                else:
                    try:
                        active_window = parse_window(arg)
                    except ValueError as exc:
                        sys.stdout.write(f"{c_mid}[{exc}]{c_reset}\n")
                    else:
                        sys.stdout.write(f"{c_mid}[Context window set to: {active_window:,} tokens]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/strategy":
                if not arg:
                    sys.stdout.write(format_context_picker(active_strategy) + "\n")
                else:
                    choice = resolve_context_mode(arg)
                    if choice is None:
                        sys.stdout.write(f"{c_mid}[Unknown context mode '{arg}'.]{c_reset}\n")
                        sys.stdout.write(format_context_picker(active_strategy) + "\n")
                    else:
                        active_strategy = choice
                        sys.stdout.write(f"{c_mid}[Context strategy set to: {active_strategy}]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/compact":
                summary = active_ledger.compact_session()
                active_strategy = "recall"
                sys.stdout.write(f"{c_mid}[{summary['summary']}]{c_reset}\n")
                sys.stdout.write(f"{c_mid}[Live window set to recall. Ledger turns stay verbatim.]{c_reset}\n")
                sys.stdout.flush()

            elif cmd == "/retrieve":
                words = arg.split() if arg else []
                if not words:
                    sys.stdout.write(f"{c_mid}[Usage: /retrieve <keywords>]{c_reset}\n")
                else:
                    hits = active_ledger.retrieve_verbatim(words, max_turns=3)
                    if not hits:
                        sys.stdout.write(f"{c_mid}[No ledger turns matched]{c_reset}\n")
                    else:
                        sys.stdout.write(f"\n{c_head}--- Retrieved Turns ---{c_reset}\n")
                        for hit in hits:
                            body = str(hit.get("content") or "").strip()
                            sys.stdout.write(f"  {hit.get('role', 'turn')}: {body[:500]}\n")
                        sys.stdout.write("\n")
                sys.stdout.flush()

            elif cmd in ("/help", "/h", "?"):
                sys.stdout.write(f"""
{c_head}HYDRA CODING AGENT SLASH COMMANDS:{c_reset}
  /model [n|alias] Alias picker. The provider id appears only in this list
  /models          Same picker
  /effort [n|level] Reasoning effort: low, medium, high, xhigh, max. off clears it
  /heat [value]    Sampling heat from 0 to 2. off omits temperature
  /status          Alias, effort, heat, window, and working directory
  /system [text]   Show, set, or reset the session system prompt
  /window [size]   Context window in tokens, or 128k / 1m
  /banner          Reprint the hydra splash once (does not change the look)
  /summon [alias]  Inference alias Alice calls when local search misses
  /stack           Live paths for hydra, alice, easylm, and progen
  /skills          Written skill index
  /route           Last gate action, brain, and summon target
  /tier [tier]     Display or switch active tier (free, local, paid, frontier, none)
  /steer [opts]    Toggle or set step-by-step confirmation mode (on/off/status)
  /skip            Skip the next tool call in the next agent turn
  /retry [guide]   Retry the previous turn with optional additional guidance
  /context [dump]  Context token count
  /strategy [mode] recall searches the ledger and pastes matching turns whole. sliding keeps the last few turns whole
  /compact         Flush the ledger to disk and unload the live window. Turn text stays verbatim
  /retrieve <words> Show ledger turns matching keywords
  /diff            Display colorized unified git diff of current changes
  /undo [path]     Revert last modified file or specific file via git checkout
  /auth [status]   Launch interactive provider API key wizard or view status
  /swarm <task>    Execute parallel specialist swarm across Architect, Coder, Auditor
  /tokens          Alias for /context
  /history         View turn-by-turn prompt and outcome history for active session
  /clear           Reset active session memory and initialize a new ledger
  /new             Reset active session memory and initialize a new ledger
  /help, /h, ?     Display this slash command reference guide
  /exit, /quit, /q Exit the Hydra agent REPL session

{c_head}Composer Keys:{c_reset}
  Enter            Send; while a turn runs, queue the message
  Enter twice      Steer: inject the queued message into the running turn
  Ctrl+Enter       Insert a newline; Alt+Enter on POSIX, Ctrl+J or a trailing backslash anywhere
  Ctrl+Space       Dictate: start or stop voice capture; the transcript lands in the box
  Escape           Interrupt the running turn, clear the box, or exit on double press
  Ctrl+C / Ctrl+V  Copy the selection and paste, including the Windows clipboard
  Mouse            Click moves the cursor; drag selects; wheel scrolls history; typing re-pins the prompt
  Tab              Complete slash commands and arguments

{c_head}Native Coding Tools:{c_reset}
  read_file        Read file contents with line slicing and byte limits
  write_file       Create new files or overwrite existing files
  edit_file        Exact search-and-replace with unique-match guard
  list_dir         Directory tree visualization with file sizes
  grep_search      Regex or text search across files with line numbers
  find_files       Glob file search across directories
  run_command      Isolated command execution via zero-trust sandbox
  invoke_subagent  Spawn child agent loop with bounded recursion depth (<= 3)
  swarm_fanout     Coordinate specialist swarm across Architect, Coder, Auditor

""")
                sys.stdout.flush()
            else:
                sys.stdout.write(f"Unknown command '{cmd}'. Type /help for available commands.\n")
                sys.stdout.flush()
            continue

        # Execute instruction via run_agent_loop with Ctrl+C turn abort handling
        try:
            answer = _run_turn(line)
            skip_next_tool = False
            turns_history.append((line, answer))
            sys.stdout.flush()
        except KeyboardInterrupt:
            sys.stderr.write(f"\n{c_mid}^C Agent turn aborted by user.{c_reset}\n\n")
            sys.stderr.flush()
        except Exception as exc:
            sys.stderr.write(f"\n[Agent Error]: {redact(exc)}\n\n")
            sys.stderr.flush()

    if tui is not None:
        tui.close()
    return 0
