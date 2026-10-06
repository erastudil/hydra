"""
Autonomous ReAct agent execution loop with MCP tool calling and multi-turn state.
Zero external dependencies.
"""

from datetime import datetime, timezone
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from hydra_cli.config import DEFAULT_SYSTEM_PROMPT, build_cached_system_prompt, hydra_home, resolve_route
from hydra_cli.providers import (
    ProviderError,
    UsageError,
    adapt_model_for_url,
    completion_timeout,
    describe_endpoint,
    ensure_temperature,
    get_frontier_providers,
    providers_for_model,
    reasoning_fields,
    redact,
)
from hydra_cli.ui import GREEN_BRIGHT, GREEN_MID, RESET, supports_color


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
            inst.final_response = data.get("final_response")
            return inst
        except Exception:
            return None


def _build_bounded_messages(
    messages: List[Dict[str, Any]],
    turn_groups: List[List[Dict[str, Any]]],
    scratchpad: HierarchicalScratchpad,
    max_history_turns: int = 5,
) -> List[Dict[str, Any]]:
    """
    Construct bounded working memory message list (WO-06).
    Keeps system prompt (token 0 cache block) and initial user prompt.
    Compacts older completed tool turn groups into a structured working memory note
    while retaining the most recent `max_history_turns` turns in full detail.
    """
    if len(turn_groups) <= max_history_turns:
        return list(messages)

    system_msg = messages[0]
    user_msg = messages[1]

    recent_groups = turn_groups[-max_history_turns:]
    compact_summary = scratchpad.format_for_context()

    summary_assistant = {
        "role": "assistant",
        "content": (
            f"[ACTIVE WORKING MEMORY & HISTORICAL SCRATCHPAD]\n"
            f"{compact_summary}\n"
            f"Earlier {len(turn_groups) - max_history_turns} tool execution cycles completed successfully."
        ),
    }
    summary_user = {
        "role": "user",
        "content": "Proceed with the next execution step according to active working memory and objectives.",
    }

    bounded = [system_msg, user_msg, summary_assistant, summary_user]
    for group in recent_groups:
        bounded.extend(group)
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
) -> str:
    """
    Execute autonomous agent loop with MCP tool invocation until completion or max turns.
    Includes bounded working memory, hierarchical scratchpads, and persistent session
    statechart checkpointing saved to ~/.hydra/sessions/<session_id>.json (WO-04, WO-06).
    """
    route = resolve_route(alias)
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

    if not checkpointer:
        checkpointer = SessionCheckpointer(
            session_id=clean_id,
            alias=alias,
            model=requested_model,
            task=prompt,
            max_turns=max_turns,
        )

    checkpointer.transition("PLANNING")

    # Isolate immutable invariants at prefix for provider prompt caching (WO-04)
    sys_text = build_cached_system_prompt(system_prompt or DEFAULT_SYSTEM_PROMPT)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": prompt},
    ]

    tools = registry.get_openai_tools() if registry else []
    reasoning = reasoning_fields(route.get("effort"), route.get("reasoning_mode"))
    timeout = completion_timeout(reasoning)

    color_on = supports_color()
    c_tool_head = f"{GREEN_BRIGHT}[HYDRA AGENT]{RESET}" if color_on else "[HYDRA AGENT]"
    c_tool_call = f"{GREEN_MID}" if color_on else ""
    c_reset = RESET if color_on else ""

    order = list(providers)
    turn_groups: List[List[Dict[str, Any]]] = []

    def complete_turn() -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        bounded_messages = _build_bounded_messages(
            messages=messages,
            turn_groups=turn_groups,
            scratchpad=checkpointer.scratchpad,
            max_history_turns=max_history_turns,
        )

        for index, provider in enumerate(order):
            payload: Dict[str, Any] = {
                "model": adapt_model_for_url(provider["url"], requested_model),
                "messages": bounded_messages,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            if reasoning:
                payload["reasoning"] = reasoning
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            try:
                res_json = _fetch_raw_completion(provider["url"], provider["headers"], payload, timeout=timeout)
                if not res_json.get("choices"):
                    raise ProviderError(f"{provider.get('name', 'Provider')} returned an empty choices array.")
            except UsageError:
                raise
            except Exception as exc:
                last_error = exc
                if index + 1 < len(order):
                    sys.stderr.write(
                        f"{c_tool_head} {provider.get('name', 'Provider')} failed ({redact(exc)}). "
                        f"Trying {order[index + 1].get('name', 'next provider')}.\n"
                    )
                    sys.stderr.flush()
                continue
            if index:
                order.insert(0, order.pop(index))
            return res_json
        checkpointer.transition("ERROR")
        raise ProviderError(redact(f"All configured providers failed for '{requested_model}'. Last error: {last_error}"))

    for turn_idx in range(max_turns):
        res_json = complete_turn()
        choice = res_json["choices"][0]
        message = choice.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # Model emitted final answer -> statechart transition to COMPLETED
            final_content = message.get("content", "") or ""
            checkpointer.final_response = final_content
            checkpointer.transition("COMPLETED")
            return final_content

        # Statechart transition to EXECUTING
        checkpointer.transition("EXECUTING")
        current_turn_msgs = [message]
        tool_executions: List[Dict[str, Any]] = []

        # Execute each tool call
        for tc in tool_calls:
            call_id = tc.get("id", "")
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
            except Exception:
                args = {}

            sys.stderr.write(f"\n{c_tool_head} Invoking tool: {c_tool_call}{fn_name}{c_reset}\n")
            sys.stderr.flush()

            is_error = False
            if registry:
                try:
                    result_content: Any = registry.dispatch(fn_name, args)
                    if isinstance(result_content, dict) and result_content.get("isError"):
                        is_error = True
                except Exception as e:
                    is_error = True
                    result_content = {"isError": True, "error": f"Error executing {fn_name}: {redact(e)}"}
            else:
                is_error = True
                result_content = {"isError": True, "error": f"MCP registry not available to execute {fn_name}"}

            checkpointer.scratchpad.record_tool_invocation(fn_name, args, result_content, is_error=is_error)

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

        turn_groups.append(current_turn_msgs)
        checkpointer.transition("OBSERVING")
        checkpointer.record_turn(turn_idx + 1, message, tool_executions)

    sys.stderr.write(f"\n{c_tool_head} Reached maximum iterations ({max_turns}).\n")
    checkpointer.transition("MAX_TURNS")
    last_content = messages[-1].get("content", "Agent loop reached maximum turns without termination.")
    checkpointer.final_response = last_content
    checkpointer.save()
    return last_content
