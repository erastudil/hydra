"""Per-family dialect layer (HYDRA-PASS §8).

Hydra's internal envelope is one canonical shape: OpenAI-style messages,
assistant ``tool_calls`` and ``tool`` role observations. Each model family
speaks a different native envelope, so this module owns the translation
both ways for the families Hydra seats:

* ``gemma``  — local Gemma seats on llama.cpp. Native form is the Gemma 4
  turn/tool token envelope: ``<|turn>role ... <turn|>`` turns, tool
  declarations/calls/responses inside ``<|tool>`` / ``<|tool_call>`` /
  ``<|tool_response>`` blocks, and every string literal delimited with
  ``<|"|>``. Thought channels (``<|channel> ... <channel|>``) are kept
  across tool calls inside one model turn and stripped between turns,
  per Google's Gemma formatting guidance.
* ``bonsai`` — local Bonsai seats (Qwen3.5-derived weights served by the
  Prism llama.cpp fork). Native form is the Qwen/Hermes ChatML envelope:
  ``<|im_start|>role ... <|im_end|>`` turns, tool specs inside a
  ``<tools>`` block in the system turn, calls as ``<tool_call>`` JSON
  blocks, results wrapped in ``<tool_response>`` blocks. Multimodal
  content parts pass through in standard OpenAI form, which is what the
  vendor serving docs specify.
* ``api``    — remote OpenAI-compatible endpoints (OpenRouter, the free
  routes, frontier providers). The API dialect *is* Hydra's internal
  envelope: native ``tools`` / ``tool_calls`` / ``tool`` messages pass
  through unchanged, so this shim is the identity plus the shared
  text-form fallback parser for endpoints that answer in text.

This layer is where the hardcodable half of the adapter corpus lives
(ADAPTER-RUN-2026-11-01 rescope): ``envelope_exemplar`` material (the
wire envelopes above) and ``lookup_render`` material (the deterministic
rendering of tool specs and tool observations). Both are generated here
in code; neither belongs in adapter training data. Only real behavior
residue remains as future training material.

Dialect follows the *serving* envelope, not the weights: a Gemma model
served by a remote API speaks the API dialect, because the endpoint —
not the checkpoint — defines the wire format. Local seats are classified
by model; remote URLs always classify as ``api`` unless the catalog
pins an explicit family shim.
"""

import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from hydra_cli.tool_adapter import extract_tool_calls

FAMILY_GEMMA = "gemma"
FAMILY_BONSAI = "bonsai"
FAMILY_API = "api"
FAMILIES = (FAMILY_GEMMA, FAMILY_BONSAI, FAMILY_API)

#: Families whose envelope differs from the internal one and therefore
#: get the in-envelope treatment in the agent loop.
DIALECT_FAMILIES = (FAMILY_GEMMA, FAMILY_BONSAI)


def is_managed_local_url(url: Optional[str]) -> bool:
    """True when ``url`` is Hydra's managed llama.cpp seat endpoint.

    The dialect treatment (envelope in-message, no native ``tools``
    field) is only safe where Hydra owns the server and the model's
    template: the managed local endpoint from
    ``hydra_cli.local_server``. Any other endpoint — a remote API, an
    Ollama/custom local base, or a test double on an ephemeral port —
    keeps the native envelope, so behavior there cannot regress.
    """
    if not url:
        return False
    try:
        from hydra_cli.local_server import _llamacpp_base

        base = _llamacpp_base()
    except Exception:
        base = "http://127.0.0.1:8080"
    return str(url).rstrip("/").startswith(base + "/v1")

_LOCAL_URL_HINTS = ("127.0.0.1", "localhost", "0.0.0.0", "[::1]")


def _is_local_url(url: Optional[str]) -> bool:
    if not url:
        return False
    lowered = str(url).lower()
    return any(hint in lowered for hint in _LOCAL_URL_HINTS)


def family_for_route(
    model: Optional[str],
    url: Optional[str] = None,
    format_shim: Optional[str] = None,
) -> str:
    """Classify a (model, endpoint) pair into a dialect family.

    An explicit catalog ``format_shim`` naming a family wins. A remote
    endpoint speaks the API dialect regardless of which weights sit
    behind it. Local endpoints are classified by the model they serve.
    The legacy ``stdio`` shim names no family envelope: those routes
    fall through to model classification, then to ``api``.
    """
    shim = str(format_shim or "").strip().lower()
    if shim in FAMILIES:
        return shim
    if url and not _is_local_url(url):
        return FAMILY_API
    name = str(model or "").lower()
    if "gemma" in name:
        return FAMILY_GEMMA
    if "bonsai" in name or "ternary" in name:
        return FAMILY_BONSAI
    return FAMILY_API


# ---------------------------------------------------------------------------
# Gemma value literals: strings are delimited with <|"|>, keys are bare,
# numbers/booleans/null are bare literals (Google Gemma formatting guide).
# ---------------------------------------------------------------------------

_GEMMA_STR = '<|"|>'


def gemma_render_value(value: Any) -> str:
    """Render a JSON-shaped value in Gemma's literal syntax."""
    if isinstance(value, str):
        cleaned = value.replace(_GEMMA_STR, "")
        return f"{_GEMMA_STR}{cleaned}{_GEMMA_STR}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(gemma_render_value(v) for v in value) + "]"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            key_text = str(key)
            rendered_key = key_text if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_text) else gemma_render_value(key_text)
            parts.append(f"{rendered_key}:{gemma_render_value(item)}")
        return "{" + ",".join(parts) + "}"
    return gemma_render_value(str(value))


class _GemmaValueParser:
    """Recursive-descent parser for Gemma literal values."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek(self) -> str:
        self._skip_ws()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def parse(self) -> Any:
        value = self._value()
        return value

    def _value(self) -> Any:
        self._skip_ws()
        if self.text.startswith(_GEMMA_STR, self.pos):
            end = self.text.find(_GEMMA_STR, self.pos + len(_GEMMA_STR))
            if end == -1:
                raise ValueError("unterminated Gemma string literal")
            out = self.text[self.pos + len(_GEMMA_STR):end]
            self.pos = end + len(_GEMMA_STR)
            return out
        ch = self._peek()
        if ch == "{":
            return self._object()
        if ch == "[":
            return self._array()
        return self._bare()

    def _object(self) -> Dict[str, Any]:
        self.pos += 1  # consume {
        out: Dict[str, Any] = {}
        if self._peek() == "}":
            self.pos += 1
            return out
        while True:
            self._skip_ws()
            if self.text.startswith(_GEMMA_STR, self.pos):
                key = self._value()
            else:
                match = re.match(r"[A-Za-z_][A-Za-z0-9_\-]*", self.text[self.pos:])
                if not match:
                    raise ValueError(f"expected Gemma object key at {self.pos}")
                key = match.group(0)
                self.pos += len(key)
            if self._peek() != ":":
                raise ValueError("expected ':' in Gemma object")
            self.pos += 1
            out[str(key)] = self._value()
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                continue
            if ch == "}":
                self.pos += 1
                return out
            raise ValueError("expected ',' or '}' in Gemma object")

    def _array(self) -> List[Any]:
        self.pos += 1  # consume [
        out: List[Any] = []
        if self._peek() == "]":
            self.pos += 1
            return out
        while True:
            out.append(self._value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                continue
            if ch == "]":
                self.pos += 1
                return out
            raise ValueError("expected ',' or ']' in Gemma array")

    def _bare(self) -> Any:
        match = re.match(r"[^,\}\]\s]+", self.text[self.pos:])
        if not match:
            raise ValueError(f"expected Gemma value at {self.pos}")
        token = match.group(0)
        self.pos += len(token)
        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            pass
        return token


def gemma_parse_value(text: str) -> Any:
    """Parse one Gemma literal value (used for tool-call arguments)."""
    return _GemmaValueParser(text).parse()


# ---------------------------------------------------------------------------
# Tool spec rendering (the lookup_render category): one deterministic
# rendering of the tool catalog per family.
# ---------------------------------------------------------------------------


def _openai_tool_parts(tool: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    fn = tool.get("function", tool) if isinstance(tool, dict) else {}
    name = str(fn.get("name") or "")
    description = str(fn.get("description") or "")
    parameters = fn.get("parameters") or {"type": "object", "properties": {}}
    return name, description, parameters


def render_tool_index(tools: List[Dict[str, Any]], family: str) -> Any:
    """Render the tool catalog in ``family``'s native lookup form.

    gemma -> declaration blocks (text); bonsai -> a ``<tools>`` block
    (text); api -> the native tools array itself (structure).
    """
    if family == FAMILY_API:
        return copy.deepcopy(tools)
    if family == FAMILY_GEMMA:
        blocks = []
        for tool in tools or []:
            name, description, parameters = _openai_tool_parts(tool)
            if not name:
                continue
            decl: Dict[str, Any] = {}
            if description:
                decl["description"] = description
            decl["parameters"] = parameters
            blocks.append(f"<|tool>declaration:{name}{gemma_render_value(decl)}<tool|>")
        return "\n".join(blocks)
    # bonsai / Qwen-Hermes form
    specs = []
    for tool in tools or []:
        name, description, parameters = _openai_tool_parts(tool)
        if not name:
            continue
        specs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
    body = "\n".join(json.dumps(spec, ensure_ascii=False) for spec in specs)
    return (
        "You have access to the following functions:\n\n"
        f"<tools>\n{body}\n</tools>\n\n"
        "For each function call, return a json object with the function name and its arguments "
        "within <tool_call> and </tool_call> XML tags:\n"
        "<tool_call>\n"
        '{"name": "<function-name>", "arguments": <args-json-object>}\n'
        "</tool_call>"
    )


def render_tool_call(name: str, arguments: Any, family: str) -> str:
    """Render one tool call in ``family``'s native envelope form."""
    if isinstance(arguments, str):
        try:
            args_obj = json.loads(arguments) if arguments.strip() else {}
        except Exception:
            args_obj = {"_raw": arguments}
    else:
        args_obj = arguments or {}
    if family == FAMILY_GEMMA:
        return f"<|tool_call>call:{name}{gemma_render_value(args_obj)}<tool_call|>"
    payload = {"name": name, "arguments": args_obj}
    return "<tool_call>\n" + json.dumps(payload, ensure_ascii=False) + "\n</tool_call>"


def render_observation(name: str, result: Any, family: str, call_id: str = "") -> Any:
    """Render one tool observation in ``family``'s native form.

    gemma -> a ``<|tool_response>`` block (text); bonsai -> a
    ``<tool_response>`` block (text); api -> a native ``tool`` role
    message (structure).
    """
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if family == FAMILY_GEMMA:
        return f"<|tool_response>response:{name}{gemma_render_value({'result': text})}<tool_response|>"
    if family == FAMILY_BONSAI:
        return f"<tool_response>\n{text}\n</tool_response>"
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": text}


# ---------------------------------------------------------------------------
# Message adaptation: internal envelope -> family envelope (chat form).
# The output is still a message list an OpenAI-shaped endpoint accepts;
# each family's own template tokens carry the envelope inside content.
# ---------------------------------------------------------------------------

_GEMMA_THOUGHT_RE = re.compile(r"<\|channel>.*?<channel\|>", re.DOTALL)


def strip_gemma_thoughts(text: str) -> str:
    """Drop Gemma thought-channel spans from replayed history.

    Google's guidance: thoughts are stripped between standard turns and
    kept only across tool calls within a single model turn. Replayed
    history is by definition between turns, so the spans go.
    """
    return _GEMMA_THOUGHT_RE.sub("", text or "")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _adapt_messages_gemma(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    adapted: List[Dict[str, Any]] = []
    declarations = render_tool_index(tools or [], FAMILY_GEMMA)
    system_seen = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system":
            system_seen = True
            content = _content_text(message.get("content"))
            if declarations:
                content = f"{content}\n\n{declarations}" if content else declarations
            adapted.append({"role": "system", "content": content})
            continue
        if role == "tool":
            name = str(message.get("name") or "tool")
            adapted.append({
                "role": "user",
                "content": render_observation(name, _content_text(message.get("content")), FAMILY_GEMMA),
            })
            continue
        if role == "assistant":
            content = strip_gemma_thoughts(_content_text(message.get("content")))
            blocks = []
            for call in message.get("tool_calls") or []:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                if fn.get("name"):
                    blocks.append(render_tool_call(str(fn["name"]), fn.get("arguments", "{}"), FAMILY_GEMMA))
            if blocks:
                content = (content + "\n" + "\n".join(blocks)).strip() if content else "\n".join(blocks)
            adapted.append({"role": "assistant", "content": content})
            continue
        adapted.append({"role": role or "user", "content": _content_text(message.get("content"))})
    if declarations and not system_seen:
        adapted.insert(0, {"role": "system", "content": declarations})
    return adapted


def _adapt_messages_bonsai(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    adapted: List[Dict[str, Any]] = []
    tool_block = render_tool_index(tools or [], FAMILY_BONSAI) if tools else ""
    system_seen = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system":
            system_seen = True
            content = message.get("content")
            if tool_block:
                text = _content_text(content)
                content = f"{text}\n\n{tool_block}" if text else tool_block
            adapted.append({"role": "system", "content": content})
            continue
        if role == "tool":
            adapted.append({
                "role": "user",
                "content": render_observation(str(message.get("name") or "tool"), _content_text(message.get("content")), FAMILY_BONSAI),
            })
            continue
        if role == "assistant":
            # Keep multimodal content parts untouched (vendor serving
            # form); only structured tool_calls are re-enveloped.
            content = message.get("content")
            blocks = []
            for call in message.get("tool_calls") or []:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                if fn.get("name"):
                    blocks.append(render_tool_call(str(fn["name"]), fn.get("arguments", "{}"), FAMILY_BONSAI))
            if blocks:
                text = _content_text(content)
                content = (text + "\n" + "\n".join(blocks)).strip() if text else "\n".join(blocks)
            adapted.append({"role": "assistant", "content": content})
            continue
        adapted.append(dict(message))
    if tool_block and not system_seen:
        adapted.insert(0, {"role": "system", "content": tool_block})
    return adapted


def adapt_messages(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    family: str,
) -> List[Dict[str, Any]]:
    """Translate internal-envelope messages into ``family``'s envelope.

    ``api`` is the identity: the internal envelope already is the API
    dialect, so messages pass through structurally unchanged.
    """
    if family == FAMILY_GEMMA:
        return _adapt_messages_gemma(messages, tools)
    if family == FAMILY_BONSAI:
        return _adapt_messages_bonsai(messages, tools)
    return [dict(m) if isinstance(m, dict) else m for m in messages]


# ---------------------------------------------------------------------------
# Full prompt rendering (inspectable native form of the same envelope).
# ---------------------------------------------------------------------------


def render_prompt(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], family: str) -> str:
    """Render the complete native prompt string for ``family``.

    gemma -> Gemma 4 turn-token prompt ending at the model turn marker;
    bonsai -> ChatML prompt ending at the assistant marker; api -> the
    JSON payload form (messages + tools), which is the API envelope.
    """
    if family == FAMILY_API:
        payload: Dict[str, Any] = {"messages": adapt_messages(messages, tools, family)}
        if tools:
            payload["tools"] = copy.deepcopy(tools)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    adapted = adapt_messages(messages, tools, family)
    if family == FAMILY_GEMMA:
        turns = []
        for message in adapted:
            role = message.get("role")
            gemma_role = {"system": "system", "user": "user", "assistant": "model"}.get(role or "", "user")
            turns.append(f"<|turn>{gemma_role}\n{_content_text(message.get('content'))}<turn|>")
        turns.append("<|turn>model\n")
        return "\n".join(turns)
    turns = []
    for message in adapted:
        role = message.get("role") or "user"
        turns.append(f"<|im_start|>{role}\n{_content_text(message.get('content'))}<|im_end|>")
    turns.append("<|im_start|>assistant\n")
    return "\n".join(turns)


# ---------------------------------------------------------------------------
# Response parsing: family envelope -> internal tool calls.
# ---------------------------------------------------------------------------

_GEMMA_CALL_RE = re.compile(r"<\|tool_call>call:([A-Za-z0-9_.\-]+)(\{.*?\})<tool_call\|>", re.DOTALL)


def parse_gemma_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract tool calls from Gemma's native call blocks."""
    calls: List[Dict[str, Any]] = []
    for idx, match in enumerate(_GEMMA_CALL_RE.finditer(text or "")):
        name, raw_args = match.group(1), match.group(2)
        try:
            args = gemma_parse_value(raw_args)
            if not isinstance(args, dict):
                args = {"value": args}
        except Exception:
            args = {}
        calls.append({
            "id": f"call_{idx}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return calls


def parse_tool_calls(text: str, family: str) -> List[Dict[str, Any]]:
    """Parse tool calls out of a model reply in ``family``'s envelope.

    The family-native form is tried first; the shared text forms from
    the universal tool adapter are accepted as a fallback for every
    family, so a model that answers in the generic form still lands.
    """
    if family == FAMILY_GEMMA:
        native = parse_gemma_tool_calls(text)
        if native:
            return native
    return extract_tool_calls(text)
