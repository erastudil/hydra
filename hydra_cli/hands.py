"""Local hands for Hydra: calc, units, clock, stacks, features, and memory.

No kid-safe mode. A personality changes the receipt label, not the sentence.
Stack cards are the compiled EasyLM shelves shipped in hydra_cli/data.
"""

from __future__ import annotations

import ast
import json
import operator
import re
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

from hydra_cli.alice_interpret import interpret
from hydra_cli.alice_knowledge import search_knowledge
from hydra_cli.alice_senses import browse, hear_audio, search_code, see_image, write_drawing
from hydra_cli.config import hydra_home

CARDS_PATH = Path(__file__).resolve().parent / "data" / "stacks_cards.jsonl"

PERSONALITIES = {
    "coder": "You are the coder. Prefer working code. Quote a tool result or a stack card. Do not invent an API.",
    "researcher": "You are the researcher. A claim needs a stack card or a tool result. Name a gap instead of filling it.",
    "chat": "You are in free chat. Be brief. Facts still come from a card or a tool.",
    "writer": "You are the creative writer. Shape the language. A factual claim still has to come from a card or a tool.",
}
ALIASES = {
    "coder": "coder",
    "coding": "coder",
    "code": "coder",
    "researcher": "researcher",
    "research": "researcher",
    "chat": "chat",
    "free": "chat",
    "free chat": "chat",
    "free-chat": "chat",
    "writer": "writer",
    "creative": "writer",
    "creative writer": "writer",
    "creative-writer": "writer",
}

STOP = {
    "a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "is", "it",
    "what", "whats", "does", "how", "why", "with", "from", "this", "that",
    "are", "be", "about", "which", "explain", "show", "tell", "me", "find",
    "please", "define", "who", "describe", "do", "you", "i", "my", "remember",
}

FEATURES = [
    ("feature:stacks", "stacks library shelf shelves textbook", "The stacks are the offline undergraduate shelves. A hit returns the stored sentence, its Dewey code, and the door."),
    ("feature:hands", "hands hand tools calc units datetime convert clock", "Local hands are calc, units, datetime, stacks, and memory. They run before a model sample."),
    ("feature:memory", "memory remember atom note", "Say 'remember preference: ...' to keep a note, or 'what do you remember'. A note is not a source for a new fact."),
    ("feature:personalities", "personality personalities voice coder researcher writer chat", "Voices are coder, researcher, free chat, and creative writer. A voice changes tone. The receipt still names the shelf."),
    ("feature:hydra", "hydra agent swarm serve mcp free", "Hydra commands: hands for a local answer, agent for a tool loop, free for one completion, swarm for the heads, serve for the gateway, mcp for community servers."),
]

UNITS = {
    ("km", "mi"): 1 / 1.60934,
    ("mi", "km"): 1.60934,
    ("kmh", "mph"): 1 / 1.60934,
    ("mph", "kmh"): 1.60934,
    ("c", "f"): None,
    ("f", "c"): None,
    ("kg", "lb"): 1 / 0.453592,
    ("lb", "kg"): 0.453592,
    ("m", "ft"): 1 / 0.3048,
    ("ft", "m"): 0.3048,
}
UNIT_ALIAS = {
    "kilometer": "km", "kilometers": "km", "km": "km",
    "mile": "mi", "miles": "mi", "mi": "mi",
    "km/h": "kmh", "kmh": "kmh", "kph": "kmh",
    "mph": "mph",
    "c": "c", "celsius": "c",
    "f": "f", "fahrenheit": "f",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "m": "m", "meter": "m", "meters": "m",
    "ft": "ft", "foot": "ft", "feet": "ft",
}

REMEMBER = re.compile(r"^remember(?:\s+(rule|goal|preference|fact|insight))?\s*:\s*(.+)$", re.I)
FORGET = re.compile(r"^forget\s*:\s*(.+)$", re.I)
RECALL = re.compile(r"^(?:what do you remember|show memory|memory search)(?:\s+about\s+(.+))?$", re.I)
ASK = re.compile(r"^(?:please\s+)?(?:what(?:'s| is)|define|explain|tell me about|who is|describe|calculate|compute|simplify)\s+", re.I)
UNIT_LINE = re.compile(r"^([0-9.]+)\s*([a-zA-Z/]+)\s+(?:to|in)\s+([a-zA-Z/]+)$", re.I)
DAYS = re.compile(r"days between\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})", re.I)

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def resolve_personality(name: str) -> str:
    key = (name or "chat").strip().lower()
    if key not in ALIASES:
        known = ", ".join(PERSONALITIES)
        raise KeyError(f"Unknown personality '{name}'. Known voices: {known}.")
    return ALIASES[key]


def personality_preface(name: str) -> str:
    return PERSONALITIES[resolve_personality(name)]


def tokenize(text: str) -> List[str]:
    return [tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if len(tok) > 1 and tok not in STOP]


def memory_path(explicit: Optional[Path] = None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(hydra_home()) / "alice_memory.db"


class MemoryStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path))
        self._con.row_factory = sqlite3.Row
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS atoms (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                personality TEXT NOT NULL,
                created TEXT NOT NULL
            )
            """
        )
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def add(self, text: str, kind: str, personality: str) -> Dict[str, Any]:
        created = datetime.now(timezone.utc).isoformat()
        cur = self._con.execute(
            "INSERT INTO atoms (kind, text, personality, created) VALUES (?, ?, ?, ?)",
            (kind, text.strip(), personality, created),
        )
        self._con.commit()
        return {"id": int(cur.lastrowid), "kind": kind, "text": text.strip()}

    def forget(self, needle: str) -> int:
        needle = needle.strip().lower()
        rows = self._con.execute("SELECT id, text FROM atoms").fetchall()
        ids = [int(row["id"]) for row in rows if needle and needle in row["text"].lower()]
        for atom_id in ids:
            self._con.execute("DELETE FROM atoms WHERE id = ?", (atom_id,))
        self._con.commit()
        return len(ids)

    def search(self, query: str, budget_chars: int = 1000) -> List[dict]:
        rows = list(self._con.execute("SELECT id, kind, text FROM atoms ORDER BY id"))
        terms = set(tokenize(query))
        ranked = []
        for row in rows:
            score = 100 if row["kind"] == "rule" else len(set(tokenize(row["text"])).intersection(terms))
            if score <= 0 and terms:
                continue
            if score <= 0 and not terms and row["kind"] != "rule":
                score = 1
            ranked.append((score, int(row["id"]), dict(row)))
        ranked.sort(key=lambda item: (-item[0], -item[1]))
        chosen = []
        used = 0
        for _score, _atom_id, atom in ranked:
            weight = len(atom["text"]) + 24
            if chosen and used + weight > budget_chars:
                continue
            chosen.append(atom)
            used += weight
            if len(chosen) >= 8:
                break
        rules = [atom for atom in chosen if atom["kind"] == "rule"]
        rest = [atom for atom in chosen if atom["kind"] != "rule"]
        return rules + rest


def _eval_node(node: ast.AST) -> Fraction:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Fraction(str(node.value)) if isinstance(node.value, float) else Fraction(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"gcd", "lcm"}:
        args = [_eval_node(arg) for arg in node.args]
        if len(args) != 2:
            raise ValueError("gcd and lcm take two integers")
        left, right = int(args[0]), int(args[1])
        import math
        if node.func.id == "gcd":
            result = math.gcd(left, right)
        else:
            result = 0 if left == 0 or right == 0 else abs(left * right) // math.gcd(left, right)
        return Fraction(result)
    raise ValueError("expression is not arithmetic")


def try_calc(query: str) -> Optional[str]:
    cleaned = ASK.sub("", query.strip()).rstrip("?.!=")
    cleaned = cleaned.replace("^", "**")
    if not re.search(r"\d", cleaned):
        return None
    if re.search(r"[A-Za-z]", cleaned) and not re.match(r"^(gcd|lcm)\s*\(", cleaned, re.I):
        return None
    try:
        value = _eval_node(ast.parse(cleaned, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None
    if value.denominator == 1:
        shown = str(value.numerator)
    else:
        shown = f"{value.numerator}/{value.denominator}"
    return f"act: say\nroute: COMPUTE\nsource: tool:calc\nanswer: Result: {shown}"


def try_units(query: str) -> Optional[str]:
    cleaned = re.sub(r"^(?:convert|how many)\s+", "", query.strip(), flags=re.I)
    matched = UNIT_LINE.match(cleaned.rstrip("?.!"))
    if not matched:
        return None
    amount = float(matched.group(1))
    src = UNIT_ALIAS.get(matched.group(2).lower())
    dst = UNIT_ALIAS.get(matched.group(3).lower())
    if not src or not dst:
        return None
    if (src, dst) == ("c", "f"):
        result = amount * 9 / 5 + 32
    elif (src, dst) == ("f", "c"):
        result = (amount - 32) * 5 / 9
    elif (src, dst) in UNITS and UNITS[(src, dst)] is not None:
        result = amount * UNITS[(src, dst)]
    else:
        return None
    return f"act: say\nroute: COMPUTE\nsource: tool:units\nanswer: {amount} {matched.group(2)} = {result:.4g} {matched.group(3)}"


def try_clock(query: str) -> Optional[str]:
    text = query.strip().rstrip("?.!")
    if re.match(r"^(what time is it|what is the time|current time|time now)$", text, re.I):
        now = datetime.now().astimezone()
        return f"act: say\nroute: COMPUTE\nsource: tool:datetime\nanswer: {now.isoformat(timespec='seconds')}"
    matched = DAYS.search(text)
    if matched:
        start = datetime.strptime(matched.group(1), "%Y-%m-%d")
        end = datetime.strptime(matched.group(2), "%Y-%m-%d")
        days = (end - start).days
        return f"act: say\nroute: COMPUTE\nsource: tool:datetime\nanswer: {days} days"
    return None


def load_cards() -> List[dict]:
    if not CARDS_PATH.exists():
        return []
    cards = []
    for line in CARDS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cards.append(json.loads(line))
    return cards


def search_stacks(query: str, limit: int = 1) -> List[dict]:
    raw = ASK.sub("", query.strip().rstrip("?.!")).strip().lower()
    terms = tokenize(raw)
    if len(terms) < 2:
        return []
    hits = []
    for card in load_cards():
        topic = card["topic"].lower()
        topic_terms = tokenize(card["topic"])
        if raw and raw in topic:
            score = 100 + len(terms)
        elif all(term in topic_terms or term in topic for term in terms):
            score = 40 + len(terms)
        else:
            continue
        hits.append((score, card))
    hits.sort(key=lambda item: (-item[0], item[1]["topic"]))
    return [card for _score, card in hits[:limit]]


def search_features(query: str) -> Optional[tuple]:
    if not re.search(r"\b(how|help|what can you|how do i|how to)\b", query, re.I):
        return None
    terms = set(tokenize(query))
    best = None
    best_score = 0
    for feature in FEATURES:
        score = len(terms.intersection(feature[1].split()))
        if score > best_score:
            best = feature
            best_score = score
    if best_score <= 0:
        return None
    return best


def _card_answer(card: dict) -> str:
    door = f" Door: {card['door']}" if card.get("door") else ""
    return (
        f"act: cite\nroute: RETRIEVE\nsource: stack:{card['card_id']}\n"
        f"answer: {card['topic']}: {card['comment']} [Dewey {card['dewey']} · {card['title']}]{door}"
    )


def _lines(act: str, route: str, source: str, answer: str, frame: str = "", nxt: str = "") -> str:
    rows = [f"act: {act}", f"route: {route}"]
    if frame:
        rows.append(f"frame: {frame}")
    rows.append(f"source: {source or '-'}")
    rows.append(f"answer: {answer}")
    if nxt:
        rows.append(f"next: {nxt}")
    return "\n".join(rows)


def _instrument_answer(text: str, personality_id: str) -> Optional[str]:
    """Cite a Hydra command, or run a local eye, ear, or drawing hand. None walks the shelves."""
    reading = interpret(text, personality_id)
    if reading.frame in {"swarm", "agent", "mcp", "serve"}:
        return _lines(
            "cite",
            "ORCHESTRATE",
            reading.shelves[0] if reading.shelves else reading.tool,
            reading.command,
            frame=reading.frame,
            nxt="run that command yourself; Alice does not launch a paid swarm or a frontier agent",
        )
    if reading.frame == "browse":
        result = browse(reading.subject)
        route = "SENSE" if result.get("ok") else "ABSTAIN"
        return _lines(result["act"], route, result["source"], result["answer"], frame="browse", nxt=result.get("next") or "")
    if reading.frame == "see":
        result = see_image(Path(reading.subject))
        route = "SENSE" if result.get("ok") else "ABSTAIN"
        return _lines(result["act"], route, result["source"], result["answer"], frame="see", nxt=result.get("next") or "")
    if reading.frame == "hear":
        result = hear_audio(Path(reading.subject))
        route = "SENSE" if result.get("ok") else "ABSTAIN"
        return _lines(result["act"], route, result["source"], result["answer"], frame="hear", nxt=result.get("next") or "")
    if reading.frame == "draw":
        dest = Path(hydra_home()) / "drawings" / "drawing.svg"
        result = write_drawing(text, dest)
        svg = result.get("svg") or ""
        answer = result["answer"] + ("\n" + svg if svg else "")
        return _lines("say", "SENSE", "tool:draw", answer, frame="draw")
    if reading.frame == "search":
        result = search_code(reading.subject, Path.cwd())
        route = "SENSE" if result.get("ok") else "ABSTAIN"
        return _lines(result["act"], route, result["source"], result["answer"], frame="search", nxt=result.get("next") or "")
    return None


def answer_locally(query: str, personality: str = "chat", memory_file: Optional[Path] = None) -> str:
    personality_id = resolve_personality(personality)
    text = query.strip()
    store = MemoryStore(memory_path(memory_file))
    try:
        body = _answer(text, personality_id, store)
    finally:
        store.close()
    return f"personality: {personality_id}\n{body}"


def _answer(text: str, personality_id: str, store: MemoryStore) -> str:
    remembered = REMEMBER.match(text)
    if remembered:
        kind = (remembered.group(1) or "fact").lower()
        atom = store.add(remembered.group(2), kind, personality_id)
        return f"act: say\nroute: MEMORY\nsource: memory:{atom['id']}\nanswer: Kept a {atom['kind']}: {atom['text']}"
    forgotten = FORGET.match(text)
    if forgotten:
        count = store.forget(forgotten.group(1))
        return f"act: say\nroute: MEMORY\nsource: memory:forget\nanswer: Forgot {count} note{'s' if count != 1 else ''}."
    recall = RECALL.match(text)
    if recall:
        found = store.search(recall.group(1) or text)
        if not found:
            return "act: silence-gap\nroute: ABSTAIN\nsource: -\nanswer: No memory notes matched.\nnext: remember a note, or ask a stack"
        lines = "\n".join(f"- [{atom['kind']}] {atom['text']}" for atom in found)
        return f"act: say\nroute: MEMORY\nsource: memory:search\nanswer: {lines}"
    instrument = _instrument_answer(text, personality_id)
    if instrument:
        return instrument
    for attempt in (try_clock, try_units, try_calc):
        hit = attempt(text)
        if hit:
            return hit
    stacks = search_stacks(text)
    if stacks:
        return _card_answer(stacks[0])
    feature = search_features(text)
    if feature:
        return f"act: cite\nroute: RETRIEVE\nsource: {feature[0]}\nanswer: {feature[2]}"
    known = search_knowledge(text)
    if known:
        return (
            f"act: cite\nroute: RETRIEVE\nsource: {known['card_id']}\n"
            f"answer: {known['topic']}: {known['comment']}"
        )
    return (
        "act: silence-gap\nroute: ABSTAIN\nsource: -\n"
        "answer: No stack card, feature card, or tool covered that.\n"
        "next: name a stack subject, or ask for a sample"
    )


def native_openai_tools() -> List[dict]:
    specs = [
        ("calc", "Exact arithmetic. Example: 3/4 + 1/6", "expression"),
        ("units", "Convert a measurement. Example: 10 km to miles", "expression"),
        ("datetime", "Local time, or days between two YYYY-MM-DD dates.", "expression"),
        ("stacks", "Search the undergraduate stack cards for a stored sentence.", "query"),
        ("feature", "How a Hydra or EasyLM surface works: stacks, hands, memory, personalities.", "query"),
        ("memory_search", "Read notes the person asked to keep. Not a truth shelf.", "query"),
        ("memory_write", "Keep a note. Prefix the query with rule, goal, preference, fact, or insight.", "query"),
    ]
    tools = []
    for name, description, arg in specs:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {arg: {"type": "string"}},
                        "required": [arg],
                    },
                },
            }
        )
    return tools


def dispatch_native(name: str, args: Dict[str, Any]) -> Optional[str]:
    known = {"calc", "units", "datetime", "stacks", "feature", "memory_search", "memory_write"}
    if name not in known:
        return None
    query = str(args.get("expression") or args.get("query") or "").strip()
    if name == "memory_search":
        return answer_locally(f"what do you remember about {query}")
    if name == "memory_write":
        if ":" not in query:
            query = f"fact: {query}"
        return answer_locally(f"remember {query}")
    if name == "stacks":
        hits = search_stacks(query)
        if not hits:
            return "act: silence-gap\nroute: ABSTAIN\nanswer: No stack card matched."
        return _card_answer(hits[0])
    if name == "feature":
        feature = search_features(f"how do I {query}")
        if not feature:
            return "act: silence-gap\nroute: ABSTAIN\nanswer: No feature card matched."
        return f"act: cite\nsource: {feature[0]}\nanswer: {feature[2]}"
    if name == "calc":
        return try_calc(query) or "act: silence-instrument\nanswer: Could not evaluate that expression."
    if name == "units":
        return try_units(query) or "act: silence-instrument\nanswer: No conversion for that pair."
    if name == "datetime":
        return try_clock(query) or "act: silence-instrument\nanswer: Could not read that clock question."
    return None
