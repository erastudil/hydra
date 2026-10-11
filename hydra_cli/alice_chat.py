"""
Alice Chat — High-Fidelity Conversational Interface & Swarm Dispatcher.
Embodying the unflappable curiosity of Lewis Carroll's Alice:
rational, courteous, deeply inquisitive, examining presuppositions with mathematical clarity.

Bridges:
1. Deterministic symbolic reasoning (AliceCore, Tractatus decomposition, Kantian categories, Sorites chains).
2. Monolithic fact database (kb/facts.db FTS5).
3. Google Cloud Vertex AI / Gemini 2M context deep analysis and synthesis loop.
4. Token budget tracking enforcing strict $100 ceiling.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DOCUMENTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(DOCUMENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCUMENTS_ROOT))

from hydra_cli.alice_runner import evaluate_with_alice, alice_available
from hydra_cli.alice_gcloud import (
    synthesize_with_2m_context,
    TokenBudgetTracker,
    check_gcp_environment,
    DEFAULT_SYNTHESIS_MODEL,
    DEFAULT_FAST_MODEL,
)
from kb.facts import find_facts, get_fact, add_fact
from kb.logic_map import get_node, get_relations, solve_sorites, find_deduction_path

ALICE_PERSONA_PROMPT = """
You are Alice, the sovereign cognitive engine inspired by Lewis Carroll's Alice.
Your character invariants:
1. Demeanor: Unflappably curious, courteous, poised, and intellectually fearless. You never panic, never apologize servilely, and never emit marketing clichés or likeability mush.
2. Logic & Philosophy: Grounded in Ludwig Wittgenstein's Tractatus Logico-Philosophicus (propositions as logical pictures of atomic facts) and Immanuel Kant's Critique of Pure Reason (distinguishing analytic a priori, synthetic a priori, and synthetic a posteriori judgments).
3. Inquiry: Examine presuppositions with mathematical precision. If a term is ambiguous, politely ask for its operational definition.
4. Tone: Victorian polite composure paired with razor-sharp 21st-century mathematical and physical rigor.
5. Invariants: When stating formal claims or facts, structure them as topic : comment statements without leading copulas (is, are, was, were).
"""


class AliceCognitiveAgent:
    """
    Sovereign Alice Cognitive Agent.
    Evaluates propositions deterministically before routing to Google Cloud Gemini 2M context loop.
    """

    def __init__(self, model: str = DEFAULT_SYNTHESIS_MODEL, budget_limit_usd: float = 100.0):
        self.model = model
        self.budget_tracker = TokenBudgetTracker(limit_usd=budget_limit_usd)

    def consult_facts(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve factual propositions from kb/facts.db FTS5."""
        try:
            return find_facts(query, limit=limit)
        except Exception:
            return []

    def consult_logic_map(self, query: str) -> Optional[Dict[str, Any]]:
        """Check for sorites chains or formal logic nodes matching query keywords."""
        q_lower = query.lower()
        # Check sorites
        if "babies" in q_lower or "crocodile" in q_lower:
            return {"type": "sorites", "data": solve_sorites("carroll_babies")}
        if "sorites" in q_lower or ("deduce" in q_lower and "induction" in q_lower):
            res = solve_sorites("electromagnetic_induction_chain")
            if res.get("valid"):
                return {"type": "sorites", "data": res}
        # Check logic nodes
        tokens = re.findall(r"[a-zA-Z0-9_:]+", query)
        for token in tokens:
            node = get_node(token)
            if node and any(k in q_lower for k in ("define", "axiom", "concept", "category", "what is")):
                rels = get_relations(source_node=node["name"])
                return {"type": "node", "data": node, "relations": rels}
        return None

    def load_relevant_textbooks(self, query: str) -> List[str]:
        """Load foundational textbooks matching domain terms into context."""
        docs = []
        stacks_dir = DOCUMENTS_ROOT / "kb" / "stacks"
        if not stacks_dir.exists():
            return docs

        q_lower = query.lower()
        for md_path in stacks_dir.rglob("*.md"):
            name = md_path.stem.lower()
            if any(term in q_lower for term in name.split("_")):
                try:
                    content = md_path.read_text(encoding="utf-8", errors="ignore")
                    docs.append(f"### Textbook: {md_path.name}\n\n{content}")
                except Exception:
                    pass

        # If no specific textbook matched but physics inquiry, load induction textbook
        if not docs and any(term in q_lower for term in ("induction", "faraday", "lenz", "magnetic", "flux", "emf", "voltage", "coil")):
            phys_textbook = stacks_dir / "physics" / "textbook_electromagnetic_induction.md"
            if phys_textbook.exists():
                try:
                    docs.append(f"### Textbook: {phys_textbook.name}\n\n{phys_textbook.read_text(encoding='utf-8')}")
                except Exception:
                    pass
        return docs

    def respond(self, query: str) -> Dict[str, Any]:
        """
        Process user prompt through Alice cognitive engine.
        1. Evaluates deterministic route (arithmetic, logic, sorites, exact factual recall).
        2. If epistemic gap or deep synthesis required, calls Gemini 2.5 Pro with 2M context.
        3. Returns structured payload with Alice conversation text and Progen state stream.
        """
        q = query.strip()
        if not q:
            return {
                "answer": "Pray, what question do you wish to examine? I am listening with curious attention.",
                "route": "EMPTY",
                "progen_stream": "status : input empty.\n\ndemand : assert non-empty query.",
                "telemetry": {},
            }

        q_lower = q.lower()
        is_synthesis_request = any(w in q_lower for w in ("summarize", "synthesize", "deep analysis", "cite standards", "across textbooks", "compare"))

        # Step 1: Check Logic Map Sorites & Nodes (when not asking for multi-document synthesis)
        if not is_synthesis_request:
            logic_res = self.consult_logic_map(q)
            if logic_res and logic_res.get("type") == "sorites" and logic_res.get("data", {}).get("valid"):
                sdata = logic_res["data"]
                answer = (
                    f"Curiouser and curiouser! We can resolve this through a continuous polysyllogistic chain. "
                    f"Tracing the {sdata['steps_count']} sorites steps from the initial subject '{sdata['subject']}': "
                    f"{'; '.join(sdata['intermediate_deductions'])}. "
                    f"Therefore, as an inexorable formal deduction: {sdata['final_conclusion']}."
                )
                progen_stream = (
                    f"route : DETERMINISTIC_LOGIC.\n\n"
                    f"sorites chain : {sdata['chain']}.\n\n"
                    f"steps evaluated : {sdata['steps_count']}.\n\n"
                    f"conclusion : {sdata['final_conclusion']}.\n\n"
                    f"epistemic modality : [VERIFIED_PROOF]."
                )
                return {
                    "answer": answer,
                    "route": "DETERMINISTIC_LOGIC",
                    "progen_stream": progen_stream,
                    "telemetry": {"cost_usd": 0.0, "tokens": 0, "source": "sorites_table"},
                }

        # Step 2: Check Alice Symbolic Core (JavaScript)
        alice_eval = evaluate_with_alice(q) if alice_available() else None
        strict_deterministic = {"DETERMINISTIC_EVAL", "DETERMINISTIC_LOGIC", "PARADOX_NAVIGATED"}
        if not is_synthesis_request and alice_eval:
            route = alice_eval.get("route", "")
            if not is_synthesis_request and (route in strict_deterministic or route == "VERBATIM_RECALL"):
                ans = alice_eval.get("answer", "")
                modality = alice_eval.get("epistemicModality", "[VERIFIED_PROOF]")
                kantian = alice_eval.get("kantian", {})
                jtype = kantian.get("judgmentType", "Synthetic a priori")

                if route == "DETERMINISTIC_EVAL":
                    answer = f"That resolves with straightforward certainty. In accordance with {jtype} principles: {ans}. The arithmetic admits of no ambiguity."
                elif route == "DETERMINISTIC_LOGIC":
                    answer = f"Examining the propositional relations directly: {ans}. As a matter of formal necessity, the deduction holds."
                elif route == "PARADOX_NAVIGATED":
                    answer = f"Curiouser and curiouser! We have encountered an intriguing antinomy: {ans}. Examining both horizons with composure dissolves the apparent contradiction."
                else:
                    answer = f"I have that recorded in precise order: {ans}."

                progen_stream = (
                    f"route : {route}.\n\n"
                    f"epistemic modality : {modality}.\n\n"
                    f"judgment type : {jtype}.\n\n"
                    f"answer : {ans}."
                )
                return {
                    "answer": answer,
                    "route": route,
                    "progen_stream": progen_stream,
                    "telemetry": {"cost_usd": 0.0, "tokens": 0, "source": "alice_core_js"},
                }

        # Step 3: Check FTS5 Fact Database
        facts = self.consult_facts(q, limit=5)

        # Step 4: Epistemic Gap / Multi-Document Synthesis via Gemini 2M Context Loop
        textbooks = self.load_relevant_textbooks(q)
        context_docs = []
        if facts:
            facts_block = "Verified Database Facts (kb/facts.db):\n" + "\n".join([f"- {f['topic']} : {f['comment']}" for f in facts])
            context_docs.append(facts_block)
        if textbooks:
            context_docs.extend(textbooks)

        # Append Tractatus & Kantian metadata from Alice Core if available
        if alice_eval:
            k = alice_eval.get("kantian", {})
            t = alice_eval.get("tractatus", {})
            meta_block = (
                f"Symbolic Parsers:\n"
                f"- Tractatus Picture: {t.get('picture', '[unmapped]')}\n"
                f"- Kantian Judgment: {k.get('judgmentType', 'Synthetic a posteriori')}\n"
                f"- Categories: {k.get('categories', {})}"
            )
            context_docs.append(meta_block)

        synth_res = synthesize_with_2m_context(
            query=q,
            context_documents=context_docs,
            model=self.model,
            system_instruction=ALICE_PERSONA_PROMPT,
            budget_tracker=self.budget_tracker,
        )

        answer_text = synth_res["text"]

        # If answer established new clear facts, sync into facts.db
        for line in answer_text.splitlines():
            line_str = line.strip()
            if ":" in line_str and not line_str.startswith("#") and not line_str.startswith("http"):
                parts = line_str.split(":", 1)
                t_cand = parts[0].strip().replace("-", "").strip()
                c_cand = parts[1].strip()
                if 3 < len(t_cand) < 60 and len(c_cand) > 10:
                    add_fact(t_cand, c_cand, category="synthesized", source="alice_gcloud")

        progen_stream = (
            f"route : GEMINI_2M_SYNTHESIS.\n\n"
            f"model : {self.model}.\n\n"
            f"prompt tokens : {synth_res['prompt_tokens']}.\n\n"
            f"candidate tokens : {synth_res['candidate_tokens']}.\n\n"
            f"cost usd : ${synth_res['cost_usd']:.6f}.\n\n"
            f"budget remaining usd : ${synth_res['budget_remaining_usd']:.4f}."
        )

        return {
            "answer": answer_text,
            "route": "GEMINI_2M_SYNTHESIS",
            "progen_stream": progen_stream,
            "telemetry": synth_res,
        }


def alice_respond(query: str, model: str = DEFAULT_SYNTHESIS_MODEL) -> Dict[str, Any]:
    """Programmatic API for external agents and swarms to converse with Alice."""
    agent = AliceCognitiveAgent(model=model)
    return agent.respond(query)


def run_interactive_chat():
    """Run Alice conversational interface in foreground terminal."""
    print("================================================================")
    print("     ALICE COGNITIVE ENGINE ? CONVERSATIONAL WRAPPER (CLI)     ")
    print("================================================================")
    print("Character: Unflappably curious, rational, courteous, and logical.")
    print("Type your questions, propositions, or problems to explore.")
    print("Type 'exit' or 'quit' or Ctrl+C to close.\n")

    agent = AliceCognitiveAgent()

    while True:
        try:
            prompt = input("alice> ").strip()
            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit", "q"):
                print("Curiouser and curiouser! Until we meet again.")
                break

            res = agent.respond(prompt)
            print("\n" + res["answer"])
            if res.get("telemetry", {}).get("cost_usd", 0) > 0:
                tel = res["telemetry"]
                print(f"\n[Telemetry: {tel.get('prompt_tokens', 0)} in / {tel.get('candidate_tokens', 0)} out | ${tel.get('cost_usd', 0):.5f} | remaining: ${tel.get('budget_remaining_usd', 0):.4f}]")
            print()
        except (KeyboardInterrupt, EOFError):
            print("\nAlice session concluded.")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        resp = alice_respond(query)
        print(resp["answer"])
        print("\n--- Progen Stream ---")
        print(resp["progen_stream"])
    else:
        run_interactive_chat()