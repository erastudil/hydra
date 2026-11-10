# Hydra — Queue

## Queue rules

Every item carries an outcome, acceptance criteria, and a proving check; state changes are recorded in the run that makes them. Work lands through the repo gate: branch, PR, merge to main, tree clean. Grading independence is absolute under A07: the agent that executes a cut never grades it.

Shared baseline for every cut below, measured on main before the cut and re-measured after: `scripts/verify.py` at 185 pass / 3 skip / 6 fail and pytest at 254 passed / 5 failed, with the failing sets identical by name to the pre-cut baseline. Any new failure is a regression and blocks the merge. Evidence for every cut's deadness or duplication claim: the factory's independent spot-verification, `erastudil/factory` at `subtraction/hydra-CUT-LIST-VERIFICATION-2026-11-09.md`. Thirteen of the sixteen Tranche 1 items passed; H-02, H-05, and H-14 failed as scoped and are not in this queue.

## Ready

### WO-HYDRA-C01 — Forum lane repointed off the VM clone, then the clone retired — cut list H-01

- Outcome: no fleet automation executes from `~/workspace/hydra`; the forum lane runs `bin/hydra free` from the canonical laptop checkout or the GitHub remote, and the stale VM clone is retired. The clone sits on the dead branch `feat/groq-summon-wiring` at ebe8a6b, 351 commits behind main, with two working-tree deletions.
- Home: fleet job and goal files under `~/workspace` for the repoint; `~/workspace/hydra` itself for the retirement.
- Acceptance criteria:
  - The repoint lands first or in the same change as the retirement: at no point does the forum lane point at a removed tree. A repo-wide search of `~/workspace` job and goal files for the path `~/workspace/hydra` returns only historical record references, each named as such.
  - The forum lane completes one live cycle from its new home with output indistinguishable in kind from the pre-repoint cycle: posts filed, watermark advanced.
  - The clone is retired to a dated Drive pool with a manifest and a verified cloud copy before the local tree is removed; no total deletions. Its only unique content, the two uncommitted deletions GLM_5.3_REVIEW.md and docs/TRIUMVIRATE_ARCHITECTURAL_ASSESSMENT.md, is named in the manifest.
- Proving check: the path search above; one observed forum cycle; a Drive API listing matching the pool manifest.
- Dependencies: None. This order goes first because the verification corrected H-01's claim that nothing runs from this tree — the forum lane does.
- Evidence on completion: this queue's Done entry plus the pool manifest path.

### WO-HYDRA-C02 — agent_runners.py renamed to external_runners.py at all five reference sites — cut list H-16

- Outcome: the one-name-apart collision is gone: `agent_runner.py`, the scheduler, stays, and the external-agent summoner carries a distinct name with every reference updated.
- Home: `hydra_cli/` plus the repo's install manifests.
- Acceptance criteria:
  - `agent_runners.py` no longer exists; `external_runners.py` carries its content, 212 lines at verification.
  - All references updated, per the verification's corrected count: three code importers — `hydra_cli/__init__.py`, `hydra_cli/swarm.py`, `scripts/verify.py` — and both install manifests, `install.ps1` and `install.sh`. The cut list's two-import-sites figure was wrong.
  - Baseline gates hold at 185/3/6 and 254/5 with identical failing sets; manifest-drift verification passes.
- Proving check: repo-wide grep for `agent_runners` returns zero live references; verify.py and pytest at baseline.
- Dependencies: None. Runs early so later cuts grep against settled names.
- Evidence on completion: Done entry with the merge commit.

### WO-HYDRA-C03 — queue/ retired kaizen machinery removed — cut list H-04

- Outcome: the `queue/` directory is out of the source repo: kaizen_runner.py, generate_kaizen_backlog.py, kaizen_state.json, and kaizen_queue.jsonl, about 660 lines plus 2.1 MB of committed runtime state.
- Home: `queue/` at the repo root.
- Acceptance criteria:
  - `queue/` absent from the tree; repo-wide search finds zero code importers. The only external mentions at verification were synthetic fixture strings inside a verify.py size-guard check, which construct the path as test data and never read the files.
  - No heartbeat or job file references the repo `queue/` paths.
  - The two state files, kaizen_state.json and kaizen_queue.jsonl, are retired to a dated Drive pool with a manifest before removal; runtime state is not source, and there are no total deletions.
  - Baseline gates hold.
- Proving check: the searches above; verify.py and pytest at baseline; the pool manifest.
- Dependencies: None.
- Evidence on completion: Done entry with the merge commit and pool path.

### WO-HYDRA-C04 — Three tokenizer copies consolidated to one — cut list H-09

- Outcome: exactly one tokenizer implementation serves the fingerprint and normalizer subsystems; the byte-identical regex copies at providers.py:1427 and providers.py:2544 and the divergent variant at speculative.py:98 are gone.
- Home: `hydra_cli/providers.py` and `hydra_cli/speculative.py`.
- Acceptance criteria:
  - The tokenizer pattern exists in exactly one place in the package; all three former call sites use it.
  - The surviving tokenizer's outputs diff clean against all three old copies over the repo's prompt fixtures. The speculative.py variant's different whitespace handling is resolved deliberately, and the resolution is stated in the PR.
  - verify.py's fingerprint contracts pass; baseline gates hold.
- Proving check: the diff run over fixtures; the grep count; verify.py and pytest at baseline.
- Dependencies: None, but this order lands before WO-HYDRA-C06: the two providers.py copies serve the subsystems that C06 moves, so consolidation comes first.
- Evidence on completion: Done entry with the fixture-diff result.

### WO-HYDRA-C05 — Commodity AST linters in native_tools.py replaced by ruff; bespoke trio kept — cut list H-06

- Outcome: the roughly fourteen hand-rolled commodity AST analyzers occupying native_tools.py lines 85–2417 at verification are replaced by a ruff configuration reproducing the commodity checks; the project-doctrine trio stays: AstWuweiAnalyzer, AstStateVectorLinter, AstP014MetaphorDetector, about 450 lines.
- Home: `hydra_cli/native_tools.py` plus one ruff config at the repo root.
- Acceptance criteria:
  - The commodity analyzer classes are absent; the three named bespoke analyzers remain and still run.
  - The ruff config runs over hydra_cli, and its flagged-file set on a sample matches the hand-rolled tools' output from before the cut; the sample comparison is recorded in the PR.
  - Size correction carried from the verification: native_tools.py measures 6,187 lines on current main, not the cut list's 5,585; the replaceable span is about 1,750 lines.
  - NativeToolRegistry contracts in verify.py pass; baseline gates hold.
- Proving check: the sample comparison; the class-absence grep; verify.py and pytest at baseline.
- Dependencies: None.
- Evidence on completion: Done entry with the sample-comparison artifact.

### WO-HYDRA-C06 — providers.py swept: unreachable factories out, non-provider subsystems moved out, HF getter pair kept — cut list H-08

- Outcome: providers.py holds providers only. The file measures 5,680 lines on current main, not the cut list's 4,727. Factories with no reaching route and no test reference are removed after a per-factory reachability audit, and TimestampSanitizer, PrefixNormalizer, PrefixIsolationManager, and KvCacheFingerprinter move to their own module, `cache_prefix.py`.
- Home: `hydra_cli/providers.py` and the new `hydra_cli/cache_prefix.py`.
- Acceptance criteria:
  - Exclusion, per the verification: the duplicate HF getters `get_huggingface_provider` and `get_hf_provider` are router-live at router.py:44–45, 206, and 1199, and are NOT swept as dead. Any deduplication of that pair is a separate, deliberate change with its own router evidence, not part of this sweep.
  - Every removed factory carries a per-factory reachability audit in the PR, with catalog, modes, router, tests, and GUIDE searched. The long tail's only external references at verification were verify.py's provider-gate assertions, which this cut updates to the surviving roster in the same change.
  - The four named subsystems exist in cache_prefix.py and nowhere in providers.py; their callers import from the new module.
  - Scripted summons per mode — local, free, and light — pass before and after; baseline gates hold.
- Proving check: the per-factory audit table; the subsystem location grep; the per-mode summons; verify.py and pytest at baseline.
- Dependencies: WO-HYDRA-C04, tokenizer consolidation first.
- Evidence on completion: Done entry with the audit table and summon results.

### WO-HYDRA-C07 — One HTTP/SSE transport for hydra_cli — cut list H-07

- Outcome: request, streaming, retry-with-backoff, and redaction live in one internal transport module. The hand-rolled per-file transports across the eleven verified modules — agent, alice_retrieve, auth, dictation, endpoints_report, local_server, media, memory, preflight, providers, serve — call it instead of their own urlopen plumbing, about fifty retry and backoff sites at verification.
- Home: `hydra_cli/`, the new transport module plus the eleven call sites.
- Acceptance criteria:
  - No hydra_cli module outside the transport module opens its own urlopen transport or implements its own retry and backoff loop.
  - The work order picks stdlib-underneath or httpx and states the pick and its reason in the PR; the cut is the duplication, not the stdlib.
  - Provider gate tests pass; one scripted summon per mode — local, free, and light — runs before and after, with streamed output diffed on a fixed prompt.
  - Baseline gates hold.
- Proving check: the transport-site grep; the per-mode streamed-output diffs; verify.py and pytest at baseline.
- Dependencies: WO-HYDRA-C06, the providers.py sweep first, so the transport consolidation operates on the swept file and not the god-file.
- Evidence on completion: Done entry with the diff artifacts.

### WO-HYDRA-C08 — MODEL_PRICING hand-maintained tables removed from desktop_core.py — cut list H-12

- Outcome: cost displays price only where a system of record provides a price, from provider-reported usage at the endpoints the router already calls, and display no price where none exists, instead of the hand-maintained tables' remembered figures.
- Home: `hydra_cli/desktop_core.py`.
- Acceptance criteria:
  - Both hard-coded rate tables are gone: the MODEL_PRICING table at line 17 and the second table at about line 227, which the verification found; the rot is slightly worse than the cut list claimed.
  - Cost displays on a recorded session fixture diff against the pre-cut displays, with every changed figure traced to no-sourced-price in the PR, in those words; displays losing unsourced numbers is the point, not a regression.
  - verify.py's token-accounting contract family, desktop_resilience_and_token_accounting_contracts, passes; baseline gates hold.
- Proving check: the table-absence grep; the fixture diff with per-figure tracing; verify.py and pytest at baseline.
- Dependencies: None. WO-HYDRA-C02 has already settled the agent_runner imports this file uses.
- Evidence on completion: Done entry with the fixture diff.

### WO-HYDRA-C09 — mcp.py hand-rolled protocol core replaced by the official mcp SDK — cut list H-10

- Outcome: mcp.py, 3,345 lines, shrinks to Hydra's registry and policy surface over the official `mcp` Python SDK. The hand-rolled JSON-RPC framing with its literals at lines 2568 and 2604 at verification, plus the stdio transport, correlation, and retries, becomes the SDK's work.
- Home: `hydra_cli/mcp.py`. mcp_registry.py is kept untouched as Hydra's own layer.
- Acceptance criteria:
  - No hand-rolled JSON-RPC framing remains in mcp.py; the SDK is the declared dependency carrying transport and protocol.
  - A scripted session against a fixture MCP server lists and calls a tool, before and after, with equivalent results.
  - mcp_registry tests in the pytest suite pass; baseline gates hold.
- Proving check: the fixture session transcript; the framing-literal grep; verify.py and pytest at baseline.
- Dependencies: None.
- Evidence on completion: Done entry with the session transcript.

### WO-HYDRA-C10 — browser.py hand-rolled page machinery replaced by Playwright's own facilities — cut list H-13

- Outcome: browser.py keeps its Playwright base and drops the hand-rolled surroundings. The file measures 4,316 lines on current main, not the cut list's 3,763. The bespoke DOM_IDLE_SCRIPT idle detection, the html.parser extraction with its four HTMLParser subclasses, and the hand-rolled binary image handling over struct, zlib, and hashlib give way to Playwright's wait_for_load_state and networkidle semantics, locator-based extraction, and screenshot encoding.
- Home: `hydra_cli/browser.py`.
- Acceptance criteria:
  - DOM_IDLE_SCRIPT and the four HTMLParser subclasses are absent; idle waits call Playwright's own facilities.
  - The browser-leg fixtures, the warehouse manifest batch from the Oct 27 Playwright work, run before and after with extracted content compared per page; every difference is named and accepted in the PR. Idle-detection semantics differ subtly, and the fixture comparison is the containment.
  - Baseline gates hold.
- Proving check: the fixture comparison; the symbol-absence grep; verify.py and pytest at baseline.
- Dependencies: None.
- Evidence on completion: Done entry with the fixture-comparison artifact.

### WO-HYDRA-C11 — Alice-on-GCP paths removed — cut list H-03

- Outcome: alice_gcloud.py at 387 lines and alice_cloud_loop.py at 378 lines are deleted, the gcloud branch of alice_chat.py is removed, and the alice_cloud_loop route is out of router.py.
- Home: `hydra_cli/alice_gcloud.py`, `hydra_cli/alice_cloud_loop.py`, `hydra_cli/alice_chat.py`, `hydra_cli/router.py`.
- Acceptance criteria:
  - Both files absent; no live module imports them; router.py carries no alice_cloud_loop route. The route sat at router.py:364–366 at grading; line numbers will have drifted.
  - The living Alice legs are untouched: hands.py still imports alice_interpret, alice_knowledge, and alice_senses; the `alice` route still dispatches to alice_runner.py; the live `alice-chat` route still answers, with only its gcloud import removed.
  - The `hydra alice` stdio smoke passes before and after; the hands and Alice tool tests pass.
  - The PR states plainly that a route disappears and that it pointed at a backend retired by the owner ruling of Nov 4, 2026: a behavior change against a dead backend, named as such.
  - Baseline gates hold.
- Proving check: the import and route greps; the alice smoke; verify.py and pytest at baseline.
- Dependencies: None.
- Evidence on completion: Done entry with the smoke result.

### WO-HYDRA-C12 — Python TUI rendering layer retired toward hydra-tui-rs — cut list H-11

- Outcome: the Python rendering layer is retired: tui.py at 1,532 lines plus the rendering parts of ui.py and repl.py. The Python chat path speaks through the same stdio adapter the Rust shells use, keeping repl's command logic. Reachability is real today — router.py routes chat through repl.run_repl and imports ui for banner and color, and agent.py lazily imports HydraTUI — so this is a duplicate-surface simplification, not a dead-code cut.
- Home: `hydra_cli/tui.py`, `hydra_cli/ui.py`, `hydra_cli/repl.py`, `hydra_cli/router.py`, `hydra_cli/agent.py`.
- Acceptance criteria:
  - HydraTUI and the Python rendering layer are absent; no module imports hydra_cli.tui.
  - Scripted terminal sessions in the WO-3 and WO-4 harness shape — timed frames, pulse phase, resize — run against the stdio path and pass.
  - The PR states the honest cost in plain words: the fallback no-frills terminal experience changes. No byte-invariance is claimed.
  - Baseline gates hold.
- Proving check: the scripted terminal sessions; the import grep; verify.py and pytest at baseline.
- Dependencies: None, but ranked last among the code cuts on risk: medium-high at verification.
- Evidence on completion: Done entry with the session results.

### WO-HYDRA-C13 — speculative.py evidence audit — cut list H-15, evidence-owed

- Outcome: the missing leg exists: a reachability-and-behavior audit establishing what speculative.py actually drives in the current modes that llama.cpp's native speculative decoding cannot. The file is 463 lines, exported via hydra_cli/__init__.py and driven from router.py's execute_speculative_mode behind the `--speculative` flag. No code is removed by this work order.
- Home: the audit document, filed with this queue's evidence in the repo docs or the factory subtraction folder, and named in the Done entry.
- Acceptance criteria:
  - The audit names, per current mode, whether the engine runs, what it orchestrates, and whether that orchestration is cross-provider and genuinely Hydra's or single-server and llama.cpp's native ground, with file and line evidence for each claim.
  - The audit closes with a graded recommendation — keep, replace, or remove — that a follow-up work order can execute cold.
  - Baseline gates untouched; this order changes no code.
- Proving check: an independent reader can re-run the audit's greps and trace its claims to the cited lines.
- Dependencies: None.
- Evidence on completion: the audit document itself, graded by an agent other than its author.

## In progress

## Blocked

## Done
