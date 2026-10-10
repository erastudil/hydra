import json
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path

from hydra_cli.config import CHARS_PER_TOKEN

COMPACT_AT = 0.8
KEEP_RECENT = 4

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
    ("recall", "dynamic threading: store every turn, reload only keyword hits"),
    ("compact", "normal: keep turns live, summarize older ones at 80 percent of the window"),
    ("sliding", "keep the last few turns whole"),
)

_MODE_ALIASES = {
    "retrieve": "recall",
    "index": "recall",
    "dynamic": "recall",
    "normal": "compact",
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
    if key in ("recall", "compact", "sliding"):
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
    if text in _MODE_ALIASES or text in ("recall", "compact", "sliding"):
        return normalize_context_mode(text)
    return None


def format_context_picker(active: Optional[str]) -> str:
    current = normalize_context_mode(active)
    lines = ["context :"]
    for index, (name, note) in enumerate(CONTEXT_MODES, 1):
        mark = ">" if name == current else " "
        lines.append(f"{mark} {index}  {name:<8} {note}")
    lines.append(f"current : {current}")
    lines.append("select : /strategy <recall, compact or sliding>")
    return "\n".join(lines)


def register_label(name: Optional[str]) -> str:
    key = (name or "progen").strip().lower()
    if key in ("syntax", "progen", ""):
        return "progen"
    return key


def _enc(term: str) -> str:
    """FTS5-safe token for an exact keyword."""
    return "k" + term.lower().encode("utf-8").hex()


def categorize(role: str, content: str, tool_calls: Any = None, tool_results: Any = None) -> str:
    if role in ("system", "instruction"):
        return "instruction"
    if tool_calls or tool_results or role == "tool":
        return "tool"
    if re.search(r"traceback|exception|error:|failed", content, re.IGNORECASE):
        return "error"
    if "```" in content or _PATH.search(content) or _SNAKE.search(content):
        return "code"
    return "chat"


class SessionContextLedger:
    """Every turn lives in one sqlite file: category, keyword labels and an FTS5 keyword index.

    Nothing is held in memory between calls. end_turn() closes the connection.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.session_dir = Path.home() / ".hydra" / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.session_dir / "ledger.db"
        self._db: Optional[sqlite3.Connection] = None
        legacy = self.session_dir / "ledger.jsonl"
        if legacy.exists():
            if not self.turns:
                for line in legacy.read_text(encoding="utf-8").splitlines():
                    try:
                        turn = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.append_turn(turn.get("role", "user"), turn.get("content", ""), turn.get("thought"),
                                     turn.get("tool_calls"), turn.get("tool_results"), bool(turn.get("pinned")))
            legacy.rename(legacy.with_suffix(".jsonl.imported"))

    @property
    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(str(self.ledger_path))
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, role TEXT, category TEXT,"
                " labels TEXT, pinned INTEGER, covers INTEGER, body TEXT)"
            )
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS kw USING fts5(terms)")
        return self._db

    def end_turn(self) -> None:
        """Commit and drop the connection. The next call reloads from disk."""
        if self._db is not None:
            self._db.commit()
            self._db.close()
            self._db = None

    def _select(self, where: str = "", args: Tuple = (), order: str = "id") -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT id, category, labels, covers, body FROM turns {where} ORDER BY {order}", args
        ).fetchall()
        out = []
        for row_id, category, labels, covers, body in rows:
            turn = json.loads(body)
            turn.update(id=row_id, category=category, labels=labels, covers=covers)
            out.append(turn)
        return out

    @property
    def turns(self) -> List[Dict[str, Any]]:
        return self._select()

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
        return [key, *SYNONYMS.get(key, ())]

    def append_turn(self, role: str, content: str, thought: Optional[str] = None, tool_calls: Optional[List[Any]] = None,
                    tool_results: Optional[List[Any]] = None, pinned: bool = False, covers: Optional[int] = None):
        turn = {"role": role, "content": content, "thought": thought, "tool_calls": tool_calls, "tool_results": tool_results}
        category = categorize(role, content or "", tool_calls, tool_results)
        keywords = [] if role == "summary" else self.extract_keywords((content or "") + " " + (thought or ""))
        pin = int(pinned or role in ("system", "instruction"))
        conn = self._conn
        cur = conn.execute(
            "INSERT INTO turns (role, category, labels, pinned, covers, body) VALUES (?, ?, ?, ?, ?, ?)",
            (role, category, " ".join(keywords[:8]), pin, covers, json.dumps(turn)),
        )
        conn.execute("INSERT INTO kw (rowid, terms) VALUES (?, ?)", (cur.lastrowid, " ".join(_enc(k) for k in keywords)))
        conn.commit()

    def categories(self) -> Dict[str, int]:
        return dict(self._conn.execute("SELECT category, COUNT(*) FROM turns GROUP BY category").fetchall())

    def compact_session(self, max_history_turns: int = 5) -> Dict[str, Any]:
        """Commit and unload. Turn text stays verbatim. Nothing is summarized away."""
        del max_history_turns
        count = self._conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        cats = ", ".join(f"{name} {n}" for name, n in sorted(self.categories().items()))
        self.end_turn()
        summary = (
            "topic: session ledger\n"
            "state: verbatim on disk\n"
            f"path: {self.ledger_path}\n"
            f"turns: {count}\n"
            f"categories: {cats or 'none'}\n"
            "live: unloaded\n"
            "instructions: pinned outside the ledger"
        )
        return {"summary": summary, "pruned_messages": [], "turns_on_disk": count, "ledger": str(self.ledger_path)}

    def retrieve_verbatim(self, keywords: List[str], max_turns: int = 4) -> List[Dict[str, Any]]:
        scores: Dict[int, int] = {}
        for kw in keywords:
            code = bool(_SNAKE.search(kw) or _CAMEL.search(kw) or _PASCAL.search(kw) or _PATH.search(kw) or "`" in kw)
            weight = 3 if code else 1
            for term in self._terms_for(kw):
                hit_weight = weight if term == kw.lower() else 1
                for (row_id,) in self._conn.execute("SELECT rowid FROM kw WHERE kw MATCH ?", (_enc(term),)):
                    scores[row_id] = scores.get(row_id, 0) + hit_weight
        pinned = [r[0] for r in self._conn.execute("SELECT id FROM turns WHERE pinned = 1 ORDER BY id")]
        ranked = [i for i in sorted(scores, key=lambda i: (-scores[i], i)) if i not in pinned and self._is_content(i)]
        chosen = pinned + ranked[:max_turns]
        if not chosen:
            return []
        return self._select(f"WHERE id IN ({','.join('?' * len(chosen))})", tuple(chosen))

    def _is_content(self, row_id: int) -> bool:
        return self._conn.execute("SELECT role FROM turns WHERE id = ?", (row_id,)).fetchone()[0] != "summary"

    def live_window(self) -> List[Dict[str, Any]]:
        """Newest summary, then every turn after the turns it covers."""
        latest = self._select("WHERE role = 'summary'", order="id DESC LIMIT 1")
        after = latest[0]["covers"] if latest else 0
        return latest + self._select("WHERE role != 'summary' AND id > ?", (after,))

    def compact_if_needed(self, window_tokens: int, summarize: Callable[[str], str], extra_chars: int = 0) -> bool:
        """Mode B. Summarize older live turns once the live text reaches 80 percent of the window."""
        live = self.live_window()
        chars = sum(len(str(t.get("content") or "")) for t in live) + extra_chars
        if chars / CHARS_PER_TOKEN < COMPACT_AT * window_tokens or len(live) <= KEEP_RECENT + 1:
            return False
        older = live[:-KEEP_RECENT]
        text = "\n".join(f"{t.get('role')} : {t.get('content')}" for t in older)
        summary = (summarize(text) or "").strip()
        if not summary:
            return False
        self.append_turn("summary", summary, covers=older[-1]["id"])
        return True

    def render_prior(self, prompt: str, mode: Optional[str] = None) -> str:
        """Live prefix for the next prompt. Recalled turns are copied whole."""
        key = normalize_context_mode(mode)
        if key == "sliding":
            recent = reversed(self._select("WHERE role IN ('user', 'assistant')", order="id DESC LIMIT 4"))
            lines = [f"{t['role']} : {t['content']}" for t in recent
                     if str(t.get("content") or "").strip() and not t["content"].startswith("[IN-FLIGHT")]
            return "[PRIOR TURNS]\n" + "\n".join(lines) if lines else ""
        if key == "compact":
            lines = []
            for t in self.live_window():
                body = str(t.get("content") or "")
                if t["role"] == "summary":
                    lines.append(f"summary of earlier turns : {body}")
                elif t["role"] in ("user", "assistant") and body.strip() and not body.startswith("[IN-FLIGHT"):
                    lines.append(f"{t['role']} : {body}")
            return "[CONVERSATION SO FAR]\n" + "\n".join(lines) if lines else ""
        bodies = []
        for hit in self.retrieve_verbatim(self.extract_keywords(prompt), max_turns=4):
            body = str(hit.get("content") or "")
            if body.strip():
                bodies.append(f"{hit.get('role') or 'turn'} [{hit['category']} : {hit['labels']}] : {body}")
        return "[RETRIEVED CONTEXT]\n" + "\n".join(bodies) if bodies else ""
