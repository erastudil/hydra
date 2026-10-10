"""
Command Palette and Shortcut Registry for Hydra Desktop.
Manages hotkey bindings, canonical shortcut normalization, priority resolution,
conflict detection, action dispatch, and command palette integration.
Adheres to AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Canonical modifier precedence
MODIFIER_ORDER = {
    "Ctrl": 0,
    "Alt": 1,
    "Shift": 2,
    "Meta": 3,
}

MODIFIER_ALIASES = {
    "ctrl": ("Ctrl", 0),
    "control": ("Ctrl", 0),
    "alt": ("Alt", 1),
    "option": ("Alt", 1),
    "opt": ("Alt", 1),
    "shift": ("Shift", 2),
    "cmd": ("Meta", 3),
    "command": ("Meta", 3),
    "meta": ("Meta", 3),
    "win": ("Meta", 3),
    "windows": ("Meta", 3),
}

SPECIAL_KEYS = {
    "delete": "Delete",
    "del": "Delete",
    "enter": "Enter",
    "return": "Enter",
    "backspace": "Backspace",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "space": "Space",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "insert": "Insert",
}


def normalize_shortcut(shortcut: Optional[str]) -> Optional[str]:
    """
    Normalize keyboard shortcut string into canonical format:
    e.g. 'shift+ctrl+k' -> 'Ctrl+Shift+K', 'cmd+shift+p' -> 'Shift+Meta+P'.
    Returns None for empty or invalid strings.
    """
    if not shortcut or not isinstance(shortcut, str):
        return None

    raw = shortcut.strip()
    if not raw:
        return None

    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts:
        return None

    modifiers: List[Tuple[str, int]] = []
    keys: List[str] = []

    for part in parts:
        p_lower = part.lower()
        if p_lower in MODIFIER_ALIASES:
            canon_name, order_idx = MODIFIER_ALIASES[p_lower]
            if (canon_name, order_idx) not in modifiers:
                modifiers.append((canon_name, order_idx))
        else:
            if p_lower in SPECIAL_KEYS:
                keys.append(SPECIAL_KEYS[p_lower])
            elif len(part) == 1:
                keys.append(part.upper())
            else:
                keys.append(part.capitalize())

    # Sort modifiers by canonical order index
    modifiers.sort(key=lambda m: m[1])
    sorted_mod_names = [m[0] for m in modifiers]

    tokens = sorted_mod_names + keys
    if not tokens:
        return None

    return "+".join(tokens)


@dataclass
class CommandItem:
    """Atomic command palette action descriptor."""
    command_id: str
    title: str
    category: str = "general"
    shortcut: Optional[str] = None
    priority: int = 100
    handler: Optional[Callable[[Dict[str, Any]], Any]] = None
    endpoint: Optional[str] = None
    method: str = "POST"
    enabled: bool = True
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.shortcut:
            self.shortcut = normalize_shortcut(self.shortcut)

    @property
    def id(self) -> str:
        return self.command_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "id": self.command_id,
            "title": self.title,
            "category": self.category,
            "shortcut": self.shortcut,
            "priority": self.priority,
            "endpoint": self.endpoint,
            "method": self.method,
            "enabled": self.enabled,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


class CommandRegistry:
    """
    Sovereign Command Registry for Hydra Desktop Command Palette.
    Handles shortcut normalization, priority collision resolution, conflict reporting,
    and action dispatching.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._commands: Dict[str, CommandItem] = {}

    @property
    def total_commands(self) -> int:
        with self._lock:
            return len(self._commands)

    def clear(self) -> None:
        """Clear all registered commands."""
        with self._lock:
            self._commands.clear()

    def register(self, command: Union[CommandItem, Dict[str, Any]]) -> CommandItem:
        """Register a command item or dictionary spec."""
        if isinstance(command, CommandItem):
            item = command
        elif isinstance(command, dict):
            cid = command.get("command_id") or command.get("id") or "unnamed_command"
            item = CommandItem(
                command_id=cid,
                title=command.get("title", cid),
                category=command.get("category", "general"),
                shortcut=command.get("shortcut"),
                priority=int(command.get("priority", 100)),
                handler=command.get("handler"),
                endpoint=command.get("endpoint"),
                method=command.get("method", "POST"),
                enabled=bool(command.get("enabled", True)),
                description=command.get("description", ""),
                metadata=dict(command.get("metadata", {})),
            )
        else:
            raise TypeError(f"Expected CommandItem or dict, got {type(command)}")

        with self._lock:
            self._commands[item.command_id] = item
        return item

    def unregister(self, command_id: str) -> bool:
        """Unregister command by ID."""
        with self._lock:
            return self._commands.pop(command_id, None) is not None

    def get_command(self, command_id: str) -> Optional[CommandItem]:
        """Fetch command item by ID."""
        with self._lock:
            return self._commands.get(command_id)

    def list_commands(self, category: Optional[str] = None, enabled_only: bool = True) -> List[CommandItem]:
        """List registered commands, optionally filtered by category and enabled flag."""
        with self._lock:
            cmds = list(self._commands.values())

        if enabled_only:
            cmds = [c for c in cmds if c.enabled]

        if category:
            clean_cat = category.strip().lower()
            cmds = [c for c in cmds if c.category.lower() == clean_cat]

        return sorted(cmds, key=lambda c: (-c.priority, c.title))

    def detect_shortcut_conflicts(self) -> List[Dict[str, Any]]:
        """Detect and report any duplicate/overlapping keyboard shortcuts."""
        with self._lock:
            by_shortcut: Dict[str, List[CommandItem]] = {}
            for c in self._commands.values():
                if c.enabled and c.shortcut:
                    by_shortcut.setdefault(c.shortcut, []).append(c)

        conflicts = []
        for sc, cmds in by_shortcut.items():
            if len(cmds) > 1:
                conflicts.append({
                    "shortcut": sc,
                    "conflict_count": len(cmds),
                    "commands": [c.to_dict() for c in cmds],
                })

        return conflicts

    def resolve_shortcut(self, shortcut: str) -> Optional[CommandItem]:
        """Resolve shortcut to winning command based on priority."""
        norm = normalize_shortcut(shortcut)
        if not norm:
            return None

        with self._lock:
            matching = [c for c in self._commands.values() if c.enabled and c.shortcut == norm]

        if not matching:
            return None

        # Sort by priority descending; winner has highest priority
        matching.sort(key=lambda c: c.priority, reverse=True)
        return matching[0]

    def dispatch(self, command_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute command handler or dispatch through desktop dispatcher."""
        p = params or {}
        cmd = self.get_command(command_id)

        if not cmd:
            return {"isError": True, "error": f"Command not found: {command_id}", "dispatched": command_id}

        if not cmd.enabled:
            return {"isError": True, "error": f"Command '{command_id}' is disabled", "dispatched": command_id}

        if cmd.handler is not None:
            try:
                res = cmd.handler(p)
                return {"isError": False, "dispatched": command_id, "result": res}
            except Exception as e:
                return {"isError": True, "error": str(e), "dispatched": command_id}

        # Check if desktop command palette actions can handle it
        try:
            from hydra_cli.desktop import COMMAND_PALETTE_ACTIONS, dispatch_command_palette_action
            if command_id in COMMAND_PALETTE_ACTIONS:
                return dispatch_command_palette_action(command_id, p)
        except Exception:
            pass

        return {"isError": False, "dispatched": command_id, "result": {"status": "ok"}}

    def dispatch_by_shortcut(self, shortcut: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Resolve command by shortcut string and execute."""
        winner = self.resolve_shortcut(shortcut)
        if not winner:
            return {"isError": True, "error": f"No command mapped to shortcut '{shortcut}'"}
        return self.dispatch(winner.command_id, params)

    def search(self, query: str, limit: int = 50) -> List[CommandItem]:
        """Search commands using token matching with exact prefix bonuses."""
        if not query or not query.strip():
            return self.list_commands()[:limit]

        q = query.strip().lower()
        tokens = [t for t in re.split(r"[\s:+_-]+", q) if t]

        with self._lock:
            all_cmds = list(self._commands.values())

        scored: List[Tuple[float, CommandItem]] = []

        for c in all_cmds:
            if not c.enabled:
                continue

            c_id = c.command_id.lower()
            c_title = c.title.lower()
            c_cat = c.category.lower()
            c_desc = c.description.lower()

            score = 0.0

            # Exact matches
            if q == c_id:
                score += 100.0
            if q == c_title:
                score += 90.0

            # Prefix matches
            if c_id.startswith(q):
                score += 50.0
            if c_title.startswith(q):
                score += 45.0

            # Token matches
            matched_all_tokens = True
            for tok in tokens:
                tok_matched = False
                if tok in c_id:
                    score += 20.0
                    tok_matched = True
                if tok in c_title:
                    score += 15.0
                    tok_matched = True
                if tok in c_cat:
                    score += 10.0
                    tok_matched = True
                if tok in c_desc:
                    score += 5.0
                    tok_matched = True

                if not tok_matched:
                    matched_all_tokens = False

            if matched_all_tokens:
                score += 30.0

            if score > 0.0:
                # Add priority tiebreaker
                final_score = score + (c.priority * 0.01)
                scored.append((final_score, c))

        scored.sort(key=lambda s: s[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def import_default_palette_actions(self) -> int:
        """Ingest built-in actions from hydra_cli.desktop.COMMAND_PALETTE_ACTIONS."""
        try:
            from hydra_cli.desktop import COMMAND_PALETTE_ACTIONS
        except Exception:
            return 0

        default_shortcuts = {
            "agent:start": "Ctrl+Shift+R",
            "agent:abort": "Ctrl+Q",
            "agent:pause": "Ctrl+P",
            "token:summary": "Ctrl+Shift+T",
            "screen:capture": "Ctrl+Shift+S",
            "terminal:execute": "Ctrl+`",
        }

        count = 0
        for act_id, act_meta in COMMAND_PALETTE_ACTIONS.items():
            sc = default_shortcuts.get(act_id)
            item = CommandItem(
                command_id=act_id,
                title=act_meta.get("title", act_id),
                category=act_meta.get("category", "general"),
                description=act_meta.get("description", ""),
                shortcut=sc,
                endpoint=act_meta.get("endpoint"),
                method=act_meta.get("method", "POST"),
                priority=100,
            )
            self.register(item)
            count += 1

        return count


_GLOBAL_REGISTRY: Optional[CommandRegistry] = None
_GLOBAL_REG_LOCK = threading.RLock()


def get_command_registry() -> CommandRegistry:
    """Acquire thread-safe singleton CommandRegistry."""
    global _GLOBAL_REGISTRY
    with _GLOBAL_REG_LOCK:
        if _GLOBAL_REGISTRY is None:
            _GLOBAL_REGISTRY = CommandRegistry()
            _GLOBAL_REGISTRY.import_default_palette_actions()
        return _GLOBAL_REGISTRY


def reset_command_registry() -> CommandRegistry:
    """Reset singleton CommandRegistry."""
    global _GLOBAL_REGISTRY
    with _GLOBAL_REG_LOCK:
        _GLOBAL_REGISTRY = CommandRegistry()
        return _GLOBAL_REGISTRY
