"""Local search before a Hydra summon.

Order: typo table, pending clarification, vagueness rule, skill index, alice_core.js strict routes,
sentence retrieval over the stacks and the web whitelist, remaining alice_core.js routes,
then the selected inference alias.
"""

from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


ANSWER_ROUTES = {
    "DETERMINISTIC_EVAL",
    "DETERMINISTIC_LOGIC",
    "ASSOCIATIVE_LOOKUP",
    "VERBATIM_RECALL",
    "PARADOX_NAVIGATED",
    "PHILOSOPHICAL_CANON",
    "INVENTION_LOOP",
    "SILENCE_BOUNDARY",
    "SYSTEM_COMMAND",
    "SYLLOGISTIC_DEDUCTION",
    "TRACTATUS_SILENCE",
}

ASK_ROUTES = {
    "EPISTEMIC_GAP",
    "VAGUENESS_DETECTED",
}

STRICT_ROUTES = {
    "DETERMINISTIC_EVAL",
    "DETERMINISTIC_LOGIC",
    "SYLLOGISTIC_DEDUCTION",
    "SYSTEM_COMMAND",
    "VERBATIM_RECALL",
}

WORD_TYPOS = {
    "teh": "the",
    "waht": "what",
    "wher": "where",
    "hwo": "how",
    "recieve": "receive",
    "seperate": "separate",
    "occured": "occurred",
    "definately": "definitely",
    "alise": "alice",
    "hidra": "hydra",
    "easyln": "easylm",
    "contianer": "container",
    "dockr": "docker",
    "pytohn": "python",
    "fucntion": "function",
    "retrun": "return",
    "lenght": "length",
    "widht": "width",
    "sevice": "service",
    "reqest": "request",
    "respose": "response",
}

VAGUE_RE = re.compile(
    r"^(fix it|do it|do this|do that|change it|change that|make it better|improve it|optimize it|refactor it|help|continue|go on)\.?$",
    re.IGNORECASE,
)

QUESTION_RE = re.compile(r"^(what|where|how|which|who|when|why)\b", re.IGNORECASE)

STACK_ROOTS = (
    ("hydra", r"C:\Users\jpm05\Documents\hydra", "CLI, agent loop, catalog, providers"),
    ("alice", r"C:\Users\jpm05\Documents\snowgate-alice\alice_core.js", "local cognitive core"),
    ("easylm", r"C:\Users\jpm05\Documents\hnai\easylm", "browser WebGPU inference"),
    ("progen", r"C:\Users\jpm05\Documents\.agents\rules\progen_invariants.md", "syntax invariants"),
    ("progen skill", r"C:\Users\jpm05\Documents\.agents\skills\progen\SKILL.md", "progen skill"),
    ("documents", r"C:\Users\jpm05\Documents\AGENTS.md", "workspace genome"),
)

STACK_SKILLS = {"stack", "hydra", "easylm", "alice"}


@dataclass
class GateDecision:
    action: str
    text: str
    route: str
    discovery: str = ""


def skills_dir() -> Path:
    return Path(__file__).resolve().parent / "skills"


def load_skills() -> List[Dict[str, Any]]:
    skills: List[Dict[str, Any]] = []
    root = skills_dir()
    if not root.is_dir():
        return skills
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, sep, body = text.partition("\n---\n")
        if not sep:
            meta, body = "", text
        fields: Dict[str, str] = {}
        for line in meta.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
        keys = [item.strip() for item in fields.get("keys", "").split(",") if item.strip()]
        skills.append({
            "id": fields.get("id", path.stem),
            "keys": keys,
            "body": body.strip(),
        })
    return skills


def stack_lines() -> List[str]:
    lines: List[str] = []
    for name, path, role in STACK_ROOTS:
        state = "present" if Path(path).exists() else "absent"
        lines.append(f"{name} : {state} : {path} : {role}")
    return lines


def stack_report() -> str:
    return "\n\n".join(stack_lines())


def skill_index_report() -> str:
    lines = ["skills :"]
    for skill in load_skills():
        first = skill["body"].splitlines()[0] if skill["body"] else skill["id"]
        lines.append(f"{skill['id']} : {first}")
    return "\n".join(lines)


def normalize_words(prompt: str) -> Tuple[str, List[str]]:
    notes: List[str] = []
    out: List[str] = []
    for token in prompt.split():
        core = token.strip(".,?!:;\"'`")
        low = core.lower()
        if low in WORD_TYPOS:
            fixed = WORD_TYPOS[low]
            notes.append(f"{core} -> {fixed}")
            if core:
                token = token.replace(core, fixed, 1)
        out.append(token)
    return " ".join(out), notes


def _key_hit(prompt: str, key: str) -> bool:
    low = prompt.lower()
    if " " in key:
        return key.lower() in low
    return re.search(rf"\b{re.escape(key)}\b", low) is not None


def _score(prompt: str, skill: Dict[str, Any]) -> int:
    return sum(1 for key in skill["keys"] if _key_hit(prompt, key))


def _format_alice(result: Dict[str, Any]) -> str:
    answer = str(result.get("answer") or "").strip()
    route = str(result.get("route") or "").strip()
    modality = str(result.get("epistemicModality") or "").strip()
    lines: List[str] = []
    if modality:
        lines.append(f"modality : {modality}")
    if answer:
        lines.append(answer)
    if route:
        lines.append(f"route : {route}")
    return "\n\n".join(lines)


def _with_route(text: str, route: str) -> str:
    marker = f"route : {route}"
    if marker in text:
        return text
    return text.rstrip() + f"\n\n{marker}"


def consult(
    prompt: str,
    *,
    summon_alias: str,
    has_referent: bool = False,
    prior: Optional[Tuple[str, str]] = None,
    evaluator: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    session: str = "default",
) -> GateDecision:
    """Return a local answer, a clarification, or a summon decision. Never calls a provider."""
    from hydra_cli import alice_retrieve

    if evaluator is None:
        from hydra_cli.alice_runner import evaluate_with_alice
        evaluator = evaluate_with_alice

    normalized, notes = normalize_words(prompt)
    note = ("normalized : " + "; ".join(notes) + "\n\n") if notes else ""
    retrieval_gap: Optional[Any] = None
    from_pending = alice_retrieve.is_followup(normalized, session)
    if not from_pending:
        alice_retrieve.clear_pending(session)

    if from_pending:
        found = alice_retrieve.answer(normalized, session=session)
        if found.action == "answer":
            return GateDecision(action="local", route=found.route, text=note + found.text)
        if found.action == "ask":
            return GateDecision(action="ask", route=found.route, text=note + found.text)
        retrieval_gap = found

    if retrieval_gap is None and VAGUE_RE.match(normalized.strip()) and not has_referent:
        return GateDecision(
            action="ask",
            route="VAGUENESS_DETECTED",
            text=_with_route(
                note + "clarification required : name the file, the error, or the proposition. the session has no prior turn.",
                "VAGUENESS_DETECTED",
            ),
        )

    ranked = sorted(((_score(normalized, skill), skill) for skill in load_skills()), key=lambda item: -item[0])
    top_score = ranked[0][0] if ranked else 0
    second = ranked[1][0] if len(ranked) > 1 else 0
    top = ranked[0][1] if ranked else None

    if retrieval_gap is None and QUESTION_RE.match(normalized.strip()) and top is not None and top_score >= 1 and top_score == second:
        ids = [skill["id"] for score, skill in ranked if score == top_score]
        return GateDecision(
            action="ask",
            route="DISAMBIGUATION",
            text=_with_route(
                note + "clarification required : the question matches " + ", ".join(ids) + ". name one subject.",
                "DISAMBIGUATION",
            ),
        )

    if retrieval_gap is None and QUESTION_RE.match(normalized.strip()) and top is not None and top_score >= 1 and top_score > second:
        body = top["body"]
        if top["id"] in STACK_SKILLS:
            body = body + "\n\n" + stack_report()
        return GateDecision(
            action="local",
            route="SKILL_INDEX",
            text=_with_route(note + body, "SKILL_INDEX"),
        )

    result: Optional[Dict[str, Any]] = None
    try:
        result = evaluator(normalized)
    except Exception:
        result = None

    core_route = str((result or {}).get("route") or "")
    if result and core_route in STRICT_ROUTES:
        return GateDecision(action="local", route=core_route, text=note + _format_alice(result))

    if retrieval_gap is None and alice_retrieve.is_inquiry(normalized):
        found = alice_retrieve.answer(normalized, session=session)
        if found.action == "answer":
            return GateDecision(action="local", route=found.route, text=note + found.text)
        if found.action == "ask":
            return GateDecision(action="ask", route=found.route, text=note + found.text)
        retrieval_gap = found

    if result and not from_pending:
        route = str(result.get("route") or "")
        formatted = _format_alice(result)
        if route in ANSWER_ROUTES:
            return GateDecision(action="local", route=route, text=note + formatted)
        if route in ASK_ROUTES and retrieval_gap is None:
            return GateDecision(action="ask", route=route, text=note + formatted)

    route = str((result or {}).get("route") or "ALICE_UNAVAILABLE")

    no_fallback = (
        summon_alias.lower().strip() in ("none", "off", "disabled", "alone")
        or os.environ.get("ALICE_NO_FALLBACK") == "1"
    )
    if retrieval_gap is not None and no_fallback:
        return GateDecision(action="local", route="EPISTEMIC_GAP", text=note + retrieval_gap.text)
    if no_fallback:
        if result and result.get("answer"):
            formatted = _format_alice(result)
            return GateDecision(action="local", route=route, text=note + formatted)
        ask_text = (
            note
            + "modality : [UNKNOWN]\n\n"
            + "epistemic gap : target proposition unverified in local core memory.\n\n"
            + "clarifying question : what is the foundational predicate or relation that defines this in your domain?\n\n"
            + "route : EPISTEMIC_GAP"
        )
        return GateDecision(action="ask", route="EPISTEMIC_GAP", text=ask_text)

    if result is None:
        miss = "alice_core.js returned no payload. the skill index missed."
    else:
        miss = f"alice route {route} has no local proof."
    skill_block = ""
    if top is not None and top_score >= 1:
        skill_block = f"skill {top['id']} :\n{top['body'][:1200]}\n"
    prior_block = ""
    if prior:
        prior_block = f"prior prompt : {prior[0][:400]}\nprior answer : {prior[1][:400]}\n"
    if retrieval_gap is not None:
        miss = "sentence retrieval stayed below threshold after the whitelist harvest and clarification rounds."
        prior_block += "retrieval :\n" + retrieval_gap.text[:1500] + "\n"
    discovery = (
        "[ALICE DISCOVERY]\n"
        f"{miss}\n"
        f"summon : {summon_alias}\n"
        "plan : read the named files, run the stated tests, inspect the named processes, and stop when the tool output holds the evidence.\n"
        + (f"normalized : {'; '.join(notes)}\n" if notes else "")
        + prior_block
        + skill_block
    )
    return GateDecision(action="summon", route=route, text="", discovery=discovery)
