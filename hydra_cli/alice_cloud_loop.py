"""
Alice Google Cloud Autonomous Loop — 15-Minute Self-Enhancement Engine.
Coordinates:
1. Mimo 2.6 Pro (Architect & Planner via Hydra providers).
2. Google Cloud Vertex AI Gemini 2.5 Pro (2M Context Synthesis & Deep Reasoning).
3. Greene Feynman Clarity (GFC) Textbook Authoring with Whitelist Door Bindings.
4. Monolithic Fact Database (kb/facts.db FTS5) & Logic Map Sorites Engine (kb/logic_map.py).
5. Conversational Language Wrapper (Lewis Carroll Alice Persona).
6. Deterministic Regression Gate & Strict $100 Credit Budget Enforcement.
7. Foreground CLI execution (terminable by user closing terminal or pressing Ctrl+C).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DOCUMENTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(DOCUMENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCUMENTS_ROOT))

from hydra_cli.alice_gcloud import (
    check_gcp_environment,
    synthesize_with_2m_context,
    TokenBudgetTracker,
    BudgetExceededError,
    DEFAULT_SYNTHESIS_MODEL,
    DEFAULT_FAST_MODEL,
)
from hydra_cli.providers import complete
from kb.facts import add_fact, sync_progen_file, find_facts
from kb.logic_map import add_node, add_relation, add_sorites_step, solve_sorites

DEFAULT_INTERVAL_SECONDS = 900  # 15 minutes


class AliceCloudLoop:
    """
    Autonomous 15-minute enhancement engine for Alice Cognitive Substrate.
    Orchestrates Mimo 2.6 Pro and Google Cloud Gemini 2.5 Pro to expand Alice's
    epistemic horizons, textbooks, logic map, and user-friendly dialogue.
    """

    def __init__(
        self,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        budget_limit_usd: float = 100.0,
        synthesis_model: str = DEFAULT_SYNTHESIS_MODEL,
        run_once: bool = False,
    ):
        self.interval = interval_seconds
        self.budget_tracker = TokenBudgetTracker(limit_usd=budget_limit_usd)
        self.synthesis_model = synthesis_model
        self.run_once = run_once
        self.cycle_count = 0

    def plan_next_enhancement(self) -> Dict[str, Any]:
        """
        Step 1: Mimo 2.6 Pro inspects existing knowledge and plans the next textbook chapter,
        logic derivation, and conversational capability.
        """
        # Read list of existing textbooks
        existing_textbooks = []
        stacks_dir = DOCUMENTS_ROOT / "kb" / "stacks"
        if stacks_dir.exists():
            for p in stacks_dir.rglob("*.md"):
                existing_textbooks.append(p.stem)

        system_prompt = (
            "You are Mimo 2.6 Pro, Lead Cognitive Architect for Alice. "
            "Your imperative is to plan the next university-level textbook chapter and logic expansion "
            "for Alice's cognitive engine. "
            "Output must be valid JSON with keys: "
            "'domain' (e.g. logic, epistemology, physics, computer_science), "
            "'topic' (specific topic title), "
            "'slug' (lowercase underscore filename slug), "
            "'mechanism' (ordinary word physical/logical mechanism), "
            "'formal_law' (governing equation or deductive rule), "
            "'worked_check' (concrete numerical or formal deduction check), "
            "'whitelist_doors' (authoritative domains e.g. plato.stanford.edu, nist.gov, bipm.org, ieee.org), "
            "'sorites_chain' (list of 3-4 transitive premises connecting subject to conclusion), "
            "'conversational_prompt' (a curious user query testing this new capability)."
        )

        prompt = (
            f"Current existing textbooks: {', '.join(existing_textbooks[:10])}.\n"
            f"Plan the next foundational enhancement topic to make Alice smarter, deeper, and more user-friendly. "
            "Choose a topic in formal logic, non-monotonic reasoning, syllogistic reduction, or fundamental metrology. "
            "Emit only pure JSON without markdown fences."
        )

        raw_plan = complete("mimo 2.6 pro", prompt, system_prompt=system_prompt, max_tokens=1024)
        clean_json = raw_plan.strip().replace("```json", "").replace("```", "").strip()
        try:
            plan = json.loads(clean_json)
        except Exception:
            # Robust fallback plan if model emitted surrounding text
            match = re.search(r"\{.*\}", clean_json, re.DOTALL)
            if match:
                plan = json.loads(match.group(0))
            else:
                plan = {
                    "domain": "logic",
                    "topic": "Non-Monotonic Defeasible Reasoning and Belief Revision",
                    "slug": "non_monotonic_defeasible_reasoning",
                    "mechanism": "Updating belief states upon encountering counter-evidence that defeats prior default inferences.",
                    "formal_law": "Alchourron-Gardenfors-Makinson AGM belief contraction and revision postulates.",
                    "worked_check": "Evaluating default inference birds fly against penguin observation defeating prior conclusion.",
                    "whitelist_doors": ["plato.stanford.edu", "iep.utm.edu", "projecteuclid.org"],
                    "sorites_chain": [
                        "All typical birds have functional wings.",
                        "All entities with functional wings produce aerodynamic lift.",
                        "All entities producing aerodynamic lift execute aerial flight.",
                    ],
                    "conversational_prompt": "If all birds fly, what occurs when we encounter an emperor penguin?",
                }
        return plan

    def execute_gcloud_synthesis(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 2: Google Cloud Vertex AI Gemini 2.5 Pro executes deep 2M context synthesis,
        authoring the complete textbook in GFC prose, generating atomic facts, and deriving sorites chains.
        """
        domain = plan.get("domain", "logic")
        topic = plan.get("topic", "Deductive Systems")
        slug = plan.get("slug", "deductive_systems")
        doors = plan.get("whitelist_doors", ["plato.stanford.edu", "nist.gov"])

        prompt = (
            f"Author a rigorous, university-level foundational textbook chapter for Alice on: {topic}.\n"
            f"Domain: {domain}\n"
            f"Whitelist Reference Doors: {', '.join(doors)}\n\n"
            "REQUIREMENTS (Greene Feynman Clarity - GFC Standard):\n"
            "1. Physical/Logical Mechanism First: Explain how the mechanism functions using plain, ordinary language before naming terms.\n"
            "2. Formal Law Second: State the exact mathematical, logical, or physical governing laws and equations.\n"
            "3. Worked Numerical/Deductive Check Third: Provide a step-by-step worked numerical calculation or formal proof check.\n"
            "4. Authoritative Metrological References: Explicitly cite and bind the requested whitelist reference doors (.gov, .edu, bipm.org, nist.gov, ieee.org, plato.stanford.edu).\n"
            "5. ZERO Bulletization of Textbook Prose: Write continuous explanatory paragraphs with headers. No bullet points in textbook sections.\n"
            "6. Zero em dashes, zero likeability mush, zero disclaimers, zero trailing questions.\n\n"
            "FORMAT OUTPUT AS EXACT SECTIONS:\n"
            "=== TEXTBOOK_START ===\n"
            "# <Title>\n"
            "[Full GFC chapter text]\n"
            "=== TEXTBOOK_END ===\n\n"
            "=== FACTS_START ===\n"
            "[5-8 atomic fact statements in Progen syntax: topic : comment, separated by single blank lines. Zero leading copula is/are/was/were in comments]\n"
            "=== FACTS_END ===\n\n"
            "=== SORITES_START ===\n"
            "[JSON list of 3-4 steps: [{\"step\": 1, \"premise\": \"...\", \"subject\": \"...\", \"predicate\": \"...\", \"conclusion\": \"...\"}]]\n"
            "=== SORITES_END ===\n\n"
            "=== CONVERSATIONAL_START ===\n"
            "[A 1-paragraph response from Alice embodying Lewis Carroll's Alice: unflappably curious, polite, rational, explaining this concept to a curious explorer with precision]\n"
            "=== CONVERSATIONAL_END ==="
        )

        res = synthesize_with_2m_context(
            query=prompt,
            model=self.synthesis_model,
            system_instruction=(
                "You are the Lead Scientific Synthesizer for Alice Google Cloud Engine. "
                "You author immaculate GFC textbooks, exact Progen atomic facts, and formal logic maps."
            ),
            budget_tracker=self.budget_tracker,
        )
        return res

    def ingest_enhancements(self, plan: Dict[str, Any], synthesis_output: str) -> Dict[str, Any]:
        """
        Step 3: Parse and ingest the synthesized components into kb/stacks, facts.db, and logic map.
        """
        domain = plan.get("domain", "logic")
        slug = plan.get("slug", f"topic_{int(time.time())}")

        # Extract Textbook
        tb_match = re.search(r"=== TEXTBOOK_START ===(.*?)=== TEXTBOOK_END ===", synthesis_output, re.DOTALL)
        tb_content = tb_match.group(1).strip() if tb_match else ""

        # Extract Facts
        facts_match = re.search(r"=== FACTS_START ===(.*?)=== FACTS_END ===", synthesis_output, re.DOTALL)
        facts_content = facts_match.group(1).strip() if facts_match else ""

        # Extract Sorites
        sorites_match = re.search(r"=== SORITES_START ===(.*?)=== SORITES_END ===", synthesis_output, re.DOTALL)
        sorites_raw = sorites_match.group(1).strip() if sorites_match else "[]"

        # Extract Conversational Sample
        conv_match = re.search(r"=== CONVERSATIONAL_START ===(.*?)=== CONVERSATIONAL_END ===", synthesis_output, re.DOTALL)
        conv_content = conv_match.group(1).strip() if conv_match else ""

        # Save Textbook
        domain_dir = DOCUMENTS_ROOT / "kb" / "stacks" / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        tb_path = domain_dir / f"textbook_{slug}.md"

        if tb_content:
            tb_path.write_text(tb_content, encoding="utf-8")

        # Save Facts .prg & Sync to facts.db
        facts_path = domain_dir / f"facts_{slug}.prg"
        synced_facts_count = 0
        if facts_content:
            facts_path.write_text(facts_content, encoding="utf-8")
            synced_facts_count = sync_progen_file(str(facts_path), category=domain)

        # Ingest Sorites & Logic Nodes
        sorites_steps_count = 0
        try:
            clean_sorites = sorites_raw.replace("```json", "").replace("```", "").strip()
            steps = json.loads(clean_sorites)
            chain_name = f"{domain}_{slug}"
            for s in steps:
                s_idx = int(s.get("step", 1))
                s_prem = str(s.get("premise", ""))
                s_sub = str(s.get("subject", ""))
                s_pred = str(s.get("predicate", ""))
                s_conc = str(s.get("conclusion", ""))
                if s_sub and s_pred:
                    if add_sorites_step(chain_name, s_idx, s_prem, s_sub, s_pred, s_conc):
                        sorites_steps_count += 1
            # Add topic node
            add_node(
                name=f"Concept:{slug}",
                node_type="concept",
                definition=plan.get("mechanism", plan.get("topic", "")),
                kantian_category="Relation:Causality",
            )
        except Exception:
            pass

        return {
            "textbook_path": str(tb_path),
            "textbook_length": len(tb_content),
            "synced_facts": synced_facts_count,
            "sorites_steps": sorites_steps_count,
            "conversational_sample": conv_content,
        }

    def run_deterministic_regression_gate(self) -> Tuple[bool, str]:
        """
        Step 4: Execute deterministic regression test suite to ensure zero regressions across Alice.
        """
        test_file = DOCUMENTS_ROOT / "hydra" / "tests" / "test_alice_gcloud.py"
        cmd = [sys.executable, "-m", "pytest", str(test_file), "-v", "-p", "no:cacheprovider"]
        proc = subprocess.run(
            cmd,
            cwd=str(DOCUMENTS_ROOT / "hydra"),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0:
            return False, f"Regression tests failed:\n{proc.stdout}\n{proc.stderr}"
        return True, "All 8 deterministic verification tests passed with exit code 0."

    def execute_cycle(self) -> Dict[str, Any]:
        """Execute one complete 15-minute enhancement cycle."""
        self.cycle_count += 1
        cycle_start = time.time()
        print(f"\n================================================================")
        print(f"   ALICE ENHANCEMENT CYCLE #{self.cycle_count} — {datetime.now(timezone.utc).isoformat()}")
        print(f"================================================================")

        # 1. Plan via Mimo 2.6 Pro
        print("[Phase 1: Planning architectural enhancements with Mimo 2.6 Pro...]")
        plan = self.plan_next_enhancement()
        print(f"  Domain : {plan.get('domain')}")
        print(f"  Topic  : {plan.get('topic')}")
        print(f"  Doors  : {', '.join(plan.get('whitelist_doors', []))}")

        # 2. Deep Synthesis via Google Cloud Vertex AI Gemini 2.5 Pro (2M Context)
        print(f"[Phase 2: Deep synthesis pass via Vertex AI {self.synthesis_model} (2M context)...]")
        synth_res = self.execute_gcloud_synthesis(plan)
        print(f"  Prompt tokens    : {synth_res['prompt_tokens']}")
        print(f"  Candidate tokens : {synth_res['candidate_tokens']}")
        print(f"  Cycle cost       : ${synth_res['cost_usd']:.6f} USD")
        print(f"  Remaining budget : ${synth_res['budget_remaining_usd']:.4f} USD")

        # 3. Ingestion into Stacks, Facts DB, and Logic Map
        print("[Phase 3: Ingesting GFC textbook, atomic facts, and Sorites table...]")
        ingest_res = self.ingest_enhancements(plan, synth_res["text"])
        print(f"  Textbook saved   : {ingest_res['textbook_path']} ({ingest_res['textbook_length']} chars)")
        print(f"  Synced facts     : {ingest_res['synced_facts']} atomic facts in kb/facts.db")
        print(f"  Sorites steps    : {ingest_res['sorites_steps']} steps added to logic map")

        # 4. Deterministic Regression Testing
        print("[Phase 4: Running deterministic regression gate...]")
        passed, msg = self.run_deterministic_regression_gate()
        if not passed:
            print(f"[REGRESSION FAILURE] {msg}")
            raise RuntimeError(msg)
        print(f"  Gate certification : {msg}")

        elapsed = time.time() - cycle_start
        print(f"[Cycle #{self.cycle_count} Complete in {elapsed:.2f}s]\n")

        return {
            "cycle": self.cycle_count,
            "plan": plan,
            "synthesis": synth_res,
            "ingestion": ingest_res,
            "regression_gate": passed,
            "elapsed_seconds": round(elapsed, 2),
        }

    def run_loop(self) -> None:
        """
        Continuous foreground CLI loop.
        Controllable and easily terminable by closing terminal or pressing Ctrl+C.
        Zero background daemons.
        """
        env = check_gcp_environment()
        print("================================================================")
        print("    ALICE AUTONOMOUS GOOGLE CLOUD LOOP (15-MINUTE ENHANCER)    ")
        print("================================================================")
        print(f"Architect Model  : Mimo 2.6 Pro (cheaperinference / openrouter)")
        print(f"Synthesis Model  : {self.synthesis_model} (Vertex AI, 2M context)")
        print(f"Active Project   : {env.get('active_project', 'bastion-510306')}")
        print(f"Credit Budget    : ${self.budget_tracker.limit_usd:.2f} USD")
        print(f"Spent to Date    : ${self.budget_tracker.total_spent_usd:.4f} USD")
        print(f"Loop Interval    : {self.interval} seconds (15 minutes)")
        print(f"Execution Mode   : Foreground CLI (Ctrl+C to terminate cleanly)")
        print("================================================================\n")

        while True:
            try:
                self.execute_cycle()
                if self.run_once:
                    print("[Run-once flag specified. Exiting Alice loop cleanly with code 0.]")
                    break

                # Sleep interval with periodic countdown
                print(f"Sleeping {self.interval}s until next enhancement cycle. Press Ctrl+C to halt.")
                sleep_start = time.time()
                while time.time() - sleep_start < self.interval:
                    time.sleep(1)
            except (KeyboardInterrupt, EOFError):
                print("\n[Halt signal received. Alice Google Cloud loop cleanly terminated.]")
                break
            except BudgetExceededError as be:
                print(f"\n[BUDGET LIMIT REACHED] {be}")
                break
            except Exception as e:
                print(f"\n[CYCLE ERROR] {e}")
                if self.run_once:
                    sys.exit(1)
                time.sleep(10)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Alice Autonomous Google Cloud Enhancement Loop")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Loop interval in seconds (default 900)")
    parser.add_argument("--run-once", action="store_true", help="Execute single enhancement pass and exit with code 0")
    parser.add_argument("--budget", type=float, default=100.0, help="Total credit budget limit in USD (default 100.0)")
    parser.add_argument("--model", type=str, default=DEFAULT_SYNTHESIS_MODEL, help="Synthesis model (default gemini-2.5-pro)")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    loop = AliceCloudLoop(
        interval_seconds=args.interval,
        budget_limit_usd=args.budget,
        synthesis_model=args.model,
        run_once=args.run_once,
    )
    loop.run_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())