"""
Alice Google Cloud Loop — Vertex AI / Gemini 2M Context Cognitive Engine.
Integrates Google Cloud Vertex AI / Gemini API (google-genai SDK) with Alice symbolic logic,
2M context window for deep document synthesis, token metering, and strict $100 budget enforcement.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

DOCUMENTS_ROOT = Path(__file__).resolve().parent.parent.parent
BUDGET_FILE = DOCUMENTS_ROOT / "kb" / "gcloud_budget.json"
DEFAULT_BUDGET_LIMIT_USD = 100.00
DEFAULT_PROJECT = "bastion-510306"
DEFAULT_LOCATION = "us-central1"
DEFAULT_SYNTHESIS_MODEL = "gemini-2.5-pro"
DEFAULT_FAST_MODEL = "gemini-2.5-flash"

# Pricing per million tokens (Vertex AI / Gemini API rates)
PRICING_TABLE = {
    "gemini-2.5-pro": {
        "prompt_standard": 1.25,
        "prompt_long": 2.50,
        "candidate_standard": 5.00,
        "candidate_long": 10.00,
        "long_threshold": 128000,
    },
    "gemini-2.5-flash": {
        "prompt_standard": 0.075,
        "prompt_long": 0.15,
        "candidate_standard": 0.30,
        "candidate_long": 0.60,
        "long_threshold": 128000,
    },
}


class BudgetExceededError(RuntimeError):
    """Raised when request would violate the $100 spending budget limit."""
    pass


class TokenBudgetTracker:
    """
    Persistent token and dollar expenditure tracker.
    Prevents runaway spending and bounds total expenditure strictly to allocated credit budget.
    """

    def __init__(self, budget_path: Optional[Path] = None, limit_usd: float = DEFAULT_BUDGET_LIMIT_USD):
        self.path = budget_path or BUDGET_FILE
        self.limit_usd = limit_usd
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.total_prompt_tokens = data.get("total_prompt_tokens", 0)
                self.total_candidate_tokens = data.get("total_candidate_tokens", 0)
                self.total_thought_tokens = data.get("total_thought_tokens", 0)
                self.total_tokens = data.get("total_tokens", 0)
                self.total_spent_usd = data.get("total_spent_usd", 0.0)
                self.limit_usd = data.get("budget_limit_usd", self.limit_usd)
                self.calls_count = data.get("calls_count", 0)
                self.history = data.get("history", [])
                return
            except Exception:
                pass
        self.total_prompt_tokens = 0
        self.total_candidate_tokens = 0
        self.total_thought_tokens = 0
        self.total_tokens = 0
        self.total_spent_usd = 0.0
        self.calls_count = 0
        self.history = []
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "budget_limit_usd": self.limit_usd,
            "total_spent_usd": round(self.total_spent_usd, 6),
            "remaining_budget_usd": round(max(0.0, self.limit_usd - self.total_spent_usd), 6),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_candidate_tokens": self.total_candidate_tokens,
            "total_thought_tokens": self.total_thought_tokens,
            "total_tokens": self.total_tokens,
            "calls_count": self.calls_count,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "history": self.history[-50:],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def calculate_cost(self, model: str, prompt_tokens: int, candidate_tokens: int) -> float:
        rates = PRICING_TABLE.get(model, PRICING_TABLE["gemini-2.5-flash"])
        threshold = rates["long_threshold"]
        prompt_rate = rates["prompt_long"] if prompt_tokens > threshold else rates["prompt_standard"]
        candidate_rate = rates["candidate_long"] if prompt_tokens > threshold else rates["candidate_standard"]
        cost = (prompt_tokens / 1_000_000.0) * prompt_rate + (candidate_tokens / 1_000_000.0) * candidate_rate
        return cost

    def check_permit(self, model: str, estimated_prompt_tokens: int = 1000) -> bool:
        est_cost = self.calculate_cost(model, estimated_prompt_tokens, 500)
        if self.total_spent_usd + est_cost > self.limit_usd:
            raise BudgetExceededError(
                f"Budget exceeded: spent ${self.total_spent_usd:.4f} of ${self.limit_usd:.2f} budget limit."
            )
        return True

    def record_usage(
        self,
        model: str,
        prompt_tokens: int,
        candidate_tokens: int,
        thought_tokens: int = 0,
        prompt_preview: str = "",
    ) -> float:
        cost = self.calculate_cost(model, prompt_tokens, candidate_tokens)
        self.total_prompt_tokens += prompt_tokens
        self.total_candidate_tokens += candidate_tokens
        self.total_thought_tokens += thought_tokens
        self.total_tokens += (prompt_tokens + candidate_tokens)
        self.total_spent_usd += cost
        self.calls_count += 1
        self.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "candidate_tokens": candidate_tokens,
            "thought_tokens": thought_tokens,
            "cost_usd": round(cost, 6),
            "prompt_preview": prompt_preview[:80],
        })
        self._save()
        return cost

    def status_summary(self) -> Dict[str, Any]:
        return {
            "budget_limit_usd": self.limit_usd,
            "total_spent_usd": round(self.total_spent_usd, 6),
            "remaining_budget_usd": round(max(0.0, self.limit_usd - self.total_spent_usd), 6),
            "total_tokens": self.total_tokens,
            "calls_count": self.calls_count,
        }


def check_gcp_environment() -> Dict[str, Any]:
    """
    Verify GCP authentication and environment (gcloud auth, gcloud config, ADC, API keys).
    Asserts raw predicates in Progen format.
    """
    status: Dict[str, Any] = {
        "gcloud_installed": shutil.which("gcloud") is not None,
        "adc_file_exists": False,
        "active_account": None,
        "active_project": None,
        "has_gemini_api_key": bool(os.environ.get("GEMINI_API_KEY")),
        "sdk_available": GENAI_AVAILABLE,
        "client_ready": False,
    }

    # Check ADC
    adc_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if adc_env and Path(adc_env).exists():
        status["adc_file_exists"] = True
        status["adc_path"] = adc_env
    else:
        appdata = os.environ.get("APPDATA")
        if appdata:
            default_adc = Path(appdata) / "gcloud" / "application_default_credentials.json"
            if default_adc.exists():
                status["adc_file_exists"] = True
                status["adc_path"] = str(default_adc)

    # Check gcloud account and project
    try:
        proc_acc = subprocess.run(
            ["gcloud", "config", "get-value", "account"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc_acc.returncode == 0:
            status["active_account"] = proc_acc.stdout.strip()
    except Exception:
        pass

    try:
        proc_proj = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc_proj.returncode == 0:
            status["active_project"] = proc_proj.stdout.strip()
    except Exception:
        pass

    if not status["active_project"]:
        status["active_project"] = DEFAULT_PROJECT

    if GENAI_AVAILABLE and (status["adc_file_exists"] or status["has_gemini_api_key"]):
        status["client_ready"] = True

    return status


def create_genai_client(
    project: Optional[str] = None,
    location: Optional[str] = None,
) -> Any:
    """
    Instantiate authenticated google-genai Client.
    Prefers Vertex AI backed by gcloud ADC, falls back to GEMINI_API_KEY.
    """
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-genai SDK not installed. Run: python -m pip install google-genai")

    proj = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or DEFAULT_PROJECT
    loc = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or DEFAULT_LOCATION
    api_key = os.environ.get("GEMINI_API_KEY")

    try:
        # Vertex AI client using Application Default Credentials
        client = genai.Client(vertexai=True, project=proj, location=loc)
        return client
    except Exception as e:
        if api_key:
            return genai.Client(api_key=api_key)
        raise RuntimeError(f"Failed to initialize GenAI client: {e}")


def synthesize_with_2m_context(
    query: str,
    context_documents: Optional[List[str]] = None,
    model: str = DEFAULT_SYNTHESIS_MODEL,
    system_instruction: Optional[str] = None,
    budget_tracker: Optional[TokenBudgetTracker] = None,
) -> Dict[str, Any]:
    """
    Execute deep synthesis query through Gemini 2.5 Pro utilizing the 2,000,000 token context window.
    Tracks exact token consumption against $100 spending budget.
    """
    tracker = budget_tracker or TokenBudgetTracker()
    tracker.check_permit(model, estimated_prompt_tokens=10000)

    client = create_genai_client()

    docs = context_documents or []
    joined_context = "\n\n---\n\n".join(docs) if docs else ""
    full_prompt = (
        f"Context Materials:\n{joined_context}\n\n"
        f"Inquiry / Task:\n{query}"
    ) if joined_context else query

    config_kwargs: Dict[str, Any] = {}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction

    config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

    start_t = time.perf_counter()
    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config=config,
    )
    elapsed_s = time.perf_counter() - start_t

    # Extract token telemetry
    meta = response.usage_metadata
    prompt_tokens = meta.prompt_token_count if meta else len(full_prompt) // 4
    candidate_tokens = meta.candidates_token_count if meta else len(response.text or "") // 4
    thought_tokens = getattr(meta, "thoughts_token_count", 0) or 0
    total_tokens = meta.total_token_count if meta else (prompt_tokens + candidate_tokens)

    cost = tracker.record_usage(
        model=model,
        prompt_tokens=prompt_tokens,
        candidate_tokens=candidate_tokens,
        thought_tokens=thought_tokens,
        prompt_preview=query,
    )

    return {
        "text": response.text.strip() if response.text else "",
        "model": model,
        "prompt_tokens": prompt_tokens,
        "candidate_tokens": candidate_tokens,
        "thought_tokens": thought_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "elapsed_seconds": round(elapsed_s, 2),
        "budget_remaining_usd": round(tracker.limit_usd - tracker.total_spent_usd, 4),
    }


def run_alice_gcloud_cli() -> None:
    """
    Interactive foreground CLI loop for Alice Google Cloud Engine.
    Operates in active terminal, easily monitored and stopped by user closing terminal or Ctrl+C.
    Zero background daemons.
    """
    tracker = TokenBudgetTracker()
    env = check_gcp_environment()

    print("================================================================")
    print("   ALICE COGNITIVE ENGINE ? GOOGLE CLOUD FOREGROUND CLI LOOP   ")
    print("================================================================")
    print(f"Active Account   : {env.get('active_account', 'ADC Default')}")
    print(f"GCP Project      : {env.get('active_project', DEFAULT_PROJECT)}")
    print(f"Primary Model    : {DEFAULT_SYNTHESIS_MODEL} (2M context window)")
    print(f"Fast Model       : {DEFAULT_FAST_MODEL}")
    print(f"Budget Limit     : ${tracker.limit_usd:.2f} USD")
    print(f"Spent so far     : ${tracker.total_spent_usd:.4f} USD")
    print(f"Remaining Budget : ${tracker.limit_usd - tracker.total_spent_usd:.4f} USD")
    print("Type 'exit' or 'quit' or press Ctrl+C to terminate.")
    print("================================================================\n")

    system_persona = (
        "You are Alice, the sovereign cognitive engine inspired by Lewis Carroll's Alice. "
        "Your demeanor is unflappable, deeply inquisitive, courteous, and analytically rigorous. "
        "You examine presuppositions with mathematical clarity. "
        "Adhere to Progen syntax topic : comment invariants when formulating state assertions. "
        "Never use leading copulas (is, are, was, were) in state comments."
    )

    while True:
        try:
            query = input("\nalice> ").strip()
            if not query:
                continue
            if query.lower() in ("exit", "quit", "q"):
                print("Terminating Alice Google Cloud session. Goodbye.")
                break

            print("[Thinking... dispatching through Gemini 2.5 Pro 2M loop]")
            res = synthesize_with_2m_context(
                query=query,
                model=DEFAULT_SYNTHESIS_MODEL,
                system_instruction=system_persona,
                budget_tracker=tracker,
            )
            print("\n" + res["text"])
            print(
                f"\n[Telemetry: {res['prompt_tokens']} prompt + {res['candidate_tokens']} output tokens "
                f"| ${res['cost_usd']:.5f} | remaining: ${res['budget_remaining_usd']:.4f} | {res['elapsed_seconds']}s]"
            )
        except (KeyboardInterrupt, EOFError):
            print("\nInterrupt received. Exiting Alice foreground session.")
            break
        except BudgetExceededError as be:
            print(f"\n[BUDGET HALT] {be}")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        env_stat = check_gcp_environment()
        for k, v in env_stat.items():
            print(f"{k} : {v}")
    elif len(sys.argv) > 1 and sys.argv[1] == "budget":
        b = TokenBudgetTracker()
        for k, v in b.status_summary().items():
            print(f"{k} : {v}")
    else:
        run_alice_gcloud_cli()