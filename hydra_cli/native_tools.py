"""
Native coding tools and registry for Hydra autonomous coding agents.
Provides zero-dependency built-in tools: read_file, write_file, edit_file, list_dir,
grep_search, find_files, run_command, invoke_subagent, and swarm_fanout.
"""

import fnmatch
import json
import os
import re
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from hydra_cli.context import SessionContextLedger


def _format_size(size_bytes: int) -> str:
    """Format byte size into human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class NativeToolRegistry:
    """
    Built-in coding tool registry providing file operations, search, execution sandboxing,
    subagent spawning, and swarm fan-out. Zero external dependencies.
    """

    IGNORED_DIRS = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "node_modules",
        ".gemini",
        ".hydra",
    }

    def __init__(
        self,
        cwd: Optional[str] = None,
        subagent_depth: int = 0,
        tier: Optional[str] = None,
        alias: Optional[str] = None,
        ledger: Optional[SessionContextLedger] = None,
    ):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.subagent_depth = subagent_depth
        self.tier = tier
        self.alias = alias
        self.ledger = ledger

        self._tools: Dict[str, Callable[..., Any]] = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "list_dir": self.list_dir,
            "grep_search": self.grep_search,
            "find_files": self.find_files,
            "run_command": self.run_command,
            "invoke_subagent": self.invoke_subagent,
            "swarm_fanout": self.swarm_fanout,
            "compact_context": self.compact_context,
            "retrieve_context": self.retrieve_context,
        }

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def read_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_bytes: int = 100_000,
    ) -> Any:
        """Read file contents with optional line slicing and byte limit."""
        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        if not os.path.exists(abs_path):
            return {"isError": True, "error": f"File not found: {path}"}
        if os.path.isdir(abs_path):
            return {"isError": True, "error": f"Path is a directory, not a file: {path}"}

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            return {"isError": True, "error": f"Failed to read {path}: {exc}"}

        lines = content.splitlines(keepends=True)
        if not lines:
            return "[File is empty]"

        total_lines = len(lines)
        if start_line is not None or end_line is not None:
            if start_line is not None and end_line is not None and int(start_line) > int(end_line):
                return f"[Invalid slice: start_line {start_line} > end_line {end_line}]"
            start = max(1, int(start_line)) if start_line is not None else 1
            if start > total_lines:
                return f"[File {path} has {total_lines} lines; start_line {start} exceeds line count]"
            end = min(total_lines, int(end_line)) if end_line is not None else total_lines
            selected = lines[start - 1 : end]
            formatted = [f"{i}: {line.rstrip(chr(10)).rstrip(chr(13))}" for i, line in enumerate(selected, start=start)]
            result = "\n".join(formatted)
        else:
            formatted = [f"{i}: {line.rstrip(chr(10)).rstrip(chr(13))}" for i, line in enumerate(lines, start=1)]
            result = "\n".join(formatted)

        encoded = result.encode("utf-8", errors="replace")
        if len(encoded) > max_bytes:
            result = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n[OUTPUT TRUNCATED: max_bytes limit reached]"

        return result

    def write_file(self, path: str, content: str) -> Any:
        """Perform atomic write to file."""
        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        parent = os.path.dirname(abs_path)
        try:
            os.makedirs(parent, exist_ok=True)
            temp_path = os.path.join(parent, f".tmp_write_{uuid.uuid4().hex}")
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp_path, abs_path)
            return f"Successfully wrote {len(content)} characters to {path}"
        except Exception as exc:
            return {"isError": True, "error": f"Failed to write {path}: {exc}"}

    def edit_file(self, path: str, old_text: str, new_text: str) -> Any:
        """Perform exact search-and-replace with unique-match validation."""
        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        if not os.path.exists(abs_path):
            return {"isError": True, "error": f"File not found: {path}"}
        if os.path.isdir(abs_path):
            return {"isError": True, "error": f"Path is a directory: {path}"}

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                current = f.read()
        except Exception as exc:
            return {"isError": True, "error": f"Failed to read {path}: {exc}"}

        count = current.count(old_text)
        if count == 0:
            return {"isError": True, "error": f"old_text not found in {path}: {old_text!r}"}
        if count > 1:
            return {"isError": True, "error": f"old_text occurs {count} times in {path}; exact search-and-replace requires unique match"}

        new_content = current.replace(old_text, new_text, 1)
        res = self.write_file(path, new_content)
        if isinstance(res, dict) and res.get("isError"):
            return res
        return f"Successfully edited {path} (1 replacement made)"

    def list_dir(self, path: str = ".", max_depth: int = 2) -> Any:
        """Provide formatted file and folder directory tree."""
        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        if not os.path.exists(abs_path):
            return {"isError": True, "error": f"Directory not found: {path}"}
        if not os.path.isdir(abs_path):
            return {"isError": True, "error": f"Path is not a directory: {path}"}

        lines = [f"{os.path.basename(abs_path) or path}/"]

        def _traverse(current_dir: str, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                entries = sorted(os.listdir(current_dir))
            except Exception:
                return

            dirs = []
            files = []
            for entry in entries:
                if entry in self.IGNORED_DIRS:
                    continue
                full_entry = os.path.join(current_dir, entry)
                if os.path.isdir(full_entry):
                    dirs.append(entry)
                else:
                    files.append(entry)

            indent = "  " * depth
            for d in dirs:
                lines.append(f"{indent}{d}/")
                _traverse(os.path.join(current_dir, d), depth + 1)
            for f in files:
                f_path = os.path.join(current_dir, f)
                try:
                    size_str = _format_size(os.path.getsize(f_path))
                    lines.append(f"{indent}{f} ({size_str})")
                except Exception:
                    lines.append(f"{indent}{f}")

        _traverse(abs_path, 1)
        if len(lines) == 1:
            return f"{path}/ (empty directory)"
        return "\n".join(lines)

    def grep_search(
        self,
        query: str,
        path: str = ".",
        file_pattern: Optional[str] = None,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> Any:
        """Provide regex or text search with file patterns and line numbers."""
        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        if not os.path.exists(abs_path):
            return {"isError": True, "error": f"Path not found: {path}"}

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(query, flags)
        except re.error:
            regex = re.compile(re.escape(query), flags)

        matches: List[str] = []

        target_files: List[str] = []
        if os.path.isfile(abs_path):
            target_files.append(abs_path)
        else:
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]
                for file in files:
                    if file_pattern and not fnmatch.fnmatch(file, file_pattern):
                        continue
                    target_files.append(os.path.join(root, file))

        for file_path in target_files:
            rel = os.path.relpath(file_path, self.cwd)
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            clean_line = line.rstrip("\r\n")
                            matches.append(f"{rel}:{line_num}: {clean_line}")
                            if len(matches) >= max_results:
                                matches.append(f"[TRUNCATED: reached max_results={max_results}]")
                                return "\n".join(matches)
            except Exception:
                continue

        if not matches:
            return f"No matches found for query {query!r} in {path}"
        return "\n".join(matches)

    def find_files(
        self,
        pattern: str = "*",
        path: str = ".",
        max_results: int = 100,
    ) -> Any:
        """Provide glob pattern file search."""
        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        if not os.path.exists(abs_path):
            return {"isError": True, "error": f"Path not found: {path}"}

        matches: List[str] = []
        if os.path.isfile(abs_path):
            rel = os.path.relpath(abs_path, self.cwd)
            if fnmatch.fnmatch(os.path.basename(abs_path), pattern) or fnmatch.fnmatch(rel, pattern):
                matches.append(rel)
        else:
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]
                for file in files:
                    full_p = os.path.join(root, file)
                    rel = os.path.relpath(full_p, self.cwd)
                    if fnmatch.fnmatch(file, pattern) or fnmatch.fnmatch(rel, pattern):
                        matches.append(rel)
                        if len(matches) >= max_results:
                            matches.append(f"[TRUNCATED: reached max_results={max_results}]")
                            return "\n".join(matches)

        if not matches:
            return f"No files matching {pattern!r} found in {path}"
        return "\n".join(matches)

    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Execute command safely via SandboxRunner and CommandInspector."""
        from hydra_cli.sandbox import SandboxConfig, SandboxRunner

        target_cwd = os.path.abspath(os.path.join(self.cwd, cwd)) if cwd else self.cwd
        config = SandboxConfig(timeout_seconds=timeout if timeout is not None else 30.0)
        runner = SandboxRunner(config=config)
        res = runner.run_command(command=command, cwd=target_cwd)
        out = res.to_dict()
        if res.status != "SUCCESS" or res.exit_code != 0:
            out["isError"] = True
        return out

    def invoke_subagent(
        self,
        prompt: str,
        alias: Optional[str] = None,
        ledger: Optional[SessionContextLedger] = None,
        tier: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_turns: int = 10,
    ) -> Any:
        """Launch child agent loop with bounded recursion depth (<= 3)."""
        if self.subagent_depth >= 3:
            return {
                "isError": True,
                "error": f"Recursion limit reached: subagent_depth={self.subagent_depth} (max=3)",
            }

        from hydra_cli.agent import run_agent_loop

        from hydra_cli.config import DEFAULT_ORCHESTRATOR_MODEL
        target_alias = alias or self.alias or DEFAULT_ORCHESTRATOR_MODEL
        target_tier = tier or self.tier
        try:
            return run_agent_loop(
                alias=target_alias,
                prompt=prompt,
                tier=target_tier,
                system_prompt=system_prompt,
                max_turns=max_turns,
                subagent_depth=self.subagent_depth + 1,
                cwd=self.cwd,
            )
        except Exception as exc:
            return {"isError": True, "error": f"Subagent execution failed: {exc}"}

    def swarm_fanout(
        self,
        task: str,
        heads: Optional[List[str]] = None,
        tier: Optional[str] = None,
        custom_model: Optional[str] = None,
    ) -> Any:
        """Execute multi-head swarm and return consolidated findings."""
        from hydra_cli.swarm import execute_swarm

        target_tier = tier or self.tier
        try:
            results = execute_swarm(
                task=task,
                heads=heads,
                tier=target_tier,
                custom_model=custom_model,
                json_output=False,
            )
            sections = []
            for r in results:
                status = "ok" if not r.error else f"error ({r.error})"
                sections.append(f"### HEAD: {r.title} ({r.role}) · [{status}]\n{r.content if not r.error else r.error}")
            return "\n\n".join(sections)
        except Exception as exc:
            return {"isError": True, "error": f"Swarm fanout failed: {exc}"}

    def compact_context(self, max_history_turns: int = 5) -> Any:
        """Compact the session history ledger to save context window."""
        if not getattr(self, "ledger", None):
            return {"isError": True, "error": "No active SessionContextLedger to compact"}
        return self.ledger.compact_session(max_history_turns)

    def retrieve_context(self, keywords: List[str], max_turns: int = 3) -> Any:
        """Retrieve verbatim turns from history by keyword matches."""
        if not getattr(self, "ledger", None):
            return {"isError": True, "error": "No active SessionContextLedger to retrieve from"}
        return self.ledger.retrieve_verbatim(keywords, max_turns)

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Generate standard OpenAI function calling tool schemas for all native tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file contents with optional line slicing and byte limit.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative or absolute path to the file."},
                            "start_line": {"type": "integer", "description": "Optional 1-indexed start line number."},
                            "end_line": {"type": "integer", "description": "Optional 1-indexed end line number."},
                            "max_bytes": {"type": "integer", "description": "Maximum bytes to read (default 100000)."},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Atomically write content to a file, creating parent directories if needed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative or absolute path to the destination file."},
                            "content": {"type": "string", "description": "Full text content to write."},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Perform exact search-and-replace on a file. The old_text must match uniquely.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative or absolute path to the file to edit."},
                            "old_text": {"type": "string", "description": "Exact text segment to replace (must appear exactly once)."},
                            "new_text": {"type": "string", "description": "New replacement text."},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "Provide a formatted directory tree listing folders and files with sizes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the directory to list (default '.')."},
                            "max_depth": {"type": "integer", "description": "Maximum traversal depth (default 2)."},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_search",
                    "description": "Search files for regex or text patterns, returning matching lines with line numbers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Regex or text query to search for."},
                            "path": {"type": "string", "description": "Directory or file path to search within (default '.')."},
                            "file_pattern": {"type": "string", "description": "Optional glob pattern filter, e.g. '*.py'."},
                            "case_sensitive": {"type": "boolean", "description": "Whether match is case-sensitive (default false)."},
                            "max_results": {"type": "integer", "description": "Maximum matching lines to return (default 100)."},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_files",
                    "description": "Search for files matching a glob pattern.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Glob pattern to match, e.g. '*.py' or '*agent*'."},
                            "path": {"type": "string", "description": "Root directory to search in (default '.')."},
                            "max_results": {"type": "integer", "description": "Maximum files to return (default 100)."},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Safely run a shell command in the zero-trust execution sandbox.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Shell command to execute."},
                            "cwd": {"type": "string", "description": "Working directory for command execution."},
                            "timeout": {"type": "number", "description": "Timeout limit in seconds (default 30.0)."},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "invoke_subagent",
                    "description": "Launch a focused subagent with an isolated task loop (bounded recursion depth <= 3).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Specific task instruction for the subagent."},
                            "alias": {"type": "string", "description": "Model alias override (e.g. 'sonnet 5.5')."},
                            "tier": {"type": "string", "description": "Routing tier: 'free', 'local', or 'paid'."},
                            "system_prompt": {"type": "string", "description": "Custom subagent system prompt."},
                            "max_turns": {"type": "integer", "description": "Maximum agent turns (default 10)."},
                        },
                        "required": ["prompt"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compact_context",
                    "description": "Compact the session history ledger to save context window.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "max_history_turns": {"type": "integer", "description": "Number of recent turns to keep (default 5)."}
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "retrieve_context",
                    "description": "Retrieve verbatim turns from history by keyword matches.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords to match against turn history."},
                            "max_turns": {"type": "integer", "description": "Max turns to return (default 3)."}
                        },
                        "required": ["keywords"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "swarm_fanout",
                    "description": "Dispatch multi-agent swarm across specialist heads (Architect, Coder, Auditor, Synthesizer).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "description": "High-level engineering task or architectural challenge."},
                            "heads": {"type": "array", "items": {"type": "string"}, "description": "Specialist heads to run (e.g. ['architect', 'coder', 'auditor'])."},
                            "tier": {"type": "string", "description": "Routing tier: 'free', 'local', or 'paid'."},
                            "custom_model": {"type": "string", "description": "Explicit model override for all heads."},
                        },
                        "required": ["task"],
                    },
                },
            },
        ]

    def dispatch(
        self,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Dispatch tool call by name with arguments and execution context."""
        if not self.has_tool(name):
            return {"isError": True, "error": f"Unknown native tool: {name}"}

        handler = self._tools[name]
        call_args = dict(args or {})

        # Context updates (e.g. depth, cwd)
        if context:
            if "subagent_depth" in context:
                self.subagent_depth = context["subagent_depth"]
            if "cwd" in context and context["cwd"]:
                self.cwd = os.path.abspath(context["cwd"])
            if "tier" in context and context["tier"]:
                self.tier = context["tier"]
            if "alias" in context and context["alias"]:
                self.alias = context["alias"]

        try:
            return handler(**call_args)
        except TypeError as te:
            return {"isError": True, "error": f"Invalid arguments for {name}: {te}"}
        except Exception as exc:
            return {"isError": True, "error": f"Error executing {name}: {exc}"}
