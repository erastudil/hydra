from unittest.mock import MagicMock, patch
import json
import os
import tempfile
import pytest

from hydra_cli.native_tools import NativeToolRegistry


@pytest.fixture
def temp_workspace(tmp_path):
    # Setup test workspace directory structure
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main():\n    print('hello world')\n", encoding="utf-8")
    (src_dir / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "README.md").write_text("# Project Docs\nWelcome to documentation.\n", encoding="utf-8")

    ignored_dir = tmp_path / ".git"
    ignored_dir.mkdir()
    (ignored_dir / "config").write_text("git config", encoding="utf-8")

    return tmp_path


def test_read_file_operations(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # 1. Successful whole read
    res = reg.read_file("src/main.py")
    assert "1: def main():" in res
    assert "2:     print('hello world')" in res

    # 2. Line slicing
    res_slice = reg.read_file("src/main.py", start_line=2, end_line=2)
    assert "2:     print('hello world')" in res_slice
    assert "1: def main():" not in res_slice

    # 3. Slice beyond file bounds
    res_oob = reg.read_file("src/main.py", start_line=10, end_line=15)
    assert "exceeds line count" in res_oob

    # 4. Invalid slice
    res_inv = reg.read_file("src/main.py", start_line=5, end_line=2)
    assert "Invalid slice" in res_inv

    # 5. Non-existent file
    err_missing = reg.read_file("nonexistent.py")
    assert isinstance(err_missing, dict) and err_missing.get("isError")

    # 6. Directory as file
    err_dir = reg.read_file("src")
    assert isinstance(err_dir, dict) and err_dir.get("isError")

    # 7. Empty file
    empty_file = temp_workspace / "empty.txt"
    empty_file.write_text("", encoding="utf-8")
    assert reg.read_file("empty.txt") == "[File is empty]"

    # 8. Byte limit truncation
    res_trunc = reg.read_file("src/main.py", max_bytes=10)
    assert "[OUTPUT TRUNCATED: max_bytes limit reached]" in res_trunc


def test_write_file_operations(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # Atomic write to new file in new nested directory
    res = reg.write_file("new_dir/sub/test.txt", "line A\nline B\n")
    assert "Successfully wrote" in res

    target = temp_workspace / "new_dir" / "sub" / "test.txt"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "line A\nline B\n"

    # Overwrite existing file
    res_ov = reg.write_file("new_dir/sub/test.txt", "updated content")
    assert "Successfully wrote" in res_ov
    assert target.read_text(encoding="utf-8") == "updated content"


def test_edit_file_operations(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # 1. Unique match edit
    res = reg.edit_file("src/main.py", "hello world", "sovereign hydra")
    assert "Successfully edited" in res
    assert "sovereign hydra" in (temp_workspace / "src" / "main.py").read_text(encoding="utf-8")

    # 2. Pattern not found
    err_not_found = reg.edit_file("src/main.py", "missing string", "replacement")
    assert isinstance(err_not_found, dict) and err_not_found.get("isError")
    assert "not found" in err_not_found["error"]

    # 3. Ambiguous multiple match
    dup_file = temp_workspace / "dup.txt"
    dup_file.write_text("item item item\n", encoding="utf-8")
    err_mult = reg.edit_file("dup.txt", "item", "replacement")
    assert isinstance(err_mult, dict) and err_mult.get("isError")
    assert "requires unique match" in err_mult["error"]


def test_list_dir_operations(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    tree = reg.list_dir(".")
    assert "src/" in tree
    assert "docs/" in tree
    assert "main.py" in tree
    assert ".git" not in tree  # Ignored directory

    # Subdirectory
    docs_tree = reg.list_dir("docs")
    assert "README.md" in docs_tree

    # Non-existent directory
    err = reg.list_dir("ghost_dir")
    assert isinstance(err, dict) and err.get("isError")


def test_grep_search_operations(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # Match in multiple files
    res = reg.grep_search("def", ".")
    assert "main.py:1: def main():" in res
    assert "utils.py:1: def helper():" in res

    # File pattern filter
    res_filtered = reg.grep_search("def", ".", file_pattern="*main*.py")
    assert "main.py" in res_filtered
    assert "utils.py" not in res_filtered

    # Case sensitivity
    res_case = reg.grep_search("DEF", ".", case_sensitive=True)
    assert "No matches found" in res_case

    # Non-existent search path
    err = reg.grep_search("def", "missing_folder")
    assert isinstance(err, dict) and err.get("isError")


def test_find_files_operations(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # Find python files
    res = reg.find_files("*.py", ".")
    assert "main.py" in res
    assert "utils.py" in res
    assert "README.md" not in res

    # Match markdown
    res_md = reg.find_files("*.md", ".")
    assert "README.md" in res_md

    # No match
    res_none = reg.find_files("*.rs", ".")
    assert "No files matching" in res_none


def test_run_command_sandbox_integration(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # Safe command execution
    res = reg.run_command("python -c \"print('sandbox ok')\"")
    assert res["status"] == "SUCCESS"
    assert "sandbox ok" in res["stdout"]
    assert res.get("isError") is None

    # Destructive command blocked by CommandInspector
    res_blocked = reg.run_command("rm -rf /")
    assert res_blocked["status"] == "BLOCKED"
    assert res_blocked.get("isError") is True
    assert "Destructive command" in res_blocked["violation"]


def test_invoke_subagent_recursion_guard(temp_workspace):
    # Depth < 3 executes subagent
    reg_ok = NativeToolRegistry(cwd=str(temp_workspace), subagent_depth=1)
    with patch("hydra_cli.agent.run_agent_loop", return_value="Subagent result"):
        ans = reg_ok.invoke_subagent("Analyze security")
        assert ans == "Subagent result"

    # Depth >= 3 triggers recursion limit error
    reg_blocked = NativeToolRegistry(cwd=str(temp_workspace), subagent_depth=3)
    err = reg_blocked.invoke_subagent("Deeper recursion")
    assert isinstance(err, dict) and err.get("isError")
    assert "Recursion limit reached" in err["error"]


def test_swarm_fanout_integration(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))
    mock_res = [
        MagicMock(title="Architect", role="architect", content="Arch plan", error=None),
        MagicMock(title="Coder", role="coder", content="Code plan", error=None),
        MagicMock(title="Synthesizer", role="synthesizer", content="Final roadmap", error=None),
    ]
    with patch("hydra_cli.swarm.execute_swarm", return_value=mock_res):
        out = reg.swarm_fanout("Build system")
        assert "Architect" in out
        assert "Arch plan" in out
        assert "Final roadmap" in out


def test_openai_tool_schemas():
    reg = NativeToolRegistry()
    schemas = reg.get_openai_tools()
    assert len(schemas) == 11
    names = {s["function"]["name"] for s in schemas}
    expected = {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "grep_search",
        "find_files",
        "run_command",
        "invoke_subagent",
        "swarm_fanout",
        "compact_context",
        "retrieve_context",
    }
    assert names == expected
    for s in schemas:
        assert s["type"] == "function"
        assert "description" in s["function"]
        assert "parameters" in s["function"]


def test_dispatch_mechanism(temp_workspace):
    reg = NativeToolRegistry(cwd=str(temp_workspace))

    # Dispatch valid tool
    res = reg.dispatch("read_file", {"path": "src/main.py", "start_line": 1, "end_line": 1})
    assert "1: def main():" in res

    # Dispatch with context
    reg.dispatch("read_file", {"path": "src/main.py"}, context={"subagent_depth": 2})
    assert reg.subagent_depth == 2

    # Dispatch unknown tool
    err_unknown = reg.dispatch("nonexistent_tool", {})
    assert err_unknown.get("isError")

    # Dispatch invalid arguments
    err_args = reg.dispatch("read_file", {"invalid_arg": 123})
    assert err_args.get("isError")
