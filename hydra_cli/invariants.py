"""Hydra invariants: a small typed file, loaded every turn, enforced by probes.

The file is hydra_cli/invariants.json. Invariants are never retrieved by
similarity and never decay. Episodes (the ledger in hydra_cli/memory.py) are
history: when an episode contradicts an invariant the invariant wins and the
episode is superseded with a reason code from the closed memory.REASONS set.
Stdlib only. The command line is: python -m hydra_cli.invariants check|context
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from hydra_cli import memory
from hydra_cli.memory import GateError

INVARIANT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "invariants.json")
ID_RE = re.compile(r"^INV-(GIT|DOC|CODE|FS)-\d{3}$")
SOURCE_RE = re.compile(r"^(user|doc):\S+$")
SCOPES = ("global", "repo")
FIELDS = ("id", "scope", "rule", "source", "supersedes", "probe", "contradiction_reason", "forbids")
TIERS = ("invariants", "episodes")
DETAIL_MAX = 80
RULE_MAX = 160
# Pinned set: deleting one from the file fails the gate. Changing this tuple needs Pat.
REQUIRED_IDS = ("INV-GIT-001", "INV-GIT-002", "INV-GIT-003", "INV-DOC-001", "INV-CODE-001", "INV-FS-001")
MARKERS = re.compile(r"(#|//|/\*)\s*(" + "TO" + "DO|FIX" + "ME|XX" + "X)\\b|raise NotImplemented" + "Error")
STUB_TEXT = re.compile(r"(?i)(moved to|has moved|now lives (in|at)|renamed to|see \S.{0,80} instead|redirect(s|ed)? to)")
NO_REMOTE_CLAIM = re.compile(r"(?i)\bno (git(hub)? )?remote\b")
SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".ts", ".tsx", ".rs", ".go", ".sh", ".ps1", ".bat", ".java", ".c", ".cpp", ".h")
# The gate script holds detector fixtures with marker text inside string literals.
MARKER_SKIP = ("scripts/verify.py",)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def estimate_tokens(text: str) -> int:
    """Upper-bound estimate, four bytes per token, rounded up. No tokenizer dependency."""
    return (len(text.encode("utf-8")) + 3) // 4


# ------------------------------------------------------------------- loading

def _read(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise GateError(f"invariants : cannot read {path} ({exc.strerror})")
    except ValueError as exc:
        raise GateError(f"invariants : {path} is not JSON ({exc})")
    if not isinstance(data, dict):
        raise GateError("invariants : top level must be an object")
    return data


def validate(data: Dict[str, Any]) -> List[str]:
    """Return every schema problem, empty when the file is valid."""
    problems: List[str] = []
    if data.get("schema") != 1:
        problems.append("invariants : schema must be 1")
    caps = data.get("token_cap")
    if not isinstance(caps, dict) or set(caps) != set(TIERS) or not all(isinstance(v, int) and v > 0 for v in caps.values()):
        problems.append("invariants : token_cap needs a positive integer for invariants and for episodes")
    items, retired = data.get("invariants"), data.get("retired")
    if not isinstance(items, list) or not items:
        return problems + ["invariants : invariants must be a non-empty list"]
    if not isinstance(retired, list):
        return problems + ["invariants : retired must be a list"]
    retired_ids = set()
    for old in retired:
        if not isinstance(old, dict) or not ID_RE.match(str(old.get("id", ""))) or not old.get("rule") or not old.get("retired_by"):
            problems.append("invariants : each retired entry needs id, rule and retired_by")
        else:
            retired_ids.add(old["id"])
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            problems.append("invariants : each invariant must be an object")
            continue
        iid = str(item.get("id", "?"))
        extra = set(item) - set(FIELDS)
        missing = [f for f in FIELDS if f not in item]
        if missing or extra:
            problems.append(f"{iid} : fields missing {missing or 'none'}, unknown {sorted(extra) or 'none'}")
            continue
        if not ID_RE.match(iid):
            problems.append(f"{iid} : id must match INV-<GIT|DOC|CODE|FS>-<three digits>")
        if iid in seen or iid in retired_ids:
            problems.append(f"{iid} : id is reused, ids are never recycled")
        seen.add(iid)
        if item["scope"] not in SCOPES:
            problems.append(f"{iid} : scope must be one of {', '.join(SCOPES)}")
        rule = item["rule"]
        if not isinstance(rule, str) or not rule.strip() or len(rule) > RULE_MAX:
            problems.append(f"{iid} : rule must be text of at most {RULE_MAX} characters")
        if not isinstance(item["source"], str) or not SOURCE_RE.match(item["source"]):
            problems.append(f"{iid} : source must be user:<ref> or doc:<path>")
        if item["supersedes"] is not None and item["supersedes"] not in retired_ids:
            problems.append(f"{iid} : supersedes must be null or an id listed under retired")
        if item["probe"] not in PROBES:
            problems.append(f"{iid} : probe {item['probe']!r} is not implemented, allowed {', '.join(PROBES)}")
        if item["contradiction_reason"] not in memory.REASONS:
            problems.append(f"{iid} : contradiction_reason must be one of {', '.join(memory.REASONS)}")
        forbids = item["forbids"]
        if not isinstance(forbids, list) or not all(isinstance(p, str) for p in forbids):
            problems.append(f"{iid} : forbids must be a list of regex strings")
        else:
            for pattern in forbids:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append(f"{iid} : forbids pattern does not compile ({exc})")
    if not problems:
        worst = estimate_tokens(render([(i, True, "x" * DETAIL_MAX) for i in items], violated_worst=True))
        if worst > caps["invariants"]:
            problems.append(f"invariants : worst case block is {worst} tokens, cap is {caps['invariants']}. fix: shorten rules or Pat raises the cap")
    return problems


def load(path: Optional[str] = None) -> Dict[str, Any]:
    data = _read(path or INVARIANT_FILE)
    problems = validate(data)
    if problems:
        raise GateError("; ".join(problems[:3]) + (f"; and {len(problems) - 3} more" if len(problems) > 3 else ""))
    return data


# -------------------------------------------------------------------- probes

@contextmanager
def _scratch_ledger() -> Iterator[None]:
    """Run a probe against an empty throwaway ledger so a probe never writes to the real one."""
    saved = os.environ.get("HYDRA_HOME")
    with tempfile.TemporaryDirectory(prefix="hydra-probe-") as tmp:
        os.environ["HYDRA_HOME"] = tmp
        try:
            yield
        finally:
            if saved is None:
                os.environ.pop("HYDRA_HOME", None)
            else:
                os.environ["HYDRA_HOME"] = saved


def _hooks_installed(repo: str, names: Tuple[str, ...]) -> List[str]:
    hooks = memory._hooks_dir(repo)
    missing = []
    for name in names:
        target = os.path.join(hooks, name)
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                if memory.HOOK_MARK not in handle.read():
                    missing.append(name)
        except OSError:
            missing.append(name)
    return missing


def _head(repo: str) -> str:
    rc, out = memory.git(repo, "rev-parse", "--verify", "-q", "HEAD", env=memory._clean_env())
    return out if rc == 0 else ""


def probe_push_main_refused(repo: str) -> Tuple[bool, str]:
    missing = _hooks_installed(repo, ("pre-push",))
    if missing:
        return False, "pre-push hook not installed"
    sha = _head(repo)
    if not sha:
        return False, "repo has no commits to push"
    with _scratch_ledger():
        problems = memory.check_push(repo, [f"refs/heads/x {sha} refs/heads/main {sha}", f"refs/heads/x {sha} refs/heads/master {sha}"])
    refused = [p for p in problems if "direct push to" in p]
    return len(refused) == 2, "main and master refused" if len(refused) == 2 else "gate let a push to main through"


def probe_push_force_refused(repo: str) -> Tuple[bool, str]:
    if _hooks_installed(repo, ("pre-push",)):
        return False, "pre-push hook not installed"
    sha = _head(repo)
    if not sha:
        return False, "repo has no commits to push"
    env = {**memory._clean_env(), "GIT_AUTHOR_NAME": "gate", "GIT_AUTHOR_EMAIL": "gate@local",
           "GIT_COMMITTER_NAME": "gate", "GIT_COMMITTER_EMAIL": "gate@local"}
    rc, orphan = memory.git(repo, "commit-tree", EMPTY_TREE, "-m", "unrelated history", env=env)
    if rc != 0 or not orphan:
        return False, "cannot build the unrelated commit for the probe"
    with _scratch_ledger():
        problems = memory.check_push(repo, [f"refs/heads/x {sha} refs/heads/x {orphan}"])
    refused = any("not a fast-forward" in p for p in problems)
    return refused, "non fast-forward refused" if refused else "gate let a force push through"


def probe_remote_claims_probed(repo: str) -> Tuple[bool, str]:
    name = os.path.basename(repo)
    bad = [f["id"] for f in memory.live_facts()
           if f["subject"] in (name, "global") and f["class"] != "state" and NO_REMOTE_CLAIM.search(f["body"])]
    return not bad, f"unprobed no-remote claim {bad[0]}" if bad else "no unprobed remote claim is live"


def probe_progen_lint_enforced(repo: str) -> Tuple[bool, str]:
    if _hooks_installed(repo, ("pre-commit",)):
        return False, "pre-commit hook not installed"
    try:
        flagged = memory.progen_lint("note : this line has (a parenthetical) in prose\n")
        clean = memory.progen_lint("note : this line is clean\n")
    except GateError as exc:
        return False, str(exc)[:DETAIL_MAX]
    ok = bool(flagged) and not clean
    return ok, "progen flags a bad doc and passes a clean one" if ok else "progen lint did not behave"


def _tracked(repo: str) -> List[str]:
    return memory._git_z(repo, "ls-files", "-z")


def _read_text(repo: str, rel: str, limit: int = 400000) -> Optional[str]:
    try:
        with open(os.path.join(repo, rel), "rb") as handle:
            raw = handle.read(limit)
    except OSError:
        return None
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", "replace")


def probe_no_todo_markers(repo: str) -> Tuple[bool, str]:
    for rel in _tracked(repo):
        if rel.lower().endswith(SOURCE_SUFFIXES) and not rel.startswith(("docs/", "tests/")) and rel not in MARKER_SKIP:
            text = _read_text(repo, rel)
            for number, line in enumerate((text or "").splitlines(), 1):
                if MARKERS.search(line):
                    return False, f"{rel}:{number} carries a work marker or stub"
    return True, "no markers in tracked source"


def probe_no_move_stubs(repo: str) -> Tuple[bool, str]:
    for rel in _tracked(repo):
        text = _read_text(repo, rel, limit=400)
        if text is not None and len(text.strip()) <= 300 and STUB_TEXT.search(text):
            return False, f"{rel} is a stub that points elsewhere"
    return True, "no redirect stubs tracked"


PROBES: Dict[str, Callable[[str], Tuple[bool, str]]] = {
    "push_main_refused": probe_push_main_refused,
    "push_force_refused": probe_push_force_refused,
    "remote_claims_probed": probe_remote_claims_probed,
    "progen_lint_enforced": probe_progen_lint_enforced,
    "no_todo_markers": probe_no_todo_markers,
    "no_move_stubs": probe_no_move_stubs,
}


def run_probe(name: str, repo: str) -> Tuple[bool, str]:
    try:
        ok, detail = PROBES[name](repo)
    except GateError as exc:
        return False, str(exc)
    return bool(ok), detail[:DETAIL_MAX]


# ---------------------------------------------------------------------- gate

def check_gate(repo: str, path: Optional[str] = None, required: Tuple[str, ...] = REQUIRED_IDS) -> List[str]:
    """Every problem that should fail a gate run: bad file, missing invariant, violated probe."""
    try:
        data = load(path)
    except GateError as exc:
        return [str(exc)]
    by_id = {i["id"]: i for i in data["invariants"]}
    problems = [f"{rid} : required invariant is missing from the file" for rid in required if rid not in by_id]
    for iid, item in by_id.items():
        ok, detail = run_probe(item["probe"], repo)
        if not ok:
            problems.append(f"{iid} : violated, {detail}")
    return problems


# ------------------------------------------------------------------- context

def render(results: List[Tuple[Dict[str, Any], bool, str]], violated_worst: bool = False) -> str:
    lines = ["INVARIANTS (always loaded, they beat any remembered fact, a violated one must be fixed first)"]
    for item, ok, detail in results:
        status = "VIOLATED " + detail[:DETAIL_MAX] if (not ok or violated_worst) else "ok"
        lines.append(f"{item['id']} : {item['rule']} [{item['probe']} {status}]")
    return "\n".join(lines)


def quarantine(repo: str, data: Dict[str, Any]) -> List[str]:
    """Supersede live episodes that contradict an invariant. The invariant always wins."""
    name = os.path.basename(repo)
    done: List[str] = []
    for fact in memory.live_facts():
        if fact["subject"] not in (name, "global") or fact["class"] == "state":
            continue
        for item in data["invariants"]:
            if any(re.search(p, fact["body"]) for p in item["forbids"]):
                memory.append("supersede", fact["subject"], f"contradicts {item['id']}", target=fact["id"], reason=item["contradiction_reason"])
                done.append(fact["id"])
                break
    return done


def _fit(block: str, cap: int, label: str) -> str:
    """Trim a block to the token cap, newest-last lines go first. The first line always stays."""
    if estimate_tokens(block) <= cap:
        return block
    lines = block.splitlines()
    note = f"{label} : older lines archived to fit {cap} tokens, fetch with python scripts/gate.py log"
    while len(lines) > 1 and estimate_tokens("\n".join(lines + [note])) > cap:
        lines.pop()
    return "\n".join(lines + [note])


def build_context(cwd: str, path: Optional[str] = None) -> str:
    """The full turn-start block: invariants first, then episodes, each under its own cap."""
    repo = memory.toplevel(cwd)
    try:
        data = load(path)
    except GateError as exc:
        data = None
        head = f"INVARIANTS : BROKEN, {exc}. Stop and report this to Pat before acting."
        caps = {"invariants": 900, "episodes": 1500}
    else:
        caps = data["token_cap"]
        quarantine(repo, data)
        results = [(i, *run_probe(i["probe"], repo)) for i in data["invariants"]]
        head = render(results)
    try:
        episodes = memory.preturn_block(repo)
    except GateError as exc:
        episodes = f"MEMORY : gate could not run here ({exc}); treat every remembered claim as unverified"
    return head + "\n\n" + _fit(episodes, caps["episodes"], "episodes")


def main(argv: List[str]) -> int:
    cmd = argv[0] if argv else "check"
    repo = argv[1] if len(argv) > 1 else os.getcwd()
    try:
        if cmd == "check":
            problems = check_gate(memory.toplevel(repo))
            for line in problems:
                print(line)
            print("invariants : ok" if not problems else f"invariants : {len(problems)} problem(s)")
            return 1 if problems else 0
        if cmd == "context":
            print(build_context(repo))
            return 0
    except GateError as exc:
        print(str(exc))
        return 1
    print("invariants : commands check context")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
