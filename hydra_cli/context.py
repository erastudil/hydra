import os
import re
import json
from typing import List, Dict, Any, Optional
from pathlib import Path

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
        paths = re.findall(r'(?:[a-zA-Z]:\\|/)(?:[\w\-\.]+(?:\\|/))*[\w\-\.]+', text)
        camels = re.findall(r'\b[a-z]+(?:[A-Z][a-z0-9]+)+\b', text)
        pascals = re.findall(r'\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b', text)
        snakes = re.findall(r'\b[a-z]+(?:_[a-z0-9]+)+\b', text)
        words = re.findall(r'\b\w{5,}\b', text)
        return list(set(paths + camels + pascals + snakes + words))

    def _index_turn(self, turn: Dict[str, Any]):
        idx = len(self.turns) - 1
        text = turn.get("content", "") or ""
        if turn.get("thought"):
            text += " " + turn["thought"]
        keywords = self.extract_keywords(text)
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in self.index:
                self.index[kw_lower] = []
            if idx not in self.index[kw_lower]:
                self.index[kw_lower].append(idx)

    def append_turn(self, role: str, content: str, thought: Optional[str] = None, tool_calls: Optional[List[Any]] = None, tool_results: Optional[List[Any]] = None):
        turn = {
            "role": role,
            "content": content,
            "thought": thought,
            "tool_calls": tool_calls,
            "tool_results": tool_results
        }
        self.turns.append(turn)
        self._index_turn(turn)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn) + "\n")

    def compact_session(self, max_history_turns: int = 5) -> Dict[str, Any]:
        with open(self.dump_path, "w", encoding="utf-8") as f:
            json.dump(self.turns, f, indent=2)
        summary = "topic: session context\nstate: compacted\nturns: " + str(len(self.turns))
        pruned_messages = self.turns[-max_history_turns:] if len(self.turns) > max_history_turns else self.turns
        return {"summary": summary, "pruned_messages": pruned_messages}

    def retrieve_verbatim(self, keywords: List[str], max_turns: int = 3) -> List[Dict[str, Any]]:
        scores: Dict[int, int] = {}
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in self.index:
                for idx in self.index[kw_lower]:
                    scores[idx] = scores.get(idx, 0) + 1
        sorted_indices = sorted(scores.keys(), key=lambda idx: scores[idx], reverse=True)
        top_indices = sorted_indices[:max_turns]
        return [self.turns[idx] for idx in sorted(top_indices)]
