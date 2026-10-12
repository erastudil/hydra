"""Hydra memory ledger and gate: stdlib only, one home, append-only.

Home: $HYDRA_HOME/memory (default ~/.hydra/memory). The ledger is the only
source of truth: ledger.jsonl, one hash-chained JSON line per fact, decision or
event. Nothing is edited or truncated; a correction is a new supersede line.
scripts/gate.py is the command line over this module.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

KINDS = ("fact", "supersede", "verify", "decision", "event")
CLASSES = ("state", "preference", "note")
REASONS = (
    "stale_probe_failed", "contradicted_by_user", "contradicted_by_probe",
    "superseded", "expired", "unverifiable",
)
SOURCE_RANK = {"user": 3, "probe": 2, "agent": 1}
PROTECTED_BRANCHES = ("main", "master")
TRAILER = "Ledger-Head"
HOOK_MARK = "# hydra-gate managed hook"
HOOKS = ("pre-commit", "commit-msg", "pre-push")

# The one rule table. Every gate and sweep rule reads from here.
RULES = {
    "test_delete_max": 5,
    "test_delete_window_s": 3600,
    "blob_max_bytes": 300 * 1024,
    "stale_untracked_days": 14,
    "stale_branch_behind": 30,
    "root_allow": [
        "README*", "LICENSE*", "AGENTS.md", "CLAUDE.md", ".gitignore", ".gitattributes",
        "pyproject.toml", "setup.py", "setup.cfg", "package.json", "package-lock.json",
        "requirements*.txt", "Makefile", "install.sh", "install.ps1", "hydra.bat",
        "verify.sh", "verify.ps1", "index.html", ".editorconfig", "tsconfig.json",
    ],
    "dir_allow": ["src", "scripts", "docs", "assets", "tests", "test", "bin", "tools", "lib", "hydra_cli", "launch", ".github"],
    "junk": ["*.bak", "*~", "copy of *", "tmp.*", "tmp_*", "tmp-*", "*.orig"],
    "dup_ignore": ["__init__.py", "readme*", "spec*", "license*", "index.*", "package.json", ".gitignore", "claude.md", "agents.md"],
    "launcher_ext": (".bat", ".cmd", ".lnk"),
    "launcher_max": 2,
    "gitignore_required": ["*.bak", "*~", "tmp*", ".hydra/", "purgatory/", "*.log", ".venv/"],
    "doc_lint_dirs": ["docs"],
    "win_path": r"[A-Za-z]:[\\/]+Users[\\/]+[\w.-]+",
}


class GateError(Exception):
    pass


# ---------------------------------------------------------------- paths, git

def home() -> str:
    custom = os.environ.get("HYDRA_HOME")
    base = os.path.expanduser(custom) if custom else os.path.join(os.path.expanduser("~"), ".hydra")
    return os.path.join(base, "memory")


def ledger_path() -> str:
    return os.path.join(home(), "ledger.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(repo: str, *args: str, env: Optional[Dict[str, str]] = None, stdin: Optional[str] = None) -> Tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", repo, *args], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
    except OSError as exc:  # no git on this machine: callers see a failed command, a turn must not die of it
        return 127, f"git unavailable: {exc}"
    return proc.returncode, proc.stdout.strip("\n") if proc.stdout else ""


def _clean_env() -> Dict[str, str]:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        env.pop(key, None)
    return env


def toplevel(path: str) -> str:
    rc, out = git(path, "rev-parse", "--show-toplevel", env=_clean_env())
    if rc != 0 or not out:
        raise GateError(f"repo : {path} is not a git repository")
    return os.path.normpath(out)


def _z(out: str) -> List[str]:
    return [p for p in out.split("\0") if p]


def _git_z(repo: str, *args: str) -> List[str]:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, env=_clean_env())
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)} : {proc.stderr.decode('utf-8', 'replace').strip()}")
    return _z(proc.stdout.decode("utf-8", "replace"))


def tracked_files(repo: str) -> List[str]:
    return _git_z(repo, "ls-files", "-z")


def untracked_files(repo: str) -> List[str]:
    return _git_z(repo, "ls-files", "-z", "--others", "--exclude-standard")


def head_sha(repo: str) -> str:
    rc, out = git(repo, "rev-parse", "--verify", "-q", "HEAD", env=_clean_env())
    return out if rc == 0 else ""


# -------------------------------------------------------------------- ledger

def _canon(entry: Dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(entry: Dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canon(body).encode("ascii")).hexdigest()


def _lock(handle: Any) -> None:
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except ImportError:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _unlock(handle: Any) -> None:
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except ImportError:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def read_ledger() -> List[Dict[str, Any]]:
    path = ledger_path()
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except ValueError as exc:
                raise GateError(f"ledger : line {number} is not JSON ({exc})")
    return entries


def chain_problems(entries: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    try:
        entries = read_ledger() if entries is None else entries
    except GateError as exc:
        return [str(exc)]
    problems = []
    prev = "0" * 64
    for index, entry in enumerate(entries, 1):
        if entry.get("seq") != index:
            problems.append(f"ledger : seq {entry.get('seq')} at position {index}")
        if entry.get("prev") != prev:
            problems.append(f"ledger : line {index} does not chain to line {index - 1}")
        if entry.get("hash") != _hash(entry):
            problems.append(f"ledger : line {index} hash mismatch, content was edited")
        prev = entry.get("hash", "")
        if problems:
            break
    return problems


def _validate(entries: List[Dict[str, Any]], kind: str, fields: Dict[str, Any]) -> None:
    if kind not in KINDS:
        raise GateError(f"ledger : unknown kind {kind}")
    if kind == "fact":
        cls, source = fields.get("class"), str(fields.get("source", ""))
        if cls not in CLASSES:
            raise GateError(f"ledger : fact class must be one of {', '.join(CLASSES)}")
        prefix = source.split(":", 1)[0]
        if prefix not in SOURCE_RANK or ":" not in source or not source.split(":", 1)[1]:
            raise GateError("ledger : fact source must be user:<message id>, probe:<run id> or agent:<name>")
        if cls == "preference" and prefix != "user":
            raise GateError("ledger : a preference must cite Pat as user:<message id>")
        if cls == "state":
            probe = fields.get("probe")
            if not isinstance(probe, dict) or probe.get("name") not in PROBES or not probe.get("repo") or not _expect_ok(probe.get("expect")):
                raise GateError(f"ledger : a state fact needs probe {{name in {', '.join(PROBES)}, repo, expect}}; record it as a note instead")
        elif "probe" in fields:
            raise GateError("ledger : only a state fact carries a probe")
    elif kind == "supersede":
        by_id = {e["id"]: e for e in entries}
        target = by_id.get(fields.get("target"))
        if target is None or target["kind"] != "fact":
            raise GateError("ledger : supersede target must be an existing fact id")
        if fields.get("reason") not in REASONS:
            raise GateError(f"ledger : reason must be one of {', '.join(REASONS)}")
        if any(e["kind"] == "supersede" and e.get("target") == target["id"] for e in entries):
            raise GateError(f"ledger : {target['id']} is already superseded")
    elif kind == "verify":
        if "fact" not in fields or "ok" not in fields:
            raise GateError("ledger : verify needs fact and ok")
    elif kind == "decision" and not isinstance(fields.get("refs", []), list):
        raise GateError("ledger : decision refs must be a list")


def append(kind: str, subject: str, body: str, actor: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
    """Append one line. Validation, hashing and write happen under one file lock."""
    os.makedirs(home(), exist_ok=True)
    path = ledger_path()
    with open(path, "a+b") as handle:
        _lock(handle)
        try:
            handle.seek(0)
            raw = handle.read().decode("utf-8")
            entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
            bad = chain_problems(entries)
            if bad:
                raise GateError(bad[0] + "; refusing to append to a broken ledger")
            _validate(entries, kind, fields)
            seq = len(entries) + 1
            entry = {"seq": seq, "id": f"L{seq}", "ts": _now(), "kind": kind,
                     "actor": actor or os.environ.get("HYDRA_ACTOR", "gate"),
                     "subject": subject, "body": str(body)[:20000], **fields,
                     "prev": entries[-1]["hash"] if entries else "0" * 64}
            entry["hash"] = _hash(entry)
            handle.seek(0, os.SEEK_END)
            handle.write((_canon(entry) + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
            return entry
        finally:
            _unlock(handle)


def superseded_ids(entries: List[Dict[str, Any]]) -> set:
    return {e["target"] for e in entries if e["kind"] == "supersede"}


def live_facts(entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    entries = read_ledger() if entries is None else entries
    gone = superseded_ids(entries)
    facts = [e for e in entries if e["kind"] == "fact" and e["id"] not in gone]
    return sorted(facts, key=lambda f: -SOURCE_RANK[f["source"].split(":", 1)[0]])


def head() -> str:
    entries = read_ledger()
    return f"{entries[-1]['seq']}:{entries[-1]['hash']}" if entries else f"0:{'0' * 64}"


# -------------------------------------------------------------------- probes

def _probe_git_remote(repo: str) -> Tuple[str, str, int]:
    rc, out = git(repo, "remote", "get-url", "origin", env=_clean_env())
    return (out if rc == 0 else ""), f"git -C {repo} remote get-url origin", rc


def _probe_git_branch(repo: str) -> Tuple[str, str, int]:
    rc, out = git(repo, "rev-parse", "--abbrev-ref", "HEAD", env=_clean_env())
    return (out if rc == 0 else ""), f"git -C {repo} rev-parse --abbrev-ref HEAD", rc


PROBES = {"git_remote": _probe_git_remote, "git_branch": _probe_git_branch}


def _expect_ok(expect: Any) -> bool:
    return isinstance(expect, str) and (expect in ("empty", "nonempty") or (expect.startswith("equals:") and len(expect) > 7))


def _expect_met(expect: str, observed: str) -> bool:
    if expect == "empty":
        return observed == ""
    if expect == "nonempty":
        return observed != ""
    return observed == expect[len("equals:"):]


def run_probe(fact: Dict[str, Any]) -> Dict[str, Any]:
    probe = fact["probe"]
    observed, cmd, rc = PROBES[probe["name"]](probe["repo"])
    return {"ok": _expect_met(probe["expect"], observed), "observed": observed, "cmd": cmd, "exit": rc}


def repo_status_line(repo: str) -> str:
    env = _clean_env()
    _, branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    rc, remote = git(repo, "remote", "get-url", "origin", env=env)
    rc2, counts = git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}", env=env)
    tracking = "no upstream"
    if rc2 == 0 and counts.split():
        ahead, behind = counts.split()[:2]
        tracking = f"ahead {ahead} behind {behind}"
    _, dirty = git(repo, "status", "--porcelain", env=env)
    n = len([line for line in dirty.splitlines() if line.strip()])
    return f"repo : {os.path.basename(repo)} branch {branch or 'none'} remote {remote if rc == 0 else 'none'} {tracking} dirty {n}"


def preturn_block(path: str) -> str:
    """Context block for the start of a turn. State facts are re-proved first."""
    repo = toplevel(path)
    name = os.path.basename(repo)
    out = ["MEMORY (verified at turn start; live state wins over anything stored)", repo_status_line(repo)]
    unverified, prefs = [], []
    for fact in live_facts():
        if fact["subject"] not in (name, "global"):
            continue
        if fact["class"] == "state":
            result = run_probe(fact)
            append("verify", fact["subject"], f"probe {fact['probe']['name']}", fact=fact["id"], **{k: result[k] for k in ("ok", "observed", "cmd", "exit")})
            if result["ok"]:
                out.append(f"state : {fact['body']} (verified@{_now()} by {result['cmd']})")
            else:
                append("supersede", fact["subject"], f"live check disagrees: {result['observed']!r}", target=fact["id"], reason="stale_probe_failed")
                out.append(f"memory said {fact['body']}; live check says {result['observed'] or 'nothing'} ({result['cmd']}). Dropped {fact['id']}.")
        elif fact["class"] == "preference":
            prefs.append(f"preference : {fact['body']} (Pat, {fact['source']})")
        else:
            unverified.append(f"note : {fact['body']} ({fact['source']})")
    out.extend(prefs)
    scan_commits(repo)
    if unverified:
        out.append("UNVERIFIED notes, do not act on without checking:")
        out.extend(unverified)
    entries = read_ledger()
    last_pre = max([e["seq"] for e in entries if e["kind"] == "event" and e["subject"] == "preturn" and e.get("repo") == repo], default=0)
    for e in entries:
        if e["kind"] == "event" and e["subject"] == "unverified_commit" and e.get("repo") == repo and e["seq"] > last_pre:
            out.append(f"warning : commit {e['sha'][:10]} has no ledger event ({e['body']}); a hook was bypassed")
    append("event", "preturn", "context built", repo=repo)
    return "\n".join(out)


# ---------------------------------------------------------------- progen lint

def progen_path() -> str:
    explicit = os.environ.get("HYDRA_PROGEN")
    candidates = (explicit,) if explicit else (shutil.which("progen"), os.path.expanduser("~/.local/bin/progen"))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    raise GateError("progen : not found. fix: install progen or set HYDRA_PROGEN to its path")


def progen_lint(text: str) -> List[str]:
    import tempfile
    exe = progen_path()
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        name = handle.name
    try:
        proc = subprocess.run([sys.executable, exe, "lint", name] if exe.endswith(".py") else [exe, "lint", name],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        os.unlink(name)
    if proc.returncode == 0:
        return []
    return [line.replace(name, "text") for line in (proc.stdout + proc.stderr).splitlines() if line.strip()] or [f"progen : exit {proc.returncode}"]


_TOPIC_LINE = re.compile(r"^[^\s:\-*#>`|\d][^:]*? : \S")


def lint_reply(text: str) -> List[str]:
    """One 'topic : comment' per line, then the progen hygiene lint."""
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() and not _TOPIC_LINE.match(line):
            problems.append(f"reply : line {number} is not 'topic : comment': {line[:60]}")
    return problems + progen_lint(text)


# ------------------------------------------------------------- commit checks

def _branch(repo: str) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD", env=os.environ.copy())[1]


def _has_commits(repo: str) -> bool:
    return git(repo, "rev-parse", "--verify", "-q", "HEAD", env=os.environ.copy())[0] == 0


def _staged(repo: str) -> List[Tuple[str, str]]:
    parts = _git_z(repo, "diff", "--cached", "--name-status", "--no-renames", "-z")
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def _is_test(path: str) -> bool:
    low = path.lower().replace("\\", "/")
    base = low.rsplit("/", 1)[-1]
    return bool({"tests", "test", "__tests__"} & set(low.split("/")[:-1])) or base.startswith("test_") or ".test." in base or base.endswith("_test.py")


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def check_commit(path: str) -> List[str]:
    repo = toplevel(path)
    problems: List[str] = []
    problems += chain_problems()
    rc, _ = git(repo, "remote", "get-url", "origin", env=os.environ.copy())
    if rc != 0:
        problems.append("remote : origin is not configured. fix: git remote add origin <url>")
    branch = _branch(repo)
    if _has_commits(repo) and branch in PROTECTED_BRANCHES:
        problems.append(f"branch : commit on {branch} refused, work goes through a PR. fix: git switch -c feat/<topic>")
    staged = _staged(repo)
    for status, rel in staged:
        base = rel.rsplit("/", 1)[-1]
        if status == "A" and _matches(base, RULES["junk"]):
            problems.append(f"5S : {rel} is junk by name. fix: git restore --staged {rel} and remove it")
        if status == "A" and "/" not in rel and not _matches(rel, RULES["root_allow"]):
            problems.append(f"5S : {rel} is a new root file outside the allow-list. fix: move it under docs/, scripts/ or src/")
    deleted = [rel for status, rel in staged if status == "D" and _is_test(rel)]
    if len(deleted) > RULES["test_delete_max"]:
        key = f"test_delete:{repo}"
        recent = [e for e in read_ledger() if e["kind"] == "decision" and e["subject"] == key
                  and time.time() - datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() < RULES["test_delete_window_s"]]
        if not recent:
            problems.append(f"tests : {len(deleted)} test files deleted, limit {RULES['test_delete_max']}. fix: python scripts/gate.py decide test_delete \"<why>\" then commit again")
    from hydra_cli import invariants  # lazy: invariants imports this module
    problems += invariants.check_gate(repo)
    docs = [rel for status, rel in staged if status != "D" and rel.endswith(".md") and rel.split("/")[0] in RULES["doc_lint_dirs"] and "/" in rel]
    for rel in docs:
        with open(os.path.join(repo, rel), "r", encoding="utf-8", errors="replace") as handle:
            problems += [f"progen : {rel} {line}" for line in progen_lint(handle.read())]
    return problems


def check_push(path: str, stdin_lines: List[str]) -> List[str]:
    repo = toplevel(path)
    problems = chain_problems()
    zero = "0" * 40
    for line in stdin_lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts[:4]
        branch = remote_ref.replace("refs/heads/", "", 1)
        if remote_ref.startswith("refs/heads/") and branch in PROTECTED_BRANCHES:
            problems.append(f"push : direct push to {branch} refused, it needs a PR. fix: git push origin HEAD:feat/<topic> then open a PR")
            continue
        if local_sha != zero and remote_sha != zero:
            rc, _ = git(repo, "merge-base", "--is-ancestor", remote_sha, local_sha, env=_clean_env())
            if rc != 0:
                problems.append(f"push : {branch} is not a fast-forward (force or missing history). fix: git fetch, then rebase or merge, no --force")
        if local_sha != zero and remote_ref.startswith("refs/heads/"):
            append("event", "branch_pushed", branch, repo=repo, branch=branch, pr=None)
    return problems


# ------------------------------------------------------- hooks, trailer, bypass

def _hooks_dir(repo: str) -> str:
    rc, out = git(repo, "rev-parse", "--git-path", "hooks", env=_clean_env())
    if rc != 0:
        raise GateError("hooks : cannot resolve the hooks directory")
    return out if os.path.isabs(out) else os.path.join(repo, out)


def _hook_text(name: str, gate: str) -> str:
    py = sys.executable.replace("\\", "/")
    gate = gate.replace("\\", "/")
    chain = f'if [ -x "$0.pre-gate" ]; then exec "$0.pre-gate" "$@"; fi\nexit 0\n'
    if name == "pre-push":
        body = ('in=$(cat)\n'
                f'printf \'%s\\n\' "$in" | "{py}" "{gate}" push "$@" || exit 1\n'
                'if [ -x "$0.pre-gate" ]; then printf \'%s\\n\' "$in" | "$0.pre-gate" "$@" || exit 1; fi\nexit 0\n')
        return f"#!/bin/sh\n{HOOK_MARK}\n{body}"
    command = {"pre-commit": "commit", "commit-msg": 'msg "$1"'}[name]
    return f'#!/bin/sh\n{HOOK_MARK}\n"{py}" "{gate}" {command} || exit 1\n{chain}'


def install(path: str, gate: str) -> List[str]:
    repo = toplevel(path)
    hooks = _hooks_dir(repo)
    os.makedirs(hooks, exist_ok=True)
    done = []
    for name in HOOKS:
        target = os.path.join(hooks, name)
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                existing = handle.read()
            if HOOK_MARK not in existing:
                os.replace(target, target + ".pre-gate")
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_hook_text(name, gate))
        os.chmod(target, 0o755)
        done.append(name)
    sha = head_sha(repo)
    append("event", "install", ",".join(done), repo=repo, head=sha)
    return done


def uninstall(path: str) -> List[str]:
    repo = toplevel(path)
    hooks = _hooks_dir(repo)
    removed = []
    for name in HOOKS:
        target = os.path.join(hooks, name)
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                if HOOK_MARK not in handle.read():
                    continue
            os.unlink(target)
            if os.path.exists(target + ".pre-gate"):
                os.replace(target + ".pre-gate", target)
            removed.append(name)
    append("event", "uninstall", ",".join(removed), repo=repo)
    return removed


def stamp_message(path: str, msg_file: str) -> str:
    """commit-msg hook body: log the commit, then write the ledger head into the message trailer."""
    repo = toplevel(path)
    with open(msg_file, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    lines = [ln for ln in lines if not ln.startswith(TRAILER + ":")]
    real = [i for i, ln in enumerate(lines) if ln.strip() and not ln.startswith("#")]
    subject = lines[real[0]] if real else "(empty)"
    event = append("event", "commit", subject, repo=repo, branch=_branch(repo))
    value = f"{event['seq']}:{event['hash']}"
    at = (real[-1] + 1) if real else 0
    block = []
    for i in range(len(lines[:at]) - 1, -1, -1):
        if not lines[i].strip():
            break
        if not lines[i].startswith("#"):
            block.append(lines[i])
    in_trailers = len(block) < len(real) and all(re.match(r"^[A-Za-z-]+: ", ln) for ln in block)
    insert = [f"{TRAILER}: {value}"] if in_trailers else ["", f"{TRAILER}: {value}"]
    lines[at:at] = insert
    with open(msg_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return value


def _trailer_value(message: str) -> Optional[str]:
    found = re.findall(rf"^{TRAILER}: (\d+):([0-9a-f]{{64}})$", message, re.M)
    return f"{found[-1][0]}:{found[-1][1]}" if found else None


def _commits(repo: str, rng: List[str]) -> List[Tuple[str, str]]:
    rc, out = git(repo, "log", "--no-merges", "--format=%H%x00%B%x01", *rng, env=_clean_env())
    if rc != 0:
        return []
    items = []
    for chunk in out.split("\x01"):
        chunk = chunk.strip("\n")
        if "\x00" in chunk:
            sha, message = chunk.split("\x00", 1)
            items.append((sha.strip(), message))
    return items


def scan_commits(path: str, record: bool = True) -> Dict[str, Any]:
    """Find commits whose trailer does not point at a real ledger line (hook bypassed or ledger rolled back)."""
    repo = toplevel(path)
    if not _has_commits(repo):
        return {"unverified": [], "rollback": []}
    entries = read_ledger()
    by_seq = {e["seq"]: e["hash"] for e in entries}
    base = None
    for e in entries:
        if e["kind"] == "event" and e["subject"] in ("install", "bypass_scan") and e.get("repo") == repo and e.get("head"):
            base = e["head"]
    if not base:
        return {"unverified": [], "rollback": []}
    if git(repo, "merge-base", "--is-ancestor", base, "HEAD", env=_clean_env())[0] == 0:
        rng = [f"{base}..HEAD"]
    else:
        rng = ["-n", "20", "--since=" + [e["ts"] for e in entries if e.get("head") == base][-1]]
    seen = {e.get("sha") for e in entries if e["kind"] == "event" and e["subject"] == "unverified_commit"}
    unverified, rollback = [], []
    for sha, message in _commits(repo, rng):
        value = _trailer_value(message)
        if value is None:
            unverified.append((sha, "no Ledger-Head trailer"))
            continue
        seq, digest = value.split(":")
        if by_seq.get(int(seq)) != digest:
            rollback.append((sha, f"trailer {seq} is not in the ledger: ledger was rolled back or replaced"))
    if record:
        for sha, why in unverified + rollback:
            if sha not in seen:
                subj = git(repo, "log", "-1", "--format=%s", sha, env=_clean_env())[1]
                append("event", "unverified_commit", f"{why}: {subj}", repo=repo, sha=sha)
        append("event", "bypass_scan", f"{len(unverified)} unverified, {len(rollback)} rollback", repo=repo,
               head=head_sha(repo))
    return {"unverified": unverified, "rollback": rollback}


# --------------------------------------------------------------- 5S sweep

def _finding(rule: str, path: str, reason: str, action: str) -> Dict[str, str]:
    return {"rule": rule, "path": path, "reason": reason, "action": action}


def _ahead_behind(repo: str, ref: str, base: str) -> Optional[Tuple[int, int]]:
    rc, out = git(repo, "rev-list", "--left-right", "--count", f"{ref}...{base}", env=_clean_env())
    parts = out.split()
    return (int(parts[0]), int(parts[1])) if rc == 0 and len(parts) == 2 else None


def sweep_scan(path: str, ignore_deleted_by: Optional[str] = None) -> List[Dict[str, str]]:
    repo = toplevel(path)
    tracked = tracked_files(repo)
    untracked = untracked_files(repo)
    skip: set = set()
    if ignore_deleted_by:
        rc, out = git(repo, "diff", "--name-only", "--diff-filter=D", f"HEAD...{ignore_deleted_by}", env=_clean_env())
        if rc != 0:
            raise GateError(f"sweep : cannot diff against {ignore_deleted_by}")
        skip = set(out.splitlines())
    found: List[Dict[str, str]] = []
    root_allow = RULES["root_allow"]
    for rel in tracked + untracked:
        if rel in skip:
            continue
        base = rel.rsplit("/", 1)[-1]
        if _matches(base, RULES["junk"]):
            found.append(_finding("seiri", rel, "junk by name", "move"))
        elif "/" not in rel and not _matches(rel, root_allow):
            found.append(_finding("seiton", rel, "root file outside the allow-list", "move" if rel in untracked else "flag"))
    tops = sorted({p.split("/")[0] for p in tracked if "/" in p} - set(RULES["dir_allow"]))
    for top in tops:
        n = sum(1 for p in tracked if p.startswith(top + "/"))
        found.append(_finding("seiton", top + "/", f"top-level dir outside the allow-list ({n} files)", "flag"))
    names: Dict[str, List[str]] = {}
    for rel in tracked + untracked:
        if rel in skip:
            continue
        base = rel.rsplit("/", 1)[-1]
        if not _matches(base, RULES["dup_ignore"]):
            names.setdefault(base.lower(), []).append(rel)
    for base, rels in sorted(names.items()):
        if len(rels) > 1:
            for rel in rels:
                if rel in untracked:
                    found.append(_finding("seiri", rel, f"same basename as tracked {[r for r in rels if r in tracked][:1] or rels[:1]}", "move" if any(r in tracked for r in rels) else "flag"))
            if all(r in tracked for r in rels):
                found.append(_finding("seiri", " + ".join(rels[:4]), "same basename in several places", "flag"))
    launchers = [p for p in tracked if p.lower().endswith(RULES["launcher_ext"]) and p not in skip]
    if len(launchers) > RULES["launcher_max"]:
        found.append(_finding("seiri", " + ".join(launchers), f"{len(launchers)} launchers, limit {RULES['launcher_max']}", "flag"))
    if "setup.py" in tracked and "pyproject.toml" in tracked and "setup.py" not in skip:
        found.append(_finding("seiketsu", "setup.py + pyproject.toml", "two package configs can drift", "flag"))
    rc, tree = git(repo, "ls-tree", "-r", "-l", "HEAD", env=_clean_env())
    if rc == 0:
        for line in tree.splitlines():
            meta, rel = line.split("\t", 1)
            size = meta.split()[-1]
            if size.isdigit() and int(size) > RULES["blob_max_bytes"] and rel not in skip:
                found.append(_finding("seiso", rel, f"blob {int(size)} bytes over {RULES['blob_max_bytes']}", "flag"))
    now = time.time()
    for rel in untracked:
        if rel in skip:
            continue
        full = os.path.join(repo, rel)
        if not os.path.isfile(full):
            continue
        days = (now - os.path.getmtime(full)) / 86400
        size = os.path.getsize(full)
        note = "stale untracked" if days > RULES["stale_untracked_days"] else "untracked"
        found.append(_finding("seiso", rel, f"{note}, {days:.0f} days old" + (f", {size} bytes over limit" if size > RULES["blob_max_bytes"] else ""), "flag"))
    ignore_file = os.path.join(repo, ".gitignore")
    lines = []
    if os.path.exists(ignore_file):
        with open(ignore_file, "r", encoding="utf-8", errors="replace") as handle:
            lines = [ln.strip() for ln in handle]
    missing = [e for e in RULES["gitignore_required"] if e not in lines]
    if missing:
        found.append(_finding("seiketsu", ".gitignore", "missing " + " ".join(missing), "flag"))
    pattern = re.compile(RULES["win_path"])
    for rel in tracked:
        full = os.path.join(repo, rel)
        if rel in skip or not os.path.isfile(full) or os.path.getsize(full) > RULES["blob_max_bytes"]:
            continue
        with open(full, "rb") as handle:
            data = handle.read()
        if b"\0" in data:
            continue
        hits = len(pattern.findall(data.decode("utf-8", "replace")))
        if hits:
            found.append(_finding("seiketsu", rel, f"{hits} hard-coded Windows user paths", "flag"))
    rc, refs = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin", env=_clean_env())
    if rc == 0:
        for ref in refs.splitlines():
            if ref in ("origin", "origin/HEAD", "origin/main", "origin/master"):
                continue
            base = "origin/main" if git(repo, "rev-parse", "--verify", "-q", "origin/main", env=_clean_env())[0] == 0 else "origin/master"
            counts = _ahead_behind(repo, ref, base)
            if counts and counts[0] == 0:
                found.append(_finding("seiri", ref, "empty branch, nothing ahead of main", "flag"))
            elif counts and counts[1] >= RULES["stale_branch_behind"] and counts[0] <= 1:
                found.append(_finding("seiri", ref, f"stale branch, {counts[1]} behind main, {counts[0]} ahead", "flag"))
    return found


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sweep(path: str, apply: bool = False, ignore_deleted_by: Optional[str] = None) -> Dict[str, Any]:
    repo = toplevel(path)
    findings = sweep_scan(repo, ignore_deleted_by)
    moved: List[str] = []
    if apply:
        tracked = set(tracked_files(repo))
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for item in [f for f in findings if f["action"] == "move"]:
            rel = item["path"]
            src = os.path.join(repo, rel)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(home(), "purgatory", day, os.path.basename(repo), rel)
            n = 1
            while os.path.exists(dest):
                dest = os.path.join(home(), "purgatory", day, os.path.basename(repo), f"{rel}.{n}")
                n += 1
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            digest = _sha256(src)
            shutil.copy2(src, dest)
            if _sha256(dest) != digest:
                raise GateError(f"sweep : copy of {rel} to purgatory does not match, nothing removed")
            is_tracked = rel in tracked
            if is_tracked:
                rc, out = git(repo, "rm", "-q", "--", rel, env=_clean_env())
                if rc != 0:
                    os.unlink(dest)
                    raise GateError(f"sweep : git rm {rel} failed: {out}")
            else:
                os.unlink(src)
            event = append("event", "sweep_move", item["reason"], repo=repo, old=rel, new=dest, sha256=digest, tracked=is_tracked)
            moved.append(event["id"])
    append("event", "sweep", f"{len(findings)} findings, {len(moved)} moved", repo=repo, dry=not apply,
           moved=moved, flagged=len([f for f in findings if f["action"] == "flag"]))
    return {"findings": findings, "moved": moved}


def restore(move_id: str) -> str:
    entries = read_ledger()
    event = next((e for e in entries if e["id"] == move_id and e["kind"] == "event" and e["subject"] == "sweep_move"), None)
    if event is None:
        raise GateError(f"restore : {move_id} is not a sweep_move line")
    if any(e["kind"] == "event" and e["subject"] == "sweep_restore" and e.get("move") == move_id for e in entries):
        raise GateError(f"restore : {move_id} was already restored")
    target = os.path.join(event["repo"], event["old"])
    if os.path.exists(target):
        raise GateError(f"restore : {event['old']} exists again, refusing to overwrite")
    if not os.path.isfile(event["new"]) or _sha256(event["new"]) != event["sha256"]:
        raise GateError("restore : purgatory copy is missing or changed")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(event["new"], target)
    if event["tracked"]:
        git(event["repo"], "add", "--", event["old"], env=_clean_env())
    append("event", "sweep_restore", event["old"], repo=event["repo"], move=move_id)
    return target


def launch_sweep(cwd: str) -> Optional[Dict[str, Any]]:
    """Scheduled sweep on Hydra launch: at most once per 20 hours per repo, apply mode."""
    try:
        repo = toplevel(cwd)
    except GateError:
        return None
    for e in reversed(read_ledger()):
        if e["kind"] == "event" and e["subject"] == "sweep" and e.get("repo") == repo and not e.get("dry"):
            when = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            if time.time() - when < 20 * 3600:
                return None
            break
    return sweep(repo, apply=True)


# ------------------------------------------------------- stray branches / PRs

def _github_slug(repo: str) -> str:
    rc, url = git(repo, "remote", "get-url", "origin", env=_clean_env())
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?$", url) if rc == 0 else None
    if not match:
        raise GateError("strays : origin is not a GitHub remote")
    return f"{match.group(1)}/{match.group(2)}"


def stray_branches(path: str) -> List[str]:
    """Remote branches other than main with no pull request, asked of the GitHub API."""
    repo = toplevel(path)
    slug = _github_slug(repo)
    api = os.environ.get("HYDRA_GH_API", "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "hydra-gate"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def fetch(url: str) -> Any:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    branches = [b["name"] for b in fetch(f"{api}/repos/{slug}/branches?per_page=100")]
    strays = []
    for name in branches:
        if name in PROTECTED_BRANCHES:
            continue
        owner = slug.split("/")[0]
        if not fetch(f"{api}/repos/{slug}/pulls?state=all&head={owner}:{name}"):
            strays.append(name)
    return strays


# ----------------------------------------------------------------------- CLI

def _flag(args: List[str], name: str, default: Optional[str] = None) -> Optional[str]:
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def cli(argv: List[str], gate_script: str) -> int:
    cmd, args = (argv[0] if argv else "verify"), argv[1:]
    here = os.getcwd()

    def report(problems: List[str], ok_line: str) -> int:
        if problems:
            for line in problems:
                print(line)
            print(f"gate : {cmd} refused, {len(problems)} problem(s)")
            return 1
        print(ok_line)
        return 0

    try:
        if cmd == "preturn":
            print(preturn_block(_flag(args, "--repo", here)))
            return 0
        if cmd == "commit":
            repo = toplevel(here)
            problems = check_commit(repo)
            scan = scan_commits(repo)
            for sha, why in scan["unverified"] + scan["rollback"]:
                print(f"warning : {sha[:10]} {why}")
            return report(problems, "gate : commit ok")
        if cmd == "msg":
            print(f"ledger : {stamp_message(here, args[0])}")
            return 0
        if cmd == "push":
            return report(check_push(here, sys.stdin.read().splitlines()), "gate : push ok")
        if cmd == "install":
            print("gate : installed " + ", ".join(install(_flag(args, "--repo", here), gate_script)))
            return 0
        if cmd == "uninstall":
            print("gate : removed " + (", ".join(uninstall(_flag(args, "--repo", here))) or "nothing"))
            return 0
        if cmd == "sweep":
            result = sweep(_flag(args, "--repo", here), apply="--apply" in args, ignore_deleted_by=_flag(args, "--ignore-deleted-by"))
            for f in result["findings"]:
                print(f"{f['rule']} : {f['action']} {f['path']} ({f['reason']})")
            print(f"gate : sweep {len(result['findings'])} findings, {len(result['moved'])} moved" + ("" if "--apply" in args else " (dry run)"))
            return 0
        if cmd == "restore":
            print(f"gate : restored {restore(args[0])}")
            return 0
        if cmd == "strays":
            return report([f"branch : {b} has no pull request" for b in stray_branches(_flag(args, "--repo", here))], "gate : no stray branches")
        if cmd == "reply":
            text = sys.stdin.read() if not args or args[0] == "-" else open(args[0], "r", encoding="utf-8").read()
            return report(lint_reply(text), "gate : reply ok")
        if cmd == "decide":
            repo = toplevel(here)
            entry = append("decision", f"{args[0]}:{repo}", " ".join(args[1:]) or args[0], refs=[])
            print(f"gate : decision {entry['id']}")
            return 0
        if cmd == "fact":
            fields: Dict[str, Any] = {"class": args[0], "source": _flag(args, "--source", "agent:cli")}
            if args[0] == "state":
                fields["probe"] = {"name": _flag(args, "--probe"), "repo": toplevel(_flag(args, "--repo", here)), "expect": _flag(args, "--expect")}
            entry = append("fact", _flag(args, "--subject", "global"), args[1], **fields)
            print(f"gate : fact {entry['id']}")
            return 0
        if cmd == "supersede":
            entry = append("supersede", "global", _flag(args, "--why", "") or args[1], target=args[0], reason=args[1])
            print(f"gate : supersede {entry['id']}")
            return 0
        if cmd == "log":
            since = _flag(args, "--since", "")
            for e in read_ledger():
                if e["ts"] >= since:
                    print(f"{e['ts']} {e['id']} {e['kind']} {e['subject']} : {e['body'][:100]}")
            return 0
        if cmd == "verify":
            from hydra_cli import invariants
            problems = chain_problems()
            if _is_repo(here):
                problems += invariants.check_gate(toplevel(here))
            scan = scan_commits(here, record=False) if _is_repo(here) else {"unverified": [], "rollback": []}
            problems += [f"commit : {sha[:10]} {why}" for sha, why in scan["unverified"] + scan["rollback"]]
            return report(problems, "gate : verify ok")
    except GateError as exc:
        print(str(exc))
        print(f"gate : {cmd} refused")
        return 1
    except (IndexError, OSError) as exc:
        print(f"gate : {cmd} failed ({type(exc).__name__}: {exc})")
        return 2
    print("gate : commands preturn commit msg push install uninstall sweep restore strays reply decide fact supersede log verify")
    return 2


def _is_repo(path: str) -> bool:
    return git(path, "rev-parse", "--show-toplevel", env=_clean_env())[0] == 0
