import copy
import json
import re
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# Closed synonym sets. A query term recalls a turn indexed under any member.
SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "bug": ("error", "exception", "failure", "traceback"),
    "error": ("bug", "exception", "traceback", "failure"),
    "exception": ("error", "bug", "traceback"),
    "failure": ("error", "bug"),
    "test": ("pytest", "assert"),
    "pytest": ("test", "assert"),
    "file": ("path", "module"),
    "path": ("file", "module"),
    "module": ("file", "path"),
    "function": ("method", "def"),
    "method": ("function",),
    "rule": ("invariant", "agents"),
    "invariant": ("rule",),
    "context": ("ledger", "recall"),
    "ledger": ("context", "recall"),
    "recall": ("context", "ledger"),
    "code": ("source",),
    "source": ("code",),
}

STOPWORDS = frozenset({
    "about", "after", "again", "being", "could", "every", "first", "other",
    "their", "there", "these", "thing", "those", "under", "where", "which",
    "while", "would", "should", "please", "thanks",
})

CONTEXT_MODES: Tuple[Tuple[str, str], ...] = (
    ("recall", "search the ledger and paste matching turns whole"),
    ("sliding", "keep the last few turns whole"),
)

_MODE_ALIASES = {
    "retrieve": "recall",
    "index": "recall",
    "compact": "recall",
    "recent": "sliding",
    "window": "sliding",
}

_PATH = re.compile(r"(?:[a-zA-Z]:\\|/)(?:[\w\-\.]+(?:\\|/))*[\w\-\.]+")
_CAMEL = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+)+\b")
_PASCAL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_WORD = re.compile(r"\b[a-zA-Z][a-zA-Z0-9]{4,}\b")
_TICK = re.compile(r"`([^`]{2,80})`")


def normalize_context_mode(name: Optional[str]) -> str:
    key = (name or "recall").strip().lower()
    key = _MODE_ALIASES.get(key, key)
    if key in ("recall", "sliding"):
        return key
    return "recall"


def context_label(name: Optional[str]) -> str:
    return normalize_context_mode(name)


def resolve_context_mode(arg: str) -> Optional[str]:
    text = " ".join(arg.strip().lower().split())
    if not text:
        return None
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(CONTEXT_MODES):
            return CONTEXT_MODES[index - 1][0]
        return None
    if text in _MODE_ALIASES or text in ("recall", "sliding"):
        return normalize_context_mode(text)
    return None


def format_context_picker(active: Optional[str]) -> str:
    current = normalize_context_mode(active)
    lines = ["context :"]
    for index, (name, note) in enumerate(CONTEXT_MODES, 1):
        mark = ">" if name == current else " "
        lines.append(f"{mark} {index}  {name:<8} {note}")
    lines.append(f"current : {current}")
    lines.append("select : /strategy <recall or sliding>")
    return "\n".join(lines)


def register_label(name: Optional[str]) -> str:
    key = (name or "progen").strip().lower()
    if key in ("syntax", "progen", ""):
        return "progen"
    return key


class SessionContextLedger:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.session_dir = Path.home() / ".hydra" / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.session_dir / "ledger.jsonl"
        self.dump_path = self.session_dir / "dump.json"
        self.turns: List[Dict[str, Any]] = []
        self.index: Dict[str, List[int]] = {}
        self._load_ledger()

    def _load_ledger(self):
        if self.ledger_path.exists():
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        turn = json.loads(line)
                        self.turns.append(turn)
                        self._index_turn(turn)
                    except json.JSONDecodeError:
                        pass

    def extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        found = []
        found.extend(_PATH.findall(text))
        found.extend(_CAMEL.findall(text))
        found.extend(_PASCAL.findall(text))
        found.extend(_SNAKE.findall(text))
        found.extend(_TICK.findall(text))
        for word in _WORD.findall(text):
            if word.lower() not in STOPWORDS:
                found.append(word)
        for word in re.findall(r"\b[a-zA-Z]{3,4}\b", text):
            if word.lower() in SYNONYMS:
                found.append(word)
        return list(dict.fromkeys(found))

    def _terms_for(self, keyword: str) -> List[str]:
        key = keyword.lower()
        terms = [key]
        terms.extend(SYNONYMS.get(key, ()))
        return terms

    def _index_turn(self, turn: Dict[str, Any]):
        idx = len(self.turns) - 1
        text = turn.get("content", "") or ""
        if turn.get("thought"):
            text += " " + turn["thought"]
        for kw in self.extract_keywords(text):
            bucket = self.index.setdefault(kw.lower(), [])
            if idx not in bucket:
                bucket.append(idx)

    def append_turn(self, role: str, content: str, thought: Optional[str] = None, tool_calls: Optional[List[Any]] = None, tool_results: Optional[List[Any]] = None):
        turn = {
            "role": role,
            "content": content,
            "thought": thought,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
        }
        self.turns.append(turn)
        self._index_turn(turn)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn) + "\n")

    def compact_session(self, max_history_turns: int = 5) -> Dict[str, Any]:
        """Compact active session turns while writing full verbatim history to disk."""
        total_on_disk = len(self.turns)
        with open(self.dump_path, "w", encoding="utf-8") as f:
            json.dump(self.turns, f, indent=2)

        pinned_indices = set(self._pinned())
        pinned_turns = [copy.deepcopy(self.turns[i]) for i in sorted(pinned_indices)]

        unpinned_turns = [
            (idx, turn) for idx, turn in enumerate(self.turns) if idx not in pinned_indices
        ]

        pruned_records: List[Dict[str, Any]] = []
        if max_history_turns > 0 and len(unpinned_turns) > max_history_turns:
            excess_count = len(unpinned_turns) - max_history_turns
            pruned_pairs = unpinned_turns[:excess_count]
            retained_unpinned = [turn for _, turn in unpinned_turns[excess_count:]]
            for idx, turn in pruned_pairs:
                role = turn.get("role", "turn")
                content = str(turn.get("content") or "")
                snippet = content[:120].strip() if content else "[tool round]"
                pruned_records.append({
                    "turn_index": idx,
                    "role": role,
                    "summary": snippet,
                })
        else:
            retained_unpinned = [turn for _, turn in unpinned_turns]

        for turn in retained_unpinned:
            content = turn.get("content")
            if isinstance(content, str) and len(content) > 10000:
                withheld = len(content) - 4000
                turn["content"] = content[:4000] + f"\n[OUTPUT PRUNED: {withheld} chars on disk ledger]"
            if turn.get("tool_results"):
                for tr in turn["tool_results"]:
                    res = tr.get("result")
                    if isinstance(res, str) and len(res) > 10000:
                        tr["result"] = res[:4000] + f"\n[TOOL RESULT PRUNED: {len(res) - 4000} chars on disk]"

        rebuilt = []
        for t in self.turns:
            if t in pinned_turns or t in retained_unpinned:
                rebuilt.append(t)
        self.turns = rebuilt if rebuilt else (pinned_turns + retained_unpinned)

        self.index = {}
        for turn in self.turns:
            self._index_turn(turn)

        pruned_count = len(pruned_records)
        summary = (
            "topic : session ledger\n\n"
            "state : verbatim on disk\n\n"
            f"path : {self.ledger_path}\n\n"
            f"turns_on_disk : {total_on_disk}\n\n"
            f"turns_retained : {len(self.turns)}\n\n"
            f"turns_pruned : {pruned_count}\n\n"
            "instructions : pinned outside the ledger"
        )
        return {
            "summary": summary,
            "pruned_messages": pruned_records,
            "turns_on_disk": total_on_disk,
            "turns_retained": len(self.turns),
            "turns_pruned": pruned_count,
            "ledger": str(self.ledger_path),
        }

    def _pinned(self) -> List[int]:
        pinned = []
        for idx, turn in enumerate(self.turns):
            if turn.get("pinned") or turn.get("role") in ("system", "instruction"):
                pinned.append(idx)
        return pinned

    def retrieve_verbatim(self, keywords: List[str], max_turns: int = 4) -> List[Dict[str, Any]]:
        scores: Dict[int, int] = {}
        for kw in keywords:
            code = bool(_SNAKE.search(kw) or _CAMEL.search(kw) or _PASCAL.search(kw) or _PATH.search(kw) or "`" in kw)
            weight = 3 if code else 1
            for term in self._terms_for(kw):
                hit_weight = weight if term == kw.lower() else 1
                for idx in self.index.get(term, []):
                    scores[idx] = scores.get(idx, 0) + hit_weight
        ranked = sorted(scores.keys(), key=lambda idx: scores[idx], reverse=True)
        chosen = list(self._pinned())
        for idx in ranked:
            if idx in chosen:
                continue
            chosen.append(idx)
            if len(chosen) >= max_turns + len(self._pinned()):
                break
        chosen = chosen[: max_turns + len(self._pinned())]
        return [self.turns[idx] for idx in sorted(set(chosen))]

    def render_prior(self, prompt: str, mode: Optional[str] = None, max_chars: int = 100000) -> str:
        """Live prefix for the next prompt. Recalled turns bounded to prevent prompt prefix bloat."""
        key = normalize_context_mode(mode)
        if key == "sliding":
            lines = []
            for turn in self.turns[-4:]:
                role = turn.get("role")
                content = str(turn.get("content") or "")
                if "[PRIOR TURNS]" in content:
                    content = content.split("[PRIOR TURNS]")[-1].lstrip("\r\n")
                if "[RETRIEVED CONTEXT]" in content:
                    content = content.split("[RETRIEVED CONTEXT]")[-1].lstrip("\r\n")
                if role in ("user", "assistant") and content.strip() and not content.startswith("[IN-FLIGHT"):
                    lines.append(f"{role} : {content}")
            if not lines:
                return ""
            rendered = "[PRIOR TURNS]\n" + "\n".join(lines)
            return rendered[:max_chars]

        hits = self.retrieve_verbatim(self.extract_keywords(prompt), max_turns=4)
        bodies = []
        for hit in hits:
            content = str(hit.get("content") or "")
            if not content.strip():
                if hit.get("tool_results"):
                    results = hit["tool_results"]
                    summary = ", ".join(r.get("name", "tool") for r in results[:3])
                    content = f"[tool executed: {summary}]"
                else:
                    continue
            if "[PRIOR TURNS]" in content:
                content = content.split("[PRIOR TURNS]")[-1].lstrip("\r\n")
            if "[RETRIEVED CONTEXT]" in content:
                content = content.split("[RETRIEVED CONTEXT]")[-1].lstrip("\r\n")
            role = hit.get("role") or "turn"
            bodies.append(f"{role} : {content}")
        if not bodies:
            return ""
        rendered = "[RETRIEVED CONTEXT]\n" + "\n".join(bodies)
        return rendered[:max_chars]
