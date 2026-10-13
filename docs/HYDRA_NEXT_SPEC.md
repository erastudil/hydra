# hydra next — master specification draft v0.1

status : Draft — grill interview in progress; decisions recorded per question in section 12; zero implementation authorized until interview closes and scope contract accepted explicitly.

scope : one harness orchestrating every inference endpoint, CLI agent, and compute surface under one pattern; summon, work, output, disappear; zero named mascot agents, zero persistent personalities.

## 1. architecture baseline

core principle : single Rust workspace; one core library; three frontends sharing identical capability surface at different weights.

crates : `hydra-core` routing, providers, catalog, fallback, health; `hydra-agent` tool loop, VM, browser, computer use, MCP; `hydra-secrets` vault and ingestion; `hydra-cli` shell utility; `hydra-tui` terminal wrapper; `hydra-desktop` native app.

legacy state : Python v1.2.2 at `hydra_cli/` fully working; zero Rust files present today; desktop Python module sprawl under `desktop/` requires consolidation; TUI in `hydra_cli/tui.py` and `repl.py` carries visuals worth preserving.

migration rule : every Python behavior carries a Rust parity test against real endpoints before Python counterpart retires; `python scripts/verify.py` exit 0 gate preserved throughout.

## 2. shell utility baseline

contract : `hydra <alias> "<prompt>"` direct summon; pipe protocol `cat file | hydra <alias> - "instruction"`; `--json`, `--no-stream`, `--effort`, `--max-tokens`, `--system`, `--tier` flags retained verbatim for script compatibility.

commands retained : `free`, `hf`, `local`, `swarm`, `agent`, `serve`, `desktop`, `mcp`, `auth`, `stdio`, `hands`, `sandbox`.

exit contract : exit 0 success; nonzero with structured error on stderr; `--json` emits model, provider, tokens, cost, latency on every call.

gateway : `hydra serve` OpenAI-compatible endpoint on 127.0.0.1:7777 routing any alias; `/v1/models`, `/v1/health`, `/v1/chat/completions` mandatory.

## 3. default endpoint policy

default alias : `free-smart` resolving dynamically to highest-ranked free model currently healthy.

candidate pool : OpenRouter `:free` catalog, Cloudflare Workers AI free neurons, Hugging Face free inference, plus local engines when running.

ranking inputs : published benchmark ordering, live health, rolling latency, rolling success rate, context window size.

fallback cascade : free pool in rank order, then local engine, then cheapest paid only with explicit user opt-in flag; zero silent paid spend by default.

override : `--model`, alias argument, per-role genome pin, and `HYDRA_FREE_MODEL` env each override default resolution in that precedence order.

## 4. secrets subsystem

problem statement : manual `.env` editing across 11 keys caused repeated friction; ingestion must become the primary flow.

vault : OS credential store primary — Windows Credential Manager, macOS Keychain, Linux Secret Service; encrypted file fallback with Argon2id key derivation when OS store unavailable.

ingestion surfaces : TUI masked prompt with paste detection; desktop form with per-provider fields, show/hide toggle, test-connection button per row; CLI `hydra auth` wizard retained.

validation : every saved key probed live against provider before commit; failed probe keeps old value and reports verbatim error.

safety : secrets never enter logs, transcripts, swarm prompts, or git; masked display first 4 plus last 4 only; per-key delete and rotate actions; export encrypted backup with user passphrase.

migration : existing `~/.hydra/.env` imported once, verified per key, then vault becomes source of truth; `.env` regenerated as runtime projection for child processes.

## 5. TUI

role : lightweight full-capability wrapper over `hydra-agent`; same tools, same genomes, same vault as desktop.

preserved visuals : three-head banner art, streaming token render, swarm fan-out view from legacy `tui.py`.

glowing prompt bar : mandatory preservation; glowing prompt bar effect operator-liked across Python builds; Rust TUI reproduces effect faithfully before any redesign.

ascii art : current TUI Hydra art — best Opus-generated variant — preserved as baseline; stretch goal detailed ASCII conversion from classical Lernaean Hydra painting; prior Opus attempts missed target; art upgrade never blocks functional milestones.

fixes mandated : context manager with visible token budget meter, automatic compaction with user-visible summary, zero silent truncation; output pager with full scrollback, copy, and save-to-file; tool calls rendered as compact one-line entries expandable on demand.

controls : model and effort switchers in status bar; workspace directory indicator with quick-switch; running-agent list with kill keys; `/usage`, `/auth`, `/genome`, `/agents` slash commands.

daily driver mandate : Hydra TUI replaces Muse session, Cursor, Grok Build, and every prior TUI as primary operator surface; design bar equals best feature of each replaced tool with zero missing daily functions.

external agent substrate : TUI dispatches external CLI agents — Muse, Grok, Antigravity `agy`, Cursor Agent — as headless subagent workers under genome contracts; worker authenticates with operator credentials, executes autonomously, reports completion, output, and usage back to Hydra; Hydra remains orchestrator of record.

steering : three-channel model; single Enter queues instruction for next turn boundary; double Enter pushes steer — new instruction inserted at next safe point with prior instruction preserved, never discarded; Esc interrupts current turn and returns control; prompt bar never locks during streaming; queued items visible and editable before firing.

dictation : Ctrl+Space toggles voice dictation per Grok Build pattern; Enter sends dictated text; dictation available in TUI prompt and desktop chat input.

orchestration bar : instruction-following and orchestration quality benchmark equals Antigravity standard; every harness evaluated against that bar in keep-list review.

## 6. desktop app

role : full visibility surface; every TUI function plus computer use, VM monitoring, credit management, and advanced settings.

main screens : chat with agent timeline; agents dashboard; genomes editor; secrets vault; credits and top-up; VM and browser monitor; settings; logs.

agents dashboard : every running summon listed with role, model, provider, elapsed time, tokens, cost, state; kill, pause, steer, and inspect-transcript actions; ephemeral by design — completed agents collapse to one-line ledger entries.

workspace : directory picker in header; per-project recent list; all agent file tools scoped to selected workspace with escape attempts blocked and logged.

model bar : current model, effort, tier, and estimated cost pinned visibly on chat screen; one-click switch without opening settings.

## 7. execution substrates

shell : native tool execution with approval modes — always-ask, accept-edits, yolo — mirroring professional agent CLIs.

vm execution : sandboxed VM or container per agent session for untrusted code; snapshot, rollback, and destroy on completion; resource caps CPU, RAM, disk, wall-clock per genome.

browser : Playwright-driven Chromium with visible and headless modes; desktop shows live viewport screencast during computer use.

computer use : screenshot, click, type, scroll, key actions through Playwright plus OS-level fallback; every action logged with before/after screenshots in transcript; quality benchmark equals Codex and Astra computer-use standard; open models via Playwright accepted as working path.

agent environment : every summoned agent receives own VM or container, own browser instance, own terminal per Grok bot environment pattern; environments created on summon and destroyed on completion.

desktop swarm observability : graphical dashboard for large swarms; drag-and-drop multimodal input — files, images, audio dropped onto chat or agent; live per-agent state across fleet.

harness research : public Reverse Engineer Anything repository integrated into toolkit as reference for studying large harnesses including Codex; extracted functions reimplemented as real Rust code; prompt-soup patterns rejected — behavior lives in code, prompts carry instructions only.

mcp : full MCP client parity with registry in `~/.hydra/mcp_servers.json`; per-server enable toggles in desktop settings.

### context, session notes, and ledger

context threshold : zero compaction while working set remains at or below 50% of current model context window.

pinned context : AGENTS.md, active rules, active genome, tool contracts, and live steering state remain verbatim on every turn; pinned context never summarized.

turn-start mini compact : at beginning of each turn, if working set exceeds 50%, summarize older unpinned turns until projected turn start returns below 50% when reasonable; recent turns remain verbatim.

verbatim note cache : pruned turns written token-for-token to session notes on disk with stable note IDs, timestamps, turn ranges, model identity, and action references; verbatim notes remain retrievable for 24 hours after pruning.

breadcrumb retrieval : current context carries summaries plus note breadcrumbs; agent may retrieve exact notes by ID or search note text and rehydrate selected verbatim turns into working set without altering ledger history.

retention after 24 hours : unpromoted verbatim notes expire; surviving main-ledger record contains summary plus structured file and API action record; opening a new session clears stale expired material from active indexes.

ledger write authority : main agent writes main ledger; subagents write branch notes only; controller review promotes durable material to main ledger and discards branch residue under retention rules.

smart ledger writes : harness records file, API, tool, VM, browser, and computer-use actions as structured execution events; model prose never constitutes sole audit record.

## 8. genomes — role instruction system

definition : genome equals versioned system-prompt document plus tool allowlist, model pin or tier, effort, budget cap, and approval mode.

format : progen-instruct markdown under `~/.hydra/genomes/` and per-project `.hydra/genomes/` override.

dispatch : `hydra agent --genome auditor "task"`; swarm heads each bound to one genome; desktop editor with diff, history, and restore.

builtin roster v1 : orchestrator, controller, synthesizer, architect, coder, auditor, researcher, operator, companion, confidant, marketer, writer, critic.

companion genome : casual conversation; friendly and helpful; free-smart tier, low effort, tools off by default; token-frugal on light conversation.

confidant genome : serious conversation — personal life, money, consequential decisions; frontier-capable; talk-focused with building tools off by default; research tools on request.

marketer genome : positioning, copy, launch and campaign work; writer genome : long-form prose, editing, documentation voice.

critic genome : institutionalized heresy per AODI pattern; sole mandate finding everything wrong with plans, specs, code, and decisions; no building tools; output structured as findings ranked by severity.

lifecycle : summoned agent receives genome as system prompt plus controller-assembled memory pack, executes, emits output, terminates; genome declares ledger read/write tier; subagents write branch notes only and never write main ledger directly.

nexus evolution hook : future system evolves genome prompts against scored task outcomes; v1 ships genome versioning and outcome scoring fields only, evolution engine deferred.

## 9. credits and top-up screen

per-provider card : balance or usage remaining, burn rate, reset time, status color; data from live provider APIs cached 60 seconds.

providers wired : OpenRouter key limit and credits, RunPod balance and spend/hr, Cloudflare neuron usage, Hugging Face billing, Vercel gateway, CheaperInference, Cursor period usage, xAI, Anthropic, OpenAI, Google.

top-up action : embedded checkout where provider API permits; otherwise one-click browser launch to provider billing page with return detection.

alerts : threshold warnings at 25% and 10% remaining; desktop notification plus TUI status bar flag.

## 10. advanced settings suite

categories : providers and keys; model catalog and aliases; free-pool ranking weights; fallback cascade order; context and compaction thresholds; tool approvals and sandbox; VM resource caps; browser and computer-use permissions; MCP servers; genomes defaults; swarm composition; telemetry off by default; logging verbosity; gateway port and auth; update channel.

form : searchable settings tree; every setting shows default, current value, and source — default, vault, env, project, session.

## 11. obvious wiring backlog — pre-build

modal : `MODAL_ENDPOINT_URL` plus key into vault; profile `erastudil` already authenticated locally.

runpod : bonsai-vllm worker failure diagnosis — 159 failed against 10 completed; logs before any new dispatch.

gateway : `hydra serve` autostart option with health probe on TUI and desktop launch.

local engines : Ollama and EasyLM auto-detection with start/stop controls.

free default : replace static `google/gemma-4-26b-a4b-it:free` default in `catalog.json` with dynamic `free-smart` resolution.

branch hygiene : 8 local feature branches require merge-or-archive decision before Rust workspace lands.

## 12. open questions queue

q1 : DECIDED — big-bang rewrite; Python v1.2.2 feature-frozen; full Rust workspace built in isolation; single cutover after real-endpoint parity suite passes exit 0.

q2 : DECIDED — hybrid ranking; static benchmark-seeded table in catalog; live health, latency, and failure-rate circuit breaker demotes candidates at runtime; zero probe spend.

q3 : DECIDED — tiered per genome; genome declares container, local VM, or Modal/RunPod cloud sandbox; local container default; cloud only on genome demand for GPU or scale.

q4 : DECIDED — egui pure Rust; zero web dependencies across the stack; explicit design budget to reach modern visual standard; visual spike review gate after first dashboard build with framework re-evaluation reserved.

q5 : DECIDED — split defaults; VM/container full yolo; trusted workspace accepts everything except irreversible destructive commands; outside workspace always ask; browser and computer use ask by default; user may enable full yolo everywhere including computer use — operator machine, operator authority.

q6 : DECIDED — dedicated synthesizer genome merges head outputs, resolves conflicts with reasoning, preserves dissent in transcript only.

q7 : DECIDED — hybrid ledger; one canonical hash-chained ledger; genome declares read/write tier; subagents write to branches only; dedicated controller genome curates promotion to permanent record and assembles per-agent memory packs.

q8 : DECIDED — hybrid top-up; live balance per provider card; embedded checkout where provider billing API permits; Brave billing-page launch elsewhere; payment details never stored in Hydra.

q9 : DECIDED — localhost default; LAN exposure opt-in behind generated bearer token stored in vault; token rotation in secrets screen.

q10 : DECIDED — Alice separated from Hydra entirely until independently ready; zero Alice modules in Rust port scope; legacy Python codebase archived to Google Drive `G:\My Drive\HNAI-Backups` at cutover and removed from working tree; git history retained.

q11 : DECIDED — experience-based cutover; zero perfect-parity requirement; functions the operator liked preserved inside unified Rust architecture; Python archived when operator declares Rust version good enough that old build no longer gets opened; operator declaration sole gate.

q12 : DECIDED — keep-list includes aspirational capabilities unused in Python build; shell utility strongest proven surface; full-agent adaptation hardest surface with real progress retained; keep-list elicitation via operator-named likes plus spec scope; evidence mining optional annotation only.

q13 : DECIDED — shell-first staging; milestone 1 core plus secrets plus Rust shell; milestone 2 TUI daily driver; milestone 3 agent loop; milestone 4 desktop with `~/.hydra` state migration as entry task.

q14 : DECIDED — three-channel steering with operator stipulations; single Enter queues; double Enter steers at next safe point preserving prior instruction; Esc interrupts; Ctrl+Space dictation with Enter to send.

q15 : DECIDED — computer use DOM-first with vision fallback; Playwright accessibility tree and selectors primary; screenshot plus vision grounding on DOM failure or canvas/native targets; fallback vision model may itself be free-tier summon.

q16 : DECIDED — automatic breadcrumb context; zero compaction up to 50% of current model window; rules and genomes pinned verbatim; turn-start mini-compact summarizes older turns and keeps recent turns verbatim; pruned verbatim turns cached token-for-token in session notes for 24 hours; after expiry summary plus structured file/API action record survives; new session clears stale expired material; main agent writes main ledger, subagents branch only, controller promotes durable notes.

q17 : DECIDED by operator delegation — hybrid ledger promotion; harness records structured action events immediately as facts; controller genome reviews narrative branch notes at subagent completion, session close, new-session stale sweep, and on demand; operator pin and veto override controller everywhere.

q18 : DECIDED — session model summarizes own context at mini-compact; controller genome runs free-smart by default for ledger curation with escalation on conflicting notes.

q19 : DECIDED — outgoing-model handoff compact on mid-session model switch; outgoing model summarizes older context below 50% of incoming model window; pinned rules, genomes, and steering state carry verbatim; switch blocked with explanation when pinned material alone exceeds incoming safe budget; breadcrumb system serves this handoff role; dynamic context threading with per-turn whole-context indexing rejected — tried in Python build, never satisfactory.

q20 : DECIDED — thirteen builtin genomes: orchestrator, controller, synthesizer, architect, coder, auditor, researcher, operator, companion, confidant, marketer, writer, critic; per-project overrides under `.hydra/genomes/`; new roles clone nearest builtin.

q21 : DECIDED — critic fires at deploy/release gate only; cross-family adversarial review mandatory before release into the wild; reviewer has no stake in outcome — different provider family than producer; release requires passing critic scrutiny or explicit operator override recorded in ledger.
