"""
Native coding tools and registry for Hydra autonomous coding agents.
Provides zero-dependency built-in tools: read_file, write_file, edit_file, list_dir,
grep_search, find_files, run_command, invoke_subagent, and swarm_fanout.
"""

import ast
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


_DEFAULT_STDLIB_MODULES = frozenset({
    "abc", "argparse", "ast", "asyncio", "base64", "collections", "concurrent",
    "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal", "difflib",
    "enum", "errno", "fnmatch", "functools", "gc", "glob", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "importlib", "inspect", "io", "itertools",
    "json", "logging", "math", "mimetypes", "multiprocessing", "numbers", "operator",
    "os", "pathlib", "pickle", "platform", "pprint", "queue", "random", "re",
    "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "string", "struct",
    "subprocess", "sys", "tempfile", "textwrap", "threading", "time", "traceback",
    "types", "typing", "unittest", "urllib", "uuid", "warnings", "weakref", "zipfile",
})


class AstImportSorter:
    """AST-driven Python import organizer and sorter."""

    DEFAULT_MAX_LINE_LENGTH = 88

    FUTURE = 0
    STDLIB = 1
    THIRDPARTY = 2
    FIRSTPARTY = 3

    def __init__(
        self,
        max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
        known_first_party: Optional[List[str]] = None,
    ):
        self.max_line_length = max_line_length
        self.known_first_party = set(known_first_party or [])
        self.known_first_party.update({"hydra", "hydra_cli"})

    def classify_module(self, module: Optional[str], level: int = 0) -> int:
        """Classify module import into section group: future, stdlib, thirdparty, or firstparty."""
        if level > 0 or not module:
            return self.FIRSTPARTY
        top = module.split(".")[0]
        if top == "__future__":
            return self.FUTURE
        if top in self.known_first_party:
            return self.FIRSTPARTY
        stdlib_names = getattr(sys, "stdlib_module_names", _DEFAULT_STDLIB_MODULES)
        if top in stdlib_names:
            return self.STDLIB
        return self.THIRDPARTY

    def sort_source(self, source: str) -> Dict[str, Any]:
        """Parse source code, sort and group top-level imports, and return formatted result."""
        newline = "\r\n" if "\r\n" in source else "\n"
        code = source.replace("\r\n", "\n")
        has_bom = code.startswith("\ufeff")
        if has_bom:
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        first_import_idx = None
        for i, node in enumerate(tree.body):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                first_import_idx = i
                break

        if first_import_idx is None:
            return {
                "isError": False,
                "changed": False,
                "sorted_code": source,
                "imports_count": 0,
            }

        import_nodes = []
        for node in tree.body[first_import_idx:]:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                import_nodes.append(node)
            else:
                break

        code_lines = code.split("\n")
        first_line = import_nodes[0].lineno
        last_line = import_nodes[-1].end_lineno

        groups: Dict[int, Dict[str, Any]] = {
            self.FUTURE: {"imports": [], "froms": {}},
            self.STDLIB: {"imports": [], "froms": {}},
            self.THIRDPARTY: {"imports": [], "froms": {}},
            self.FIRSTPARTY: {"imports": [], "froms": {}},
        }

        for node in import_nodes:
            comment = ""
            if node.lineno == node.end_lineno:
                orig_line = code_lines[node.lineno - 1]
                if "#" in orig_line:
                    comment = orig_line[orig_line.find("#"):].strip()

            if isinstance(node, ast.Import):
                for alias in node.names:
                    grp = self.classify_module(alias.name, level=0)
                    groups[grp]["imports"].append((alias.name, alias.asname, comment))
            elif isinstance(node, ast.ImportFrom):
                grp = self.classify_module(node.module, level=node.level)
                mod_key = (node.level, node.module or "", comment)
                if mod_key not in groups[grp]["froms"]:
                    groups[grp]["froms"][mod_key] = set()
                for alias in node.names:
                    groups[grp]["froms"][mod_key].add((alias.name, alias.asname))

        group_blocks = []
        for g_idx in (self.FUTURE, self.STDLIB, self.THIRDPARTY, self.FIRSTPARTY):
            g_data = groups[g_idx]
            lines = []
            uniq_imports = sorted(
                set(g_data["imports"]),
                key=lambda x: (x[0].lower(), (x[1] or "").lower()),
            )
            for name, asname, comment in uniq_imports:
                stmt = f"import {name} as {asname}" if asname else f"import {name}"
                if comment:
                    stmt = f"{stmt}  {comment}"
                lines.append(stmt)

            sorted_mod_keys = sorted(
                g_data["froms"].keys(),
                key=lambda k: ("." * k[0] + k[1]).lower(),
            )
            for level, mod, comment in sorted_mod_keys:
                dots = "." * level
                prefix = f"from {dots}{mod} import "
                names = sorted(
                    g_data["froms"][(level, mod, comment)],
                    key=lambda x: (x[0].lower(), (x[1] or "").lower()),
                )
                syms = [f"{n} as {a}" if a else n for n, a in names]
                one_line = prefix + ", ".join(syms)
                if comment:
                    one_line = f"{one_line}  {comment}"

                if len(one_line) <= self.max_line_length:
                    lines.append(one_line)
                else:
                    multi = [prefix + "("]
                    for s in syms:
                        multi.append(f"    {s},")
                    if comment:
                        multi.append(f")  {comment}")
                    else:
                        multi.append(")")
                    lines.append("\n".join(multi))

            if lines:
                group_blocks.append("\n".join(lines))

        sorted_imports_text = "\n\n".join(group_blocks)
        preamble_lines = code_lines[:first_line - 1]
        preamble_text = "\n".join(preamble_lines).rstrip("\n")

        remainder_lines = code_lines[last_line:]
        remainder_text = "\n".join(remainder_lines).strip("\n")

        pieces = []
        if preamble_text:
            pieces.append(preamble_text)
        pieces.append(sorted_imports_text)
        if remainder_text:
            pieces.append(remainder_text)

        joined = "\n\n".join(pieces)
        if source.endswith("\n"):
            joined += "\n"

        if newline == "\r\n":
            joined = joined.replace("\n", "\r\n")
        if has_bom:
            joined = "\ufeff" + joined

        changed = (joined != source)
        return {
            "isError": False,
            "changed": changed,
            "sorted_code": joined,
            "imports_count": len(import_nodes),
        }


class P013StubDetector:
    """AST visitor detecting P013 placeholder and stub violations in Python source code."""

    EXEMPT_DECORATORS = frozenset({
        "abstractmethod",
        "overload",
        "abstractproperty",
    })

    TODO_PATTERN = re.compile(
        r"#\s*(TODO|FIXME|STUB|PLACEHOLDER|NOT\s*IMPLEMENTED|XXX)\b",
        re.IGNORECASE,
    )

    def __init__(self, check_comments: bool = True, check_mocks: bool = True):
        self.check_comments = check_comments
        self.check_mocks = check_mocks

    def _is_exempt(self, node: ast.AST) -> bool:
        for dec in getattr(node, "decorator_list", []):
            if isinstance(dec, ast.Name) and dec.id in self.EXEMPT_DECORATORS:
                return True
            if isinstance(dec, ast.Attribute) and dec.attr in self.EXEMPT_DECORATORS:
                return True
            if isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name) and func.id in self.EXEMPT_DECORATORS:
                    return True
                if isinstance(func, ast.Attribute) and func.attr in self.EXEMPT_DECORATORS:
                    return True
        return False

    def detect(self, source: str) -> Dict[str, Any]:
        """Scan source code for P013 violations and return structured report."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        violations: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if self.check_mocks:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("unittest.mock") or alias.name == "mock":
                            violations.append({
                                "type": "banned_synthetic_mock",
                                "name": alias.name,
                                "lineno": node.lineno,
                                "message": f"Synthetic mock import '{alias.name}' banned under zero-fake-test invariant.",
                            })
                elif isinstance(node, ast.ImportFrom):
                    if node.module and (node.module.startswith("unittest.mock") or node.module == "mock"):
                        for alias in node.names:
                            violations.append({
                                "type": "banned_synthetic_mock",
                                "name": f"{node.module}.{alias.name}",
                                "lineno": node.lineno,
                                "message": f"Synthetic mock import '{node.module}.{alias.name}' banned under zero-fake-test invariant.",
                            })

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if self._is_exempt(node):
                    continue

                non_doc = []
                for s in node.body:
                    if isinstance(s, ast.Expr):
                        val = getattr(s, "value", None)
                        if isinstance(val, (ast.Constant, getattr(ast, "Str", type(None)))):
                            raw_val = getattr(val, "value", getattr(val, "s", None))
                            if isinstance(raw_val, str):
                                continue
                    non_doc.append(s)

                if len(non_doc) == 0:
                    violations.append({
                        "type": "docstring_only",
                        "name": node.name,
                        "lineno": node.lineno,
                        "message": f"Function '{node.name}' contains docstring without implementation.",
                    })
                elif len(non_doc) == 1:
                    stmt = non_doc[0]
                    if isinstance(stmt, ast.Pass):
                        violations.append({
                            "type": "empty_function",
                            "name": node.name,
                            "lineno": node.lineno,
                            "message": f"Function '{node.name}' contains only 'pass' stub.",
                        })
                    elif isinstance(stmt, ast.Expr):
                        val = getattr(stmt, "value", None)
                        if (isinstance(val, ast.Constant) and val.value is Ellipsis) or isinstance(val, getattr(ast, "Ellipsis", type(None))):
                            violations.append({
                                "type": "ellipsis_stub",
                                "name": node.name,
                                "lineno": node.lineno,
                                "message": f"Function '{node.name}' contains only ellipsis '...' stub.",
                            })

                for sub in ast.walk(node):
                    if isinstance(sub, ast.Raise) and sub.exc:
                        exc_id = None
                        if isinstance(sub.exc, ast.Name):
                            exc_id = sub.exc.id
                        elif isinstance(sub.exc, ast.Call):
                            if isinstance(sub.exc.func, ast.Name):
                                exc_id = sub.exc.func.id
                            elif isinstance(sub.exc.func, ast.Attribute):
                                exc_id = sub.exc.func.attr
                        if exc_id in ("NotImplementedError", "NotImplemented"):
                            violations.append({
                                "type": "not_implemented",
                                "name": node.name,
                                "lineno": sub.lineno,
                                "message": f"Function '{node.name}' raises '{exc_id}' placeholder.",
                            })

        if self.check_comments:
            for idx, line in enumerate(source.splitlines(), start=1):
                match = self.TODO_PATTERN.search(line)
                if match:
                    violations.append({
                        "type": "stub_comment",
                        "name": match.group(0),
                        "lineno": idx,
                        "message": f"Stub comment '{match.group(0)}' detected on line {idx}.",
                    })

        violations.sort(key=lambda x: (x["lineno"], x["type"]))
        return {
            "isError": False,
            "violations_count": len(violations),
            "violations": violations,
            "clean": len(violations) == 0,
        }



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
            "browser_action": self.browser_action,
            "browse": self.browse,
            "click": self.click,
            "type_text": self.type_text,
            "screenshot": self.screenshot,
            "extract_content": self.extract_content,
            "sort_imports": self.sort_imports,
            "detect_p013": self.detect_p013,
            "detect_stubs": self.detect_p013,
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

    def browser_action(
        self,
        action: str,
        url: Optional[str] = None,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        path: Optional[str] = None,
        full_page: bool = False,
    ) -> Any:
        """Execute Playwright browser automation action (browse, click, type, screenshot, extract_content, close)."""
        from hydra_cli.browser import dispatch_browser_action
        return dispatch_browser_action(
            action=action,
            url=url,
            selector=selector,
            text=text,
            path=path,
            full_page=full_page,
        )

    def browse(self, url: str) -> Any:
        """Navigate to a URL using Playwright browser."""
        return self.browser_action(action="browse", url=url)

    def click(self, selector: str) -> Any:
        """Click an element matching selector using Playwright browser."""
        return self.browser_action(action="click", selector=selector)

    def type_text(self, selector: str, text: str) -> Any:
        """Type text into an element matching selector using Playwright browser."""
        return self.browser_action(action="type", selector=selector, text=text)

    def screenshot(self, path: Optional[str] = None, full_page: bool = False) -> Any:
        """Capture screenshot of current browser page using Playwright browser."""
        return self.browser_action(action="screenshot", path=path, full_page=full_page)

    def extract_content(self, selector: Optional[str] = None) -> Any:
        """Extract text content from browser page or element using Playwright browser."""
        return self.browser_action(action="extract_content", selector=selector)

    def sort_imports(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        in_place: bool = True,
        known_first_party: Optional[List[str]] = None,
        max_line_length: int = 88,
    ) -> Dict[str, Any]:
        """
        Organize and sort Python imports using AST parsing.
        Groups into __future__, standard library, third-party, and first-party blocks.
        Merges redundant from-imports and sorts symbols alphabetically.
        """
        if not path and source is None:
            return {"isError": True, "error": "Either 'path' or 'source' must be provided"}

        sorter = AstImportSorter(
            max_line_length=max_line_length,
            known_first_party=known_first_party,
        )

        if source is not None:
            return sorter.sort_source(source)

        abs_path = os.path.abspath(os.path.join(self.cwd, path))
        if not os.path.exists(abs_path):
            return {"isError": True, "error": f"File not found: {path}"}
        if os.path.isdir(abs_path):
            return {"isError": True, "error": f"Path is a directory, not a file: {path}"}

        try:
            with open(abs_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except Exception as exc:
            return {"isError": True, "error": f"Failed reading file: {exc}"}

        res = sorter.sort_source(content)
        if res.get("isError"):
            return res

        res["path"] = abs_path
        if in_place and res["changed"]:
            tmp_path = abs_path + f".tmp.{uuid.uuid4().hex[:8]}"
            try:
                with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                    f.write(res["sorted_code"])
                os.replace(tmp_path, abs_path)
            except Exception as exc:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                return {"isError": True, "error": f"Failed writing sorted file: {exc}"}

        return res

    def detect_p013(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        check_comments: bool = True,
        check_mocks: bool = True,
    ) -> Dict[str, Any]:
        """
        Scan Python source code or directories for P013 stub violations.
        Detects empty functions, ellipsis stubs, NotImplementedError, docstring-only bodies,
        TODO comments, and banned synthetic mocks.
        """
        detector = P013StubDetector(
            check_comments=check_comments,
            check_mocks=check_mocks,
        )

        if source is not None:
            return detector.detect(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = detector.detect(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        all_violations = []
        files_scanned = 0
        file_summaries = {}

        for root, dirs, files in os.walk(target_path):
            dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS and not d.startswith(".")]
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                file_path = os.path.join(root, filename)
                files_scanned += 1
                try:
                    with open(file_path, "r", encoding="utf-8-sig") as f:
                        file_code = f.read()
                except Exception:
                    continue
                report = detector.detect(file_code)
                if report.get("isError"):
                    continue
                if report["violations_count"] > 0:
                    rel_p = os.path.relpath(file_path, target_path)
                    file_summaries[rel_p] = report["violations_count"]
                    for v in report["violations"]:
                        v_copy = dict(v)
                        v_copy["file"] = rel_p
                        all_violations.append(v_copy)

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "violations_count": len(all_violations),
            "files_with_violations": len(file_summaries),
            "summary": file_summaries,
            "violations": all_violations,
            "clean": len(all_violations) == 0,
        }

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
            {
                "type": "function",
                "function": {
                    "name": "browser_action",
                    "description": "Execute Playwright browser automation (browse web pages, click elements, type input, capture screenshots, and extract text content).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["browse", "click", "type", "screenshot", "extract_content", "close"],
                                "description": "Browser action to execute."
                            },
                            "url": {"type": "string", "description": "Target webpage URL (required for 'browse')."},
                            "selector": {"type": "string", "description": "CSS or text selector (for 'click', 'type', 'extract_content')."},
                            "text": {"type": "string", "description": "Text to enter into element (for 'type')."},
                            "path": {"type": "string", "description": "Local file path to save screenshot (for 'screenshot')."},
                            "full_page": {"type": "boolean", "description": "Whether to capture full scrollable page (for 'screenshot')."}
                        },
                        "required": ["action"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sort_imports",
                    "description": "Organize and sort Python imports using AST analysis. Groups into __future__, stdlib, third-party, and first-party sections with alphabetical symbol sorting.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative or absolute file path to the Python file to sort.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Raw Python source code string to sort if path not provided.",
                            },
                            "in_place": {
                                "type": "boolean",
                                "description": "Whether to rewrite the file in place. Default true.",
                            },
                            "known_first_party": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional list of first-party package names.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "detect_p013",
                    "description": "Scan Python source code or directories for P013 stub violations: empty functions, ellipsis stubs, NotImplementedError, docstring-only bodies, TODO comments, and synthetic mocks.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to scan. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to scan.",
                            },
                            "check_comments": {
                                "type": "boolean",
                                "description": "Whether to detect TODO and FIXME comments. Default true.",
                            },
                            "check_mocks": {
                                "type": "boolean",
                                "description": "Whether to detect banned synthetic mock imports. Default true.",
                            },
                        },
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
