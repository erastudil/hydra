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



class AstComplexityMeter:
    """AST visitor calculating cyclomatic complexity, nesting depth, and function metrics."""

    def __init__(self, threshold: int = 10):
        self.threshold = threshold

    def analyze_source(self, source: str) -> Dict[str, Any]:
        """Analyze Python source code and return structured complexity metrics."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        functions: List[Dict[str, Any]] = []

        def find_functions(node: ast.AST, prefix: str = "") -> List[Tuple[str, ast.AST]]:
            items: List[Tuple[str, ast.AST]] = []
            for child in getattr(node, "body", []):
                if isinstance(child, ast.ClassDef):
                    new_prefix = f"{prefix}{child.name}."
                    items.extend(find_functions(child, prefix=new_prefix))
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn_name = f"{prefix}{child.name}"
                    items.append((fn_name, child))
                    nested_prefix = f"{prefix}{child.name}."
                    items.extend(find_functions(child, prefix=nested_prefix))
            return items

        discovered = find_functions(tree)

        for fn_name, fn_node in discovered:
            lineno = fn_node.lineno
            end_lineno = getattr(fn_node, "end_lineno", lineno)
            lines_count = end_lineno - lineno + 1

            complexity = 1
            max_depth = 0

            def walk_body(subnode: ast.AST, current_depth: int) -> None:
                nonlocal complexity, max_depth
                if current_depth > max_depth:
                    max_depth = current_depth

                for child in ast.iter_child_nodes(subnode):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue

                    is_branch = False
                    is_scope = False

                    if isinstance(child, (ast.If, ast.IfExp)):
                        complexity += 1
                        is_branch = True
                    elif isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                        complexity += 1
                        is_branch = True
                    elif isinstance(child, ast.ExceptHandler):
                        complexity += 1
                        is_branch = True
                    elif isinstance(child, ast.BoolOp):
                        complexity += len(child.values) - 1
                    elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                        for gen in child.generators:
                            complexity += len(gen.ifs)
                    elif hasattr(ast, "match_case") and isinstance(child, ast.match_case):
                        complexity += 1
                        is_branch = True

                    if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)):
                        is_scope = True

                    next_depth = current_depth + (1 if is_scope else 0)
                    walk_body(child, next_depth)

            walk_body(fn_node, 0)

            functions.append({
                "name": fn_name,
                "lineno": lineno,
                "end_lineno": end_lineno,
                "lines_count": lines_count,
                "complexity": complexity,
                "max_nesting_depth": max_depth,
                "is_high_complexity": complexity > self.threshold,
            })

        functions.sort(key=lambda f: (f["lineno"], f["name"]))
        total_complexity = sum(f["complexity"] for f in functions)
        avg_complexity = round(total_complexity / len(functions), 2) if functions else 0.0
        max_c = max((f["complexity"] for f in functions), default=0)
        high_c = [f for f in functions if f["is_high_complexity"]]

        return {
            "isError": False,
            "total_functions": len(functions),
            "total_complexity": total_complexity,
            "average_complexity": avg_complexity,
            "max_complexity": max_c,
            "high_complexity_count": len(high_c),
            "high_complexity_functions": high_c,
            "functions": functions,
        }



class AstTypeAnnotationLinter:
    """AST visitor measuring type annotation coverage and locating untyped parameters and returns."""

    def __init__(self, min_coverage: float = 0.0):
        self.min_coverage = min_coverage

    def analyze_source(self, source: str) -> Dict[str, Any]:
        """Analyze Python source code and return type annotation coverage metrics and missing items."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        functions: List[Tuple[str, ast.AST, bool, bool]] = []

        def find_functions(node: ast.AST, prefix: str = "", in_class: bool = False) -> None:
            for child in getattr(node, "body", []):
                if isinstance(child, ast.ClassDef):
                    new_prefix = f"{prefix}{child.name}."
                    find_functions(child, prefix=new_prefix, in_class=True)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    is_classmethod = False
                    for dec in child.decorator_list:
                        if isinstance(dec, ast.Name) and dec.id in ("classmethod", "staticmethod"):
                            if dec.id == "classmethod":
                                is_classmethod = True
                        elif isinstance(dec, ast.Attribute) and dec.attr in ("classmethod", "staticmethod"):
                            if dec.attr == "classmethod":
                                is_classmethod = True

                    fn_name = f"{prefix}{child.name}"
                    functions.append((fn_name, child, in_class, is_classmethod))
                    nested_prefix = f"{prefix}{child.name}."
                    find_functions(child, prefix=nested_prefix, in_class=False)

        find_functions(tree)

        total_args = 0
        annotated_args = 0
        total_returns = len(functions)
        annotated_returns = 0
        missing: List[Dict[str, Any]] = []

        for fn_name, fn_node, in_class, is_classmethod in functions:
            lineno = fn_node.lineno

            # Return type check
            if fn_node.returns is not None:
                annotated_returns += 1
            else:
                missing.append({
                    "function": fn_name,
                    "lineno": lineno,
                    "kind": "return",
                    "name": "return",
                    "message": f"Function '{fn_name}' missing return type annotation.",
                })

            # Arguments check
            args_obj = fn_node.args
            all_pos = list(args_obj.posonlyargs) + list(args_obj.args)

            # Skip self/cls for first parameter in class methods
            for idx, arg in enumerate(all_pos):
                if in_class and idx == 0 and arg.arg in ("self", "cls"):
                    continue

                total_args += 1
                if arg.annotation is not None:
                    annotated_args += 1
                else:
                    missing.append({
                        "function": fn_name,
                        "lineno": getattr(arg, "lineno", lineno),
                        "kind": "argument",
                        "name": arg.arg,
                        "message": f"Argument '{arg.arg}' in function '{fn_name}' missing type annotation.",
                    })

            for arg in args_obj.kwonlyargs:
                total_args += 1
                if arg.annotation is not None:
                    annotated_args += 1
                else:
                    missing.append({
                        "function": fn_name,
                        "lineno": getattr(arg, "lineno", lineno),
                        "kind": "argument",
                        "name": arg.arg,
                        "message": f"Keyword-only argument '{arg.arg}' in function '{fn_name}' missing type annotation.",
                    })

            if args_obj.vararg:
                total_args += 1
                if args_obj.vararg.annotation is not None:
                    annotated_args += 1
                else:
                    missing.append({
                        "function": fn_name,
                        "lineno": getattr(args_obj.vararg, "lineno", lineno),
                        "kind": "vararg",
                        "name": f"*{args_obj.vararg.arg}",
                        "message": f"Vararg '*{args_obj.vararg.arg}' in function '{fn_name}' missing type annotation.",
                    })

            if args_obj.kwarg:
                total_args += 1
                if args_obj.kwarg.annotation is not None:
                    annotated_args += 1
                else:
                    missing.append({
                        "function": fn_name,
                        "lineno": getattr(args_obj.kwarg, "lineno", lineno),
                        "kind": "kwarg",
                        "name": f"**{args_obj.kwarg.arg}",
                        "message": f"Kwarg '**{args_obj.kwarg.arg}' in function '{fn_name}' missing type annotation.",
                    })

        arg_cov = round((annotated_args / total_args) * 100, 1) if total_args > 0 else 100.0
        ret_cov = round((annotated_returns / total_returns) * 100, 1) if total_returns > 0 else 100.0
        total_items = total_args + total_returns
        annotated_items = annotated_args + annotated_returns
        overall_cov = round((annotated_items / total_items) * 100, 1) if total_items > 0 else 100.0

        missing.sort(key=lambda m: (m["lineno"], m["function"]))
        meets_threshold = overall_cov >= self.min_coverage

        return {
            "isError": False,
            "total_functions": len(functions),
            "total_arguments": total_args,
            "annotated_arguments": annotated_args,
            "annotated_returns": annotated_returns,
            "argument_coverage_pct": arg_cov,
            "return_coverage_pct": ret_cov,
            "overall_coverage_pct": overall_cov,
            "missing_count": len(missing),
            "missing": missing,
            "meets_threshold": meets_threshold,
            "clean": len(missing) == 0,
        }



class AstUnusedVarCleaner:
    """AST visitor detecting and surgically renaming unused local variables in Python source code."""

    def __init__(self, prefix: str = "_"):
        self.prefix = prefix

    def analyze_source(self, source: str, auto_fix: bool = False) -> Dict[str, Any]:
        """Analyze source code for unused local variables with optional surgical underscore prefixing."""
        newline = "\r\n" if "\r\n" in source else "\n"
        code = source.replace("\r\n", "\n")
        has_bom = code.startswith("\ufeff")
        if has_bom:
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        unused_items: List[Dict[str, Any]] = []
        replacements: List[Tuple[int, int, str, str]] = []

        def find_functions(node: ast.AST, prefix: str = "") -> List[Tuple[str, ast.AST]]:
            items: List[Tuple[str, ast.AST]] = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    full_name = f"{prefix}{child.name}"
                    items.append((full_name, child))
                    items.extend(find_functions(child, prefix=f"{full_name}."))
                elif isinstance(child, ast.ClassDef):
                    nested_prefix = f"{prefix}{child.name}."
                    items.extend(find_functions(child, prefix=nested_prefix))
            return items

        functions = find_functions(tree)

        for fn_name, fn_node in functions:
            explicit_nonlocals: Set[str] = set()
            assigned: Dict[str, List[ast.Name]] = {}

            def walk_local_scope(subnode: ast.AST) -> None:
                for child in ast.iter_child_nodes(subnode):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue
                    if isinstance(child, (ast.Global, ast.Nonlocal)):
                        explicit_nonlocals.update(child.names)
                    elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                        if not child.id.startswith(self.prefix):
                            assigned.setdefault(child.id, []).append(child)
                    walk_local_scope(child)

            walk_local_scope(fn_node)

            loaded: Set[str] = set()
            for sub in ast.walk(fn_node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    loaded.add(sub.id)
                elif isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name):
                    loaded.add(sub.target.id)

            for var_name, name_nodes in assigned.items():
                if var_name in explicit_nonlocals:
                    continue
                if var_name not in loaded:
                    for n in name_nodes:
                        unused_items.append({
                            "function": fn_name,
                            "name": var_name,
                            "variable": var_name,
                            "lineno": n.lineno,
                            "col_offset": n.col_offset,
                            "message": f"Local variable '{var_name}' in function '{fn_name}' assigned but never used.",
                        })
                        replacements.append((
                            n.lineno,
                            n.col_offset,
                            var_name,
                            f"{self.prefix}{var_name}",
                        ))

        unused_items.sort(key=lambda u: (u["lineno"], u["col_offset"], u["variable"]))

        if not auto_fix or not unused_items:
            return {
                "isError": False,
                "unused_count": len(unused_items),
                "unused_variables": unused_items,
                "changed": False,
                "cleaned_code": source,
                "clean": len(unused_items) == 0,
            }

        code_lines = code.split("\n")
        replacements.sort(key=lambda r: (r[0], -r[1]))

        for lineno, col, old_name, new_name in replacements:
            if 1 <= lineno <= len(code_lines):
                line = code_lines[lineno - 1]
                if col + len(old_name) <= len(line) and line[col : col + len(old_name)] == old_name:
                    code_lines[lineno - 1] = line[:col] + new_name + line[col + len(old_name):]

        cleaned = "\n".join(code_lines)
        if newline == "\r\n":
            cleaned = cleaned.replace("\n", "\r\n")
        if has_bom:
            cleaned = "\ufeff" + cleaned

        return {
            "isError": False,
            "unused_count": len(unused_items),
            "unused_variables": unused_items,
            "changed": (cleaned != source),
            "cleaned_code": cleaned,
            "clean": len(unused_items) == 0,
        }



class AstDocstringLinter:
    """AST visitor evaluating docstring presence, formatting, and dialect invariants."""

    def __init__(
        self,
        check_functions: bool = True,
        check_classes: bool = True,
        check_modules: bool = False,
        ignore_private: bool = True,
        require_zero_copula: bool = True,
        check_parentheticals: bool = False,
        min_coverage: float = 0.0,
    ):
        self.check_functions = check_functions
        self.check_classes = check_classes
        self.check_modules = check_modules
        self.ignore_private = ignore_private
        self.require_zero_copula = require_zero_copula
        self.check_parentheticals = check_parentheticals
        self.min_coverage = min_coverage

    def _lint_docstring_content(
        self,
        name: str,
        def_type: str,
        lineno: int,
        doc: str,
        violations: List[Dict[str, Any]],
    ) -> None:
        stripped = doc.strip()
        if not stripped:
            violations.append({
                "name": name,
                "type": def_type,
                "lineno": lineno,
                "kind": "empty_docstring",
                "message": f"{def_type.capitalize()} '{name}' has empty or whitespace docstring.",
            })
            return

        if self.require_zero_copula:
            first_word = stripped.split()[0].lower().rstrip(".,:;")
            if first_word in ("is", "are", "was", "were"):
                violations.append({
                    "name": name,
                    "type": def_type,
                    "lineno": lineno,
                    "kind": "leading_copula",
                    "message": f"{def_type.capitalize()} '{name}' docstring starts with leading copula '{first_word}'.",
                })

        if self.check_parentheticals and "(" in doc and ")" in doc:
            violations.append({
                "name": name,
                "type": def_type,
                "lineno": lineno,
                "kind": "parenthetical_in_prose",
                "message": f"{def_type.capitalize()} '{name}' docstring contains parenthetical in prose.",
            })

    def analyze_source(self, source: str) -> Dict[str, Any]:
        """Analyze Python source for missing or non-compliant docstrings."""
        code = source.replace("\r\n", "\n")
        if code.startswith("\ufeff"):
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        definitions: List[Dict[str, Any]] = []
        violations: List[Dict[str, Any]] = []

        if self.check_modules:
            doc = ast.get_docstring(tree)
            if doc is None:
                definitions.append({"name": "<module>", "type": "module", "lineno": 1, "has_doc": False})
                violations.append({
                    "name": "<module>",
                    "type": "module",
                    "lineno": 1,
                    "kind": "missing_docstring",
                    "message": "Module missing docstring.",
                })
            else:
                has_content = bool(doc.strip())
                definitions.append({"name": "<module>", "type": "module", "lineno": 1, "has_doc": has_content})
                self._lint_docstring_content("<module>", "module", 1, doc, violations)

        def walk_definitions(node: ast.AST, prefix: str = "") -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    cls_name = f"{prefix}{child.name}"
                    if not (self.ignore_private and child.name.startswith("_")):
                        if self.check_classes:
                            doc = ast.get_docstring(child)
                            if doc is None:
                                definitions.append({"name": cls_name, "type": "class", "lineno": child.lineno, "has_doc": False})
                                violations.append({
                                    "name": cls_name,
                                    "type": "class",
                                    "lineno": child.lineno,
                                    "kind": "missing_docstring",
                                    "message": f"Class '{cls_name}' missing docstring.",
                                })
                            else:
                                has_content = bool(doc.strip())
                                definitions.append({"name": cls_name, "type": "class", "lineno": child.lineno, "has_doc": has_content})
                                self._lint_docstring_content(cls_name, "class", child.lineno, doc, violations)
                    walk_definitions(child, prefix=f"{cls_name}.")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn_name = f"{prefix}{child.name}"
                    if not (self.ignore_private and child.name.startswith("_")):
                        if self.check_functions:
                            doc = ast.get_docstring(child)
                            if doc is None:
                                definitions.append({"name": fn_name, "type": "function", "lineno": child.lineno, "has_doc": False})
                                violations.append({
                                    "name": fn_name,
                                    "type": "function",
                                    "lineno": child.lineno,
                                    "kind": "missing_docstring",
                                    "message": f"Function '{fn_name}' missing docstring.",
                                })
                            else:
                                has_content = bool(doc.strip())
                                definitions.append({"name": fn_name, "type": "function", "lineno": child.lineno, "has_doc": has_content})
                                self._lint_docstring_content(fn_name, "function", child.lineno, doc, violations)
                    walk_definitions(child, prefix=f"{fn_name}.")

        walk_definitions(tree)

        documented_count = sum(1 for d in definitions if d["has_doc"])
        total_count = len(definitions)
        undocumented_count = total_count - documented_count
        coverage_pct = round((documented_count / total_count) * 100, 1) if total_count > 0 else 100.0

        violations.sort(key=lambda v: (v["lineno"], v["name"]))
        meets_threshold = coverage_pct >= self.min_coverage

        return {
            "isError": False,
            "total_definitions": total_count,
            "documented_count": documented_count,
            "undocumented_count": undocumented_count,
            "coverage_pct": coverage_pct,
            "violations_count": len(violations),
            "violations": violations,
            "meets_threshold": meets_threshold,
            "clean": len(violations) == 0,
        }



class AstConstantFolder:
    """AST visitor evaluating compile-time constant expressions and performing surgical replacements."""

    def __init__(self, max_pow: int = 32, max_str_len: int = 10000):
        self.max_pow = max_pow
        self.max_str_len = max_str_len

    def _eval_node(self, node: ast.AST):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool, str)):
                return True, node.value
            return False, None
        if isinstance(node, ast.UnaryOp):
            ok, val = self._eval_node(node.operand)
            if not ok:
                return False, None
            try:
                if isinstance(node.op, ast.UAdd):
                    return True, +val
                elif isinstance(node.op, ast.USub):
                    return True, -val
                elif isinstance(node.op, ast.Not):
                    return True, not val
                elif isinstance(node.op, ast.Invert) and isinstance(val, int):
                    return True, ~val
            except Exception:
                return False, None
        elif isinstance(node, ast.BinOp):
            ok_l, val_l = self._eval_node(node.left)
            ok_r, val_r = self._eval_node(node.right)
            if not (ok_l and ok_r):
                return False, None
            try:
                if isinstance(node.op, ast.Add):
                    if isinstance(val_l, str) and isinstance(val_r, str) and len(val_l) + len(val_r) <= self.max_str_len:
                        return True, val_l + val_r
                    if isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)):
                        return True, val_l + val_r
                elif isinstance(node.op, ast.Sub):
                    if isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)):
                        return True, val_l - val_r
                elif isinstance(node.op, ast.Mult):
                    if isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)):
                        return True, val_l * val_r
                    if isinstance(val_l, str) and isinstance(val_r, int) and 0 <= val_r <= 1000 and len(val_l) * val_r <= self.max_str_len:
                        return True, val_l * val_r
                    if isinstance(val_r, str) and isinstance(val_l, int) and 0 <= val_l <= 1000 and len(val_r) * val_l <= self.max_str_len:
                        return True, val_l * val_r
                elif isinstance(node.op, ast.Div):
                    if isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)) and val_r != 0:
                        return True, val_l / val_r
                elif isinstance(node.op, ast.FloorDiv):
                    if isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)) and val_r != 0:
                        return True, val_l // val_r
                elif isinstance(node.op, ast.Mod):
                    if isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)) and val_r != 0:
                        return True, val_l % val_r
                elif isinstance(node.op, ast.Pow):
                    if isinstance(val_l, (int, float)) and isinstance(val_r, int) and 0 <= val_r <= self.max_pow:
                        return True, val_l ** val_r
                elif isinstance(node.op, ast.BitAnd):
                    if isinstance(val_l, int) and isinstance(val_r, int):
                        return True, val_l & val_r
                elif isinstance(node.op, ast.BitOr):
                    if isinstance(val_l, int) and isinstance(val_r, int):
                        return True, val_l | val_r
                elif isinstance(node.op, ast.BitXor):
                    if isinstance(val_l, int) and isinstance(val_r, int):
                        return True, val_l ^ val_r
                elif isinstance(node.op, ast.LShift):
                    if isinstance(val_l, int) and isinstance(val_r, int) and 0 <= val_r <= 64:
                        return True, val_l << val_r
                elif isinstance(node.op, ast.RShift):
                    if isinstance(val_l, int) and isinstance(val_r, int) and 0 <= val_r <= 64:
                        return True, val_l >> val_r
            except Exception:
                return False, None
        elif isinstance(node, ast.BoolOp):
            vals = []
            for v in node.values:
                ok, val = self._eval_node(v)
                if not ok:
                    return False, None
                vals.append(val)
            try:
                if isinstance(node.op, ast.And):
                    res = vals[0]
                    for item in vals[1:]:
                        res = res and item
                    return True, res
                elif isinstance(node.op, ast.Or):
                    res = vals[0]
                    for item in vals[1:]:
                        res = res or item
                    return True, res
            except Exception:
                return False, None
        return False, None

    def fold_source(self, source: str, auto_fix: bool = False) -> Dict[str, Any]:
        """Evaluate compile-time constant expressions and optionally fold in place."""
        newline = "\r\n" if "\r\n" in source else "\n"
        code = source.replace("\r\n", "\n")
        has_bom = code.startswith("\ufeff")
        if has_bom:
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        parent_map = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent

        candidates = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp)):
                ok, val = self._eval_node(node)
                if ok:
                    candidates.append((node, val))

        candidate_nodes = {c[0] for c in candidates}
        top_candidates = []
        for node, val in candidates:
            cur = parent_map.get(node)
            is_sub = False
            while cur is not None:
                if cur in candidate_nodes:
                    is_sub = True
                    break
                cur = parent_map.get(cur)
            if not is_sub:
                top_candidates.append((node, val))

        foldable_items = []
        replacements = []

        code_lines = code.split("\n")

        for node, val in top_candidates:
            sl = node.lineno
            sc = node.col_offset
            el = getattr(node, "end_lineno", sl)
            ec = getattr(node, "end_col_offset", sc)
            rep_str = repr(val)
            foldable_items.append({
                "lineno": sl,
                "col_offset": sc,
                "end_lineno": el,
                "end_col_offset": ec,
                "value": rep_str,
                "type": type(val).__name__,
            })
            replacements.append((sl, sc, el, ec, rep_str))

        foldable_items.sort(key=lambda item: (item["lineno"], item["col_offset"]))

        if not auto_fix or not replacements:
            return {
                "isError": False,
                "foldable_count": len(foldable_items),
                "foldable_items": foldable_items,
                "changed": False,
                "folded_code": source,
                "clean": len(foldable_items) == 0,
            }

        replacements.sort(key=lambda r: (r[0], r[1]), reverse=True)
        work_lines = list(code_lines)

        for sl, sc, el, ec, rep_str in replacements:
            if sl == el:
                if 1 <= sl <= len(work_lines):
                    line = work_lines[sl - 1]
                    work_lines[sl - 1] = line[:sc] + rep_str + line[ec:]
            else:
                if 1 <= sl <= len(work_lines) and 1 <= el <= len(work_lines):
                    prefix = work_lines[sl - 1][:sc]
                    suffix = work_lines[el - 1][ec:]
                    work_lines[sl - 1 : el] = [prefix + rep_str + suffix]

        cleaned = "\n".join(work_lines)
        if newline == "\r\n":
            cleaned = cleaned.replace("\n", "\r\n")
        if has_bom:
            cleaned = "\ufeff" + cleaned

        return {
            "isError": False,
            "foldable_count": len(foldable_items),
            "foldable_items": foldable_items,
            "changed": (cleaned != source),
            "folded_code": cleaned,
            "clean": len(foldable_items) == 0,
        }



class AstMockTestDetector:
    """AST visitor detecting synthetic mocks, patches, and fake test assertions."""

    BANNED_MODULES = {
        "unittest.mock",
        "mock",
        "pytest_mock",
        "responses",
        "requests_mock",
        "flexmock",
        "freezegun",
        "doublex",
        "vcr",
    }

    BANNED_CALLS = {
        "Mock",
        "MagicMock",
        "AsyncMock",
        "PropertyMock",
        "NonCallableMock",
        "NonCallableMagicMock",
        "patch",
        "mocker",
    }

    BANNED_ASSERTIONS = {
        "assert_called",
        "assert_called_once",
        "assert_called_with",
        "assert_called_once_with",
        "assert_any_call",
        "assert_not_called",
        "assert_has_calls",
    }

    def analyze_source(self, source: str) -> Dict[str, Any]:
        """Scan source code for banned mock objects, imports, patches, and assertions."""
        code = source.replace("\r\n", "\n")
        if code.startswith("\ufeff"):
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        violations: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if alias.name in self.BANNED_MODULES or root_mod in self.BANNED_MODULES or alias.name.startswith("unittest.mock"):
                        violations.append({
                            "kind": "mock_import",
                            "target": alias.name,
                            "lineno": node.lineno,
                            "col_offset": getattr(node, "col_offset", 0),
                            "message": f"Synthetic mock module '{alias.name}' banned under zero-fake-test invariant.",
                        })
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if node.module in self.BANNED_MODULES or root_mod in self.BANNED_MODULES or node.module.startswith("unittest.mock"):
                        for alias in node.names:
                            violations.append({
                                "kind": "mock_import",
                                "target": f"{node.module}.{alias.name}",
                                "lineno": node.lineno,
                                "col_offset": getattr(node, "col_offset", 0),
                                "message": f"Synthetic mock import '{node.module}.{alias.name}' banned under zero-fake-test invariant.",
                            })
                    else:
                        for alias in node.names:
                            if alias.name in self.BANNED_CALLS:
                                violations.append({
                                    "kind": "mock_import",
                                    "target": f"{node.module}.{alias.name}",
                                    "lineno": node.lineno,
                                    "col_offset": getattr(node, "col_offset", 0),
                                    "message": f"Synthetic mock symbol '{alias.name}' from '{node.module}' banned under zero-fake-test invariant.",
                                })

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in node.decorator_list:
                    dec_name = None
                    if isinstance(dec, ast.Name):
                        dec_name = dec.id
                    elif isinstance(dec, ast.Attribute):
                        dec_name = dec.attr
                    elif isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Name):
                            dec_name = dec.func.id
                        elif isinstance(dec.func, ast.Attribute):
                            dec_name = dec.func.attr
                    if dec_name and (dec_name in ("patch", "mocker") or "mock" in dec_name.lower()):
                        violations.append({
                            "kind": "mock_decorator",
                            "target": dec_name,
                            "lineno": getattr(dec, "lineno", node.lineno),
                            "col_offset": getattr(dec, "col_offset", 0),
                            "message": f"Synthetic patch/mock decorator '@{dec_name}' banned under zero-fake-test invariant.",
                        })

                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg in node.args.args:
                        if arg.arg in ("mocker", "monkeypatch") or arg.arg.startswith("mock_"):
                            violations.append({
                                "kind": "mock_argument",
                                "target": arg.arg,
                                "lineno": getattr(arg, "lineno", node.lineno),
                                "col_offset": getattr(arg, "col_offset", 0),
                                "message": f"Test function parameter '{arg.arg}' introduces synthetic mock fixture.",
                            })

            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                    if isinstance(node.func.value, ast.Name) and node.func.value.id in ("mocker", "monkeypatch", "mock"):
                        violations.append({
                            "kind": "mock_call",
                            "target": f"{node.func.value.id}.{node.func.attr}",
                            "lineno": node.lineno,
                            "col_offset": getattr(node, "col_offset", 0),
                            "message": f"Synthetic mock call '{node.func.value.id}.{node.func.attr}()' banned under zero-fake-test invariant.",
                        })

                if func_name in self.BANNED_CALLS:
                    violations.append({
                        "kind": "mock_call",
                        "target": func_name,
                        "lineno": node.lineno,
                        "col_offset": getattr(node, "col_offset", 0),
                        "message": f"Synthetic mock call '{func_name}()' banned under zero-fake-test invariant.",
                    })
                elif func_name in self.BANNED_ASSERTIONS:
                    violations.append({
                        "kind": "mock_assertion",
                        "target": func_name,
                        "lineno": node.lineno,
                        "col_offset": getattr(node, "col_offset", 0),
                        "message": f"Mock assertion '{func_name}()' banned under zero-fake-test invariant.",
                    })

        violations.sort(key=lambda v: (v["lineno"], v["col_offset"], v["kind"]))

        return {
            "isError": False,
            "violations_count": len(violations),
            "violations": violations,
            "clean": len(violations) == 0,
        }



class AstPonytailAnalyzer:
    """AST visitor evaluating code indirection, ceremonial wrappers, and ponytail wu wei compliance."""

    def __init__(self, max_chain_depth: int = 4):
        self.max_chain_depth = max_chain_depth

    def analyze_source(self, source: str) -> Dict[str, Any]:
        """Analyze Python source for ceremonial wrappers, redundant assignments, and indirection."""
        code = source.replace("\r\n", "\n")
        if code.startswith("\ufeff"):
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        violations: List[Dict[str, Any]] = []
        total_functions = 0
        total_classes = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                total_classes += 1
                methods = [m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                non_doc_body = [
                    s for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))
                ]
                if len(methods) == 1 and len(non_doc_body) == 1:
                    m = methods[0]
                    if m.name != "__init__":
                        violations.append({
                            "kind": "ceremonial_class",
                            "name": node.name,
                            "lineno": node.lineno,
                            "col_offset": getattr(node, "col_offset", 0),
                            "message": f"Class '{node.name}' wraps single method '{m.name}' without state; convert to pure function.",
                        })

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_functions += 1
                non_doc_body = [
                    s for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))
                ]
                if len(non_doc_body) == 1 and isinstance(non_doc_body[0], ast.Return):
                    ret_val = non_doc_body[0].value
                    if isinstance(ret_val, ast.Call):
                        fn_args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
                        call_args = []
                        all_names = True
                        for a in ret_val.args:
                            if isinstance(a, ast.Name):
                                call_args.append(a.id)
                            else:
                                all_names = False
                        if all_names and fn_args and fn_args == call_args:
                            target_name = "target"
                            if isinstance(ret_val.func, ast.Name):
                                target_name = ret_val.func.id
                            elif isinstance(ret_val.func, ast.Attribute):
                                target_name = ret_val.func.attr
                            violations.append({
                                "kind": "ceremonial_forwarder",
                                "name": node.name,
                                "lineno": node.lineno,
                                "col_offset": getattr(node, "col_offset", 0),
                                "message": f"Function '{node.name}' performs ceremonial pass-through forwarding to '{target_name}'.",
                            })

                for i in range(len(node.body) - 1):
                    stmt1 = node.body[i]
                    stmt2 = node.body[i + 1]
                    if isinstance(stmt1, ast.Assign) and len(stmt1.targets) == 1 and isinstance(stmt1.targets[0], ast.Name):
                        assigned_var = stmt1.targets[0].id
                        if isinstance(stmt2, ast.Return) and isinstance(stmt2.value, ast.Name) and stmt2.value.id == assigned_var:
                            violations.append({
                                "kind": "redundant_return_assignment",
                                "name": assigned_var,
                                "lineno": stmt1.lineno,
                                "col_offset": getattr(stmt1, "col_offset", 0),
                                "message": f"Redundant intermediate variable '{assigned_var}' immediately returned at line {stmt2.lineno}.",
                            })

            elif isinstance(node, ast.Attribute):
                depth = 1
                cur = node.value
                while isinstance(cur, ast.Attribute):
                    depth += 1
                    cur = cur.value
                if depth > self.max_chain_depth:
                    violations.append({
                        "kind": "deep_call_chain",
                        "name": f"depth_{depth}",
                        "lineno": node.lineno,
                        "col_offset": getattr(node, "col_offset", 0),
                        "message": f"Attribute access chain depth {depth} exceeds threshold {self.max_chain_depth}.",
                    })

        seen_chains = set()
        unique_violations = []
        for v in violations:
            if v["kind"] == "deep_call_chain":
                key = (v["lineno"], v["col_offset"])
                if key in seen_chains:
                    continue
                seen_chains.add(key)
            unique_violations.append(v)

        unique_violations.sort(key=lambda item: (item["lineno"], item["col_offset"], item["kind"]))

        score = max(0.0, round(100.0 - (len(unique_violations) * 10.0), 1))

        return {
            "isError": False,
            "total_functions": total_functions,
            "total_classes": total_classes,
            "violations_count": len(unique_violations),
            "ponytail_score": score,
            "violations": unique_violations,
            "clean": len(unique_violations) == 0,
        }



class AstP014MetaphorDetector:
    """AST visitor detecting P014 metaphor violations, idioms, and machine-shop terminology."""

    METAPHOR_IDIOMS = (
        "under the hood",
        "silver bullet",
        "magic bullet",
        "magic wand",
        "secret sauce",
        "reinvent the wheel",
        "boil the ocean",
        "move the needle",
        "hit the ground running",
        "tip of the iceberg",
        "bite the bullet",
        "low hanging fruit",
        "go with the flow",
        "water down a hill",
        "like water",
    )

    MACHINE_SHOP_TOKENS = (
        "anvil",
        "crucible",
        "smelt",
        "smelting",
        "quenching",
        "lathe",
    )

    def analyze_source(self, source: str) -> Dict[str, Any]:
        """Analyze Python source for banned P014 metaphors, idioms, and machine-shop tokens."""
        code = source.replace("\r\n", "\n")
        if code.startswith("\ufeff"):
            code = code[1:]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"isError": True, "error": f"SyntaxError: {exc.msg} at line {exc.lineno}"}

        violations: List[Dict[str, Any]] = []

        # 1. Inspect docstrings and string constants in AST
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text_lower = node.value.lower()
                for idiom in self.METAPHOR_IDIOMS:
                    if idiom in text_lower:
                        violations.append({
                            "kind": "figurative_idiom",
                            "target": idiom,
                            "lineno": node.lineno,
                            "col_offset": getattr(node, "col_offset", 0),
                            "message": f"Banned figurative idiom '{idiom}' in string/docstring violates P014 invariant.",
                        })
                for token in self.MACHINE_SHOP_TOKENS:
                    if re.search(r"\b" + re.escape(token) + r"\b", text_lower):
                        violations.append({
                            "kind": "machine_shop_token",
                            "target": token,
                            "lineno": node.lineno,
                            "col_offset": getattr(node, "col_offset", 0),
                            "message": f"Banned machine-shop token '{token}' in string/docstring violates P014 invariant.",
                        })

        # 2. Inspect comments in source lines
        for idx, line in enumerate(code.split("\n"), start=1):
            if "#" in line:
                comment_part = line[line.find("#"):]
                comment_lower = comment_part.lower()
                for idiom in self.METAPHOR_IDIOMS:
                    if idiom in comment_lower:
                        violations.append({
                            "kind": "comment_metaphor",
                            "target": idiom,
                            "lineno": idx,
                            "col_offset": line.find("#"),
                            "message": f"Banned figurative idiom '{idiom}' in comment violates P014 invariant.",
                        })
                for token in self.MACHINE_SHOP_TOKENS:
                    if re.search(r"\b" + re.escape(token) + r"\b", comment_lower):
                        violations.append({
                            "kind": "comment_machine_shop",
                            "target": token,
                            "lineno": idx,
                            "col_offset": line.find("#"),
                            "message": f"Banned machine-shop token '{token}' in comment violates P014 invariant.",
                        })

        seen = set()
        unique = []
        for v in violations:
            key = (v["lineno"], v["target"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(v)

        unique.sort(key=lambda item: (item["lineno"], item["col_offset"], item["kind"]))

        return {
            "isError": False,
            "violations_count": len(unique),
            "violations": unique,
            "clean": len(unique) == 0,
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
            "measure_complexity": self.measure_complexity,
            "complexity_meter": self.measure_complexity,
            "check_type_annotations": self.check_type_annotations,
            "lint_type_annotations": self.check_type_annotations,
            "clean_unused_variables": self.clean_unused_variables,
            "find_unused_variables": self.clean_unused_variables,
            "unused_var_cleaner": self.clean_unused_variables,
            "lint_docstrings": self.lint_docstrings,
            "check_docstrings": self.lint_docstrings,
            "docstring_linter": self.lint_docstrings,
            "fold_constants": self.fold_constants,
            "constant_folder": self.fold_constants,
            "ban_mock_tests": self.ban_mock_tests,
            "check_mock_tests": self.ban_mock_tests,
            "detect_mock_tests": self.ban_mock_tests,
            "analyze_ponytail": self.analyze_ponytail,
            "ponytail_analyzer": self.analyze_ponytail,
            "check_ponytail": self.analyze_ponytail,
            "detect_p014": self.detect_p014,
            "ban_p014_metaphors": self.detect_p014,
            "check_p014": self.detect_p014,
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

    def measure_complexity(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        threshold: int = 10,
    ) -> Dict[str, Any]:
        """
        Measure Cyclomatic Complexity and nesting depth for Python source code or directory.
        Returns function-level breakdown and flags functions exceeding threshold.
        """
        meter = AstComplexityMeter(threshold=threshold)

        if source is not None:
            return meter.analyze_source(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = meter.analyze_source(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        files_scanned = 0
        all_functions = []
        high_complexity_all = []
        file_metrics = {}

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
                rep = meter.analyze_source(file_code)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                file_metrics[rel_p] = {
                    "functions_count": rep["total_functions"],
                    "total_complexity": rep["total_complexity"],
                    "average_complexity": rep["average_complexity"],
                    "max_complexity": rep["max_complexity"],
                }
                for fn in rep["functions"]:
                    fn_copy = dict(fn)
                    fn_copy["file"] = rel_p
                    all_functions.append(fn_copy)
                    if fn["is_high_complexity"]:
                        high_complexity_all.append(fn_copy)

        total_c = sum(fn["complexity"] for fn in all_functions)
        avg_c = round(total_c / len(all_functions), 2) if all_functions else 0.0
        max_c = max((fn["complexity"] for fn in all_functions), default=0)

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_functions": len(all_functions),
            "total_complexity": total_c,
            "average_complexity": avg_c,
            "max_complexity": max_c,
            "high_complexity_count": len(high_complexity_all),
            "high_complexity_functions": high_complexity_all,
            "file_metrics": file_metrics,
        }

    def check_type_annotations(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        min_coverage: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Evaluate type annotation coverage across functions, methods, files, or directories.
        Identifies untyped arguments and missing return types.
        """
        linter = AstTypeAnnotationLinter(min_coverage=min_coverage)

        if source is not None:
            return linter.analyze_source(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = linter.analyze_source(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        files_scanned = 0
        total_fns = 0
        total_args = 0
        annotated_args = 0
        annotated_returns = 0
        all_missing = []
        file_metrics = {}

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
                rep = linter.analyze_source(file_code)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                file_metrics[rel_p] = {
                    "functions": rep["total_functions"],
                    "overall_coverage_pct": rep["overall_coverage_pct"],
                    "missing_count": rep["missing_count"],
                }
                total_fns += rep["total_functions"]
                total_args += rep["total_arguments"]
                annotated_args += rep["annotated_arguments"]
                annotated_returns += rep["annotated_returns"]
                for item in rep["missing"]:
                    item_copy = dict(item)
                    item_copy["file"] = rel_p
                    all_missing.append(item_copy)

        total_items = total_args + total_fns
        annotated_items = annotated_args + annotated_returns
        overall_cov = round((annotated_items / total_items) * 100, 1) if total_items > 0 else 100.0

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_functions": total_fns,
            "total_arguments": total_args,
            "annotated_arguments": annotated_args,
            "annotated_returns": annotated_returns,
            "overall_coverage_pct": overall_cov,
            "missing_count": len(all_missing),
            "missing": all_missing,
            "file_metrics": file_metrics,
            "meets_threshold": overall_cov >= min_coverage,
            "clean": len(all_missing) == 0,
        }

    def clean_unused_variables(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        auto_fix: bool = False,
        in_place: bool = False,
    ) -> Dict[str, Any]:
        """
        Detect and optionally clean unused local variables in Python functions.
        Prefixes unused variables with an underscore to satisfy lint invariants.
        """
        cleaner = AstUnusedVarCleaner()

        if source is not None:
            return cleaner.analyze_source(source, auto_fix=auto_fix)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = cleaner.analyze_source(content, auto_fix=auto_fix)
            if res.get("isError"):
                return res
            res["path"] = target_path
            if in_place and auto_fix and res["changed"]:
                tmp_path = target_path + f".tmp.{uuid.uuid4().hex[:8]}"
                try:
                    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                        f.write(res["cleaned_code"])
                    os.replace(tmp_path, target_path)
                except Exception as exc:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                    return {"isError": True, "error": f"Failed writing cleaned file: {exc}"}
            return res

        # Directory recursive scan
        files_scanned = 0
        total_unused = 0
        all_unused = []
        files_modified = 0
        file_metrics = {}

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
                rep = cleaner.analyze_source(file_code, auto_fix=auto_fix)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                if rep["unused_count"] > 0:
                    file_metrics[rel_p] = rep["unused_count"]
                    total_unused += rep["unused_count"]
                    for u in rep["unused_variables"]:
                        u_copy = dict(u)
                        u_copy["file"] = rel_p
                        all_unused.append(u_copy)
                    if in_place and auto_fix and rep["changed"]:
                        tmp_p = file_path + f".tmp.{uuid.uuid4().hex[:8]}"
                        try:
                            with open(tmp_p, "w", encoding="utf-8", newline="") as f:
                                f.write(rep["cleaned_code"])
                            os.replace(tmp_p, file_path)
                            files_modified += 1
                        except Exception:
                            if os.path.exists(tmp_p):
                                try:
                                    os.remove(tmp_p)
                                except OSError:
                                    pass

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_unused": total_unused,
            "files_with_unused": len(file_metrics),
            "files_modified": files_modified,
            "summary": file_metrics,
            "unused_variables": all_unused,
            "clean": total_unused == 0,
        }

    def lint_docstrings(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        check_functions: bool = True,
        check_classes: bool = True,
        check_modules: bool = False,
        ignore_private: bool = True,
        require_zero_copula: bool = True,
        check_parentheticals: bool = False,
        min_coverage: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Evaluate docstring presence, coverage, and dialect formatting invariants.
        Inspects functions, classes, and modules for documentation conformance.
        """
        linter = AstDocstringLinter(
            check_functions=check_functions,
            check_classes=check_classes,
            check_modules=check_modules,
            ignore_private=ignore_private,
            require_zero_copula=require_zero_copula,
            check_parentheticals=check_parentheticals,
            min_coverage=min_coverage,
        )

        if source is not None:
            return linter.analyze_source(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = linter.analyze_source(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        files_scanned = 0
        total_defs = 0
        total_docs = 0
        total_undocs = 0
        all_violations = []
        file_metrics = {}

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
                rep = linter.analyze_source(file_code)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                file_metrics[rel_p] = {
                    "total_definitions": rep["total_definitions"],
                    "coverage_pct": rep["coverage_pct"],
                    "violations_count": rep["violations_count"],
                }
                total_defs += rep["total_definitions"]
                total_docs += rep["documented_count"]
                total_undocs += rep["undocumented_count"]
                for item in rep["violations"]:
                    item_copy = dict(item)
                    item_copy["file"] = rel_p
                    all_violations.append(item_copy)

        overall_cov = round((total_docs / total_defs) * 100, 1) if total_defs > 0 else 100.0
        meets_threshold = overall_cov >= min_coverage

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_definitions": total_defs,
            "documented_count": total_docs,
            "undocumented_count": total_undocs,
            "overall_coverage_pct": overall_cov,
            "total_violations": len(all_violations),
            "meets_threshold": meets_threshold,
            "files_with_violations": sum(1 for m in file_metrics.values() if m["violations_count"] > 0),
            "summary": file_metrics,
            "violations": all_violations,
            "clean": len(all_violations) == 0,
        }

    def fold_constants(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        auto_fix: bool = False,
        in_place: bool = False,
        max_pow: int = 32,
        max_str_len: int = 10000,
    ) -> Dict[str, Any]:
        """
        Evaluate and optionally fold compile-time constant expressions in Python source code.
        Replaces deterministic arithmetic and boolean literals with evaluated constants.
        """
        folder = AstConstantFolder(max_pow=max_pow, max_str_len=max_str_len)

        if source is not None:
            return folder.fold_source(source, auto_fix=auto_fix)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = folder.fold_source(content, auto_fix=auto_fix)
            if res.get("isError"):
                return res
            res["path"] = target_path
            if in_place and auto_fix and res["changed"]:
                tmp_path = target_path + f".tmp.{uuid.uuid4().hex[:8]}"
                try:
                    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                        f.write(res["folded_code"])
                    os.replace(tmp_path, target_path)
                except Exception as exc:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                    return {"isError": True, "error": f"Failed writing folded file: {exc}"}
            return res

        # Directory recursive scan
        files_scanned = 0
        total_foldable = 0
        all_foldable = []
        files_modified = 0
        file_metrics = {}

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
                rep = folder.fold_source(file_code, auto_fix=auto_fix)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                if rep["foldable_count"] > 0:
                    file_metrics[rel_p] = rep["foldable_count"]
                    total_foldable += rep["foldable_count"]
                    for item in rep["foldable_items"]:
                        item_copy = dict(item)
                        item_copy["file"] = rel_p
                        all_foldable.append(item_copy)
                    if in_place and auto_fix and rep["changed"]:
                        tmp_p = file_path + f".tmp.{uuid.uuid4().hex[:8]}"
                        try:
                            with open(tmp_p, "w", encoding="utf-8", newline="") as f:
                                f.write(rep["folded_code"])
                            os.replace(tmp_p, file_path)
                            files_modified += 1
                        except Exception:
                            if os.path.exists(tmp_p):
                                try:
                                    os.remove(tmp_p)
                                except OSError:
                                    pass

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_foldable": total_foldable,
            "files_with_foldable": len(file_metrics),
            "files_modified": files_modified,
            "summary": file_metrics,
            "foldable_items": all_foldable,
            "clean": total_foldable == 0,
        }

    def ban_mock_tests(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scan Python source code or test directories for banned synthetic mocks, patches, and fake assertions.
        Enforces sovereign zero-fake-test invariant A5 across codebase.
        """
        detector = AstMockTestDetector()

        if source is not None:
            return detector.analyze_source(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = detector.analyze_source(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        files_scanned = 0
        total_violations = 0
        all_violations = []
        file_metrics = {}

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
                rep = detector.analyze_source(file_code)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                if rep["violations_count"] > 0:
                    file_metrics[rel_p] = rep["violations_count"]
                    total_violations += rep["violations_count"]
                    for item in rep["violations"]:
                        item_copy = dict(item)
                        item_copy["file"] = rel_p
                        all_violations.append(item_copy)

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_violations": total_violations,
            "files_with_violations": len(file_metrics),
            "summary": file_metrics,
            "violations": all_violations,
            "clean": total_violations == 0,
        }

    def analyze_ponytail(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
        max_chain_depth: int = 4,
    ) -> Dict[str, Any]:
        """
        Evaluate code indirection, ceremonial wrappers, and ponytail wu wei compliance.
        Flags pass-through forwarders, redundant assignments, and sprawling chains.
        """
        analyzer = AstPonytailAnalyzer(max_chain_depth=max_chain_depth)

        if source is not None:
            return analyzer.analyze_source(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = analyzer.analyze_source(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        files_scanned = 0
        total_violations = 0
        all_violations = []
        file_metrics = {}

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
                rep = analyzer.analyze_source(file_code)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                if rep["violations_count"] > 0:
                    file_metrics[rel_p] = rep["violations_count"]
                    total_violations += rep["violations_count"]
                    for item in rep["violations"]:
                        item_copy = dict(item)
                        item_copy["file"] = rel_p
                        all_violations.append(item_copy)

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_violations": total_violations,
            "files_with_violations": len(file_metrics),
            "summary": file_metrics,
            "violations": all_violations,
            "clean": total_violations == 0,
        }

    def detect_p014(
        self,
        path: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scan Python source code, docstrings, and comments for banned P014 metaphors and machine-shop tokens.
        Enforces sovereign invariant P014 requiring mathematical and computational predicates directly.
        """
        detector = AstP014MetaphorDetector()

        if source is not None:
            return detector.analyze_source(source)

        target_path = os.path.abspath(os.path.join(self.cwd, path or "."))
        if not os.path.exists(target_path):
            return {"isError": True, "error": f"Path not found: {path or '.'}"}

        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
            except Exception as exc:
                return {"isError": True, "error": f"Failed reading file: {exc}"}
            res = detector.analyze_source(content)
            if res.get("isError"):
                return res
            res["path"] = target_path
            return res

        # Directory recursive scan
        files_scanned = 0
        total_violations = 0
        all_violations = []
        file_metrics = {}

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
                rep = detector.analyze_source(file_code)
                if rep.get("isError"):
                    continue
                rel_p = os.path.relpath(file_path, target_path)
                if rep["violations_count"] > 0:
                    file_metrics[rel_p] = rep["violations_count"]
                    total_violations += rep["violations_count"]
                    for item in rep["violations"]:
                        item_copy = dict(item)
                        item_copy["file"] = rel_p
                        all_violations.append(item_copy)

        return {
            "isError": False,
            "path": target_path,
            "files_scanned": files_scanned,
            "total_violations": total_violations,
            "files_with_violations": len(file_metrics),
            "summary": file_metrics,
            "violations": all_violations,
            "clean": total_violations == 0,
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
            {
                "type": "function",
                "function": {
                    "name": "measure_complexity",
                    "description": "Calculate Cyclomatic Complexity, nesting depth, and line counts for Python functions, methods, files, or directories.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to analyze. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to analyze.",
                            },
                            "threshold": {
                                "type": "integer",
                                "description": "Cyclomatic complexity threshold for flagging high-complexity functions. Default 10.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_type_annotations",
                    "description": "Evaluate type annotation coverage across functions, methods, files, or directories. Identifies untyped arguments and missing return types.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to analyze. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to analyze.",
                            },
                            "min_coverage": {
                                "type": "number",
                                "description": "Minimum type coverage percentage threshold. Default 0.0.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "detect_p014",
                    "description": "Scan Python source code, docstrings, and comments for banned P014 metaphors and machine-shop tokens.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to inspect. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to inspect.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_ponytail",
                    "description": "Evaluate code indirection, ceremonial wrappers, and ponytail wu wei compliance.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to inspect. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to inspect.",
                            },
                            "max_chain_depth": {
                                "type": "integer",
                                "description": "Maximum allowed attribute access chain depth. Default 4.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ban_mock_tests",
                    "description": "Scan Python source code or test directories for banned synthetic mocks, patches, and fake assertions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to inspect. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to inspect.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fold_constants",
                    "description": "Evaluate and optionally fold compile-time constant arithmetic and boolean expressions in Python code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to inspect. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to inspect.",
                            },
                            "auto_fix": {
                                "type": "boolean",
                                "description": "Whether to replace constant expressions with evaluated values. Default false.",
                            },
                            "in_place": {
                                "type": "boolean",
                                "description": "Whether to rewrite target file in place when auto_fix is enabled. Default false.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lint_docstrings",
                    "description": "Lint Python docstring presence, coverage, and dialect formatting invariants across files or code snippets.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to inspect. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to inspect.",
                            },
                            "check_functions": {
                                "type": "boolean",
                                "description": "Whether to check function and method docstrings. Default true.",
                            },
                            "check_classes": {
                                "type": "boolean",
                                "description": "Whether to check class docstrings. Default true.",
                            },
                            "check_modules": {
                                "type": "boolean",
                                "description": "Whether to check module docstrings. Default false.",
                            },
                            "ignore_private": {
                                "type": "boolean",
                                "description": "Whether to ignore private definitions starting with underscore. Default true.",
                            },
                            "require_zero_copula": {
                                "type": "boolean",
                                "description": "Whether to enforce zero leading copula invariant P018 in docstrings. Default true.",
                            },
                            "min_coverage": {
                                "type": "number",
                                "description": "Minimum acceptable docstring coverage percentage. Default 0.0.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clean_unused_variables",
                    "description": "Detect and optionally clean unused local variables in Python functions by prefixing with an underscore.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to inspect. Defaults to workspace root.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Optional raw Python source code string to inspect.",
                            },
                            "auto_fix": {
                                "type": "boolean",
                                "description": "Whether to rename unused variables with leading underscore. Default false.",
                            },
                            "in_place": {
                                "type": "boolean",
                                "description": "Whether to rewrite target file in place when auto_fix is enabled. Default false.",
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
