# Hydra — Spec addendum — 2026-11-10, spec pass

Additive addendum. This file records what the fleet record settles about
Hydra as of 2026-11-10. It edits no code and supersedes no existing spec
text; where it and an older document disagree, the dated provenance line
under each section names the newer source.

## What Hydra is

A sovereign, multi-headed model summoning engine and router: a buyer
downloads it and runs it on their own hardware, buy once, no
subscription. Product framing: owner, Oct 27, 2026. Hydra's parity
standard: it should be able to do anything any other harness can do.
Owner, Nov 1, 2026. Its strength claim is local: free endpoints, local
multimodal models, and Bonsai combined should squeeze near-frontier
intelligence out of the owner's laptop, tolerating higher latency as
the tradeoff. Owner, Nov 1, 2026.

Resolved: source `~/memory/topics/projects.md`, Oct 27 and Nov 1, 2026
entries; GUIDE.md in this repo for the CLI, swarm, sovereign gateway,
MCP, and Hermes/Pi delegation surfaces as shipped in 1.1.0/1.2.0.

## Settled seat map, landed

Landed on main through PRs #55–#58 and #60; main stood at 730d871,
the PR #61 merge, on Nov 10, 2026.

- Local default: Bonsai 2B, Q2_0 weights with the mmproj Q8_0
  projector, served at 128K context, multimodal. The local server loads
  on Hydra local start and unloads after 1 hour idle; no standing
  residency.
- Gemma E2B is a named seat. Gemma E4B is the local heavy escalation.
  Bonsai 9B and 4B are on demand.
- Free lane, in order: Nemotron lightning-free, then Gemma-4-26B,
  Gemma-4-31B, Nemotron-3-Super, with Ling as the free escalation
  alternate.
- Light/global default: muse-spark-1.3-contributor.

Resolved: source owner seat-map ruling Nov 9, 2026, landed PRs #55–#58
and #60; record at `~/MEMORY.md`, Hydra model seats entry;
`docs/QUEUE.md` WO-HYDRA-PERF-1 restates the default on main at
730d871.

## Vendor-verified context windows and output rules

Capability claims are read off the publisher's artifact list and model
card, never inferred from what is staged locally. Owner law, Nov 8,
2026; specimen: the week-long "Bonsai has no vision" error.

- Gemma 4 26B and 31B: 262,144 tokens per Google. The 26B route had
  been profiled at 131,072, a Gemma 3-era figure, and the 31B at a 1M
  budget; PR #58 of Nov 9, 2026 corrected both after the owner
  challenged the figures.
- Cloudflare Workers AI Llama 3.3 70B fp8-fast serves only a 24,000
  token window, against Meta's native 128K, with a 256-token default
  output cap. Every Cloudflare free call must set max-tokens
  explicitly; a silent small default is a defect, not a setting.
- Summoned models run at full context and vendor-spec parameters.
  Owner ruling, Nov 9, 2026: cloud is 1M class with 256K as the small
  end; sampling follows the model's own vendor spec.
- Output sizing, owner, Nov 10, 2026: max output is 25% of served
  context; 32K is the standing local output ceiling unless a task sets
  higher.

Resolved: sources `~/memory/2026-11-10.md`, lines recording PR #58 and
the Nov 10, 2026 output rulings at 02:04 and 02:08 CST;
`~/workspace/factory/METER-STRATEGY.md`, Cloudflare max-tokens rule.

## Local ceiling — owner ruling, Nov 10, 2026

Gemma E4B and Bonsai are the ceiling for local laptop agent duty on
the 4060. The 12B/26B/27B class is dead on arrival locally and lives on
Colab-class hardware: Gemma 4 26B measured 1.47 tok/s prefill on
mr-computer, a 47-token prompt took 32.05 s of prefill, generation
~12.3 tok/s. Escalation from Bonsai 2B is earned only by demonstrated
failure on real work, never assumed. Owner, Nov 9, 2026.

Resolved: source `~/memory/2026-11-10.md`, Nov 10, 2026 ~01:42 CST
ruling and the measured 26B log line.

## Modes doctrine — owner, Nov 1, 2026

- local: local models only.
- free: configured free endpoints with a routing table and fallbacks,
  each model defaulted to its biggest context.
- light: paid endpoints only for models under $1 per million tokens
  either way.
- heavy: anything up to Opus-level pricing.
- max: Fable/Astra class when it makes sense; every credit on every
  platform fair game.

Front desk doctrine, Nov 1, 2026: the smallest multimodal that benches
well runs the front desk; Bonsai is the default coder; the front desk
frees VRAM when a coding model or image generator needs the GPU. The
desktop interface stays as simple as Muse or Grok Bot; the TUI is the
full-guts display. Context management is 3-tier VRAM / RAM / disk:
threads pickable up and put down, with a full exact record on disk.

Resolved: source `~/memory/topics/projects.md`, Nov 1, 2026 entries.

## Queue state at this pass

`docs/QUEUE.md` on main carries the cut queue WO-HYDRA-C01 through C13,
converted from the factory subtraction tranche 1 cut list after
independent spot-verification: 13 of 16 items passed; H-02, H-05, and
H-14 failed as scoped and are not in the queue. The verified removable
total is roughly 8.3K–11.4K lines; the cut list's ~23K headline did not
stand as written. None of C01–C13 has been executed at this pass.
WO-HYDRA-PERF-1, local default chat reaching Pi-class responsiveness
with Bonsai 2B in Pi on Nov 9, 2026 as the graded baseline, and
WO-HYDRA-UI-1, desktop UI rework toward a clean base view, icons with
tooltips, and collapsible sidebars, closing on the owner's grade, were
queued Nov 10, 2026 and merged in PR #61, main 730d871.

C01 handles this VM checkout: `~/workspace/hydra` sits on the dead
branch `feat/groq-summon-wiring` at ebe8a6b, 351 commits behind main,
with two working-tree deletions, GLM_5.3_REVIEW.md and
docs/TRIUMVIRATE_ARCHITECTURAL_ASSESSMENT.md, and stays live only
until the forum lane is repointed off it and the clone is retired to a
dated Drive pool under C01's own acceptance criteria. This addendum
was therefore written against origin/main at 730d871, not against the
VM tree.

Resolved: sources `docs/QUEUE.md` at origin/main 730d871;
`erastudil/factory` subtraction/hydra-CUT-LIST-VERIFICATION-2026-11-09.md;
`~/memory/2026-11-10.md`, Nov 9, 2026 ~23:59 CST entry on PR #61.

## Doctrine constraints that bind Hydra work

- Sovereign software, owner, Nov 10, 2026: software the buyer owns
  once bought; moddable; external dependency minimized; everything
  needed in the package.
- Standalone Rust default, owner, Nov 10, 2026: plan A for new code
  is a compiled Rust standalone executable; a web face exists only on
  explicit ask. Hydra's Rust line — hydra-tui-rs, hydra-desktop-rs,
  hydra-stdio — is the destination surface under the language exit;
  the Python CLI is legacy with its exit paths recorded in the
  factory language-exit inventory and the C-series cuts; C12 retires
  the Python TUI rendering layer toward hydra-tui-rs.
- Browser is Brave, always, owner, Nov 10, 2026; Microsoft Edge is
  banned absolutely. The Hydra Playwright browser leg in its Edge
  form, driven through the msedge channel, is dead until rebuilt on
  Brave; C10's browser.py work is Playwright-facility consolidation
  and does not revive the Edge leg.
- Steer at the cheap stage, owner, Nov 9, 2026: correctness is
  established at the cheapest stage that can falsify it; a paid call
  is the last step of a verified chain. Benchmarking is killed for
  now, owner, Nov 9, 2026: code and content first.
- Git discipline: work lands through this repo's gate — branch, PR,
  merge to main, tree clean; nobody grades their own homework. Repo
  AGENTS.md; factory completion law.

Resolved: sources `~/MEMORY.md` standing-law entries dated Nov 9–10,
2026; `~/AGENTS.md`; this repo's AGENTS.md commit and verification
doctrine.
