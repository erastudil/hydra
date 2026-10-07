---
title: "Hydra Triumvirate Architectural Assessment"
summary: "Formal multi-model peer audit of Hydra autonomous agent core, native tool registry, credential wizard, and routing engine across Claude Opus 5.5, GPT-6.1 Sol, and GLM 5.3 Flash."
version: "1.2.1"
layer: assessment
home: docs/TRIUMVIRATE_ARCHITECTURAL_ASSESSMENT.md
dialect: progen instruct
status: ratified
---

# Hydra Triumvirate Architectural Assessment

scope : formal architectural audit and cross-head synthesis of recent Hydra CLI developments.

models summoned :
- Head 1 : Claude Opus 5.5 (anthropic/claude-opus-5.5) ? Architectural & Invariants Auditor.
- Head 2 : GPT-6.1 Sol (openai/gpt-6.1-sol) ? Formal Reasoning & Security Auditor.
- Head 3 : GLM 5.3 Flash (glm-5.3-flash) ? Performance & Developer Ergonomics Auditor.

targets evaluated :
- hydra_cli/agent.py : autonomous ReAct loop, SessionCheckpointer cyclic statechart, HierarchicalScratchpad bounded working memory, 128k context pruning, interactive REPL.
- hydra_cli/native_tools.py : zero-dependency native tool registry (read_file, write_file, edit_file, list_dir, grep_search, find_files, run_command, invoke_subagent, swarm_fanout).
- hydra_cli/auth.py : credential onboarding wizard, secret masking, atomic persistence, pre-flight provider probing.
- hydra_cli/tool_adapter.py : universal prompt-based tool calling adapter for schema-rejecting endpoints.
- hydra_cli/router.py & config.py : dynamic provider routing, ProviderHealthTracker circuit breaker, prompt cache optimization.


## 1. Head 1 Verbatim Audit : Claude Opus 5.5
role : Lead Systems Architect & Invariants Auditor.
focus : System topology, state invariants, failure modes, and boundaries.

```progen
architecture.topology : Single-process ReAct core with recursive subagent spawning, swarm fanout, and a shared router. Every head shares one filesystem, one credential store, and one health tracker with no declared ownership boundaries.

architecture.coupling : agent.py concentrates the loop, statechart, memory, context budgeting, REPL, and rules discovery in one module. Extract the statechart, ContextBudgeter, and Scratchpad behind interfaces so each invariant can be tested in isolation.

architecture.trust_model : No explicit trust tiers exist. System prompt, project rules, user input, tool output, and subagent output all enter the same message stream with equivalent authority.

statechart.transition_table : The states are declared but no legal transition relation is. Encode an explicit edge set, reject undeclared transitions at runtime, and assert the edge set in tests.

statechart.terminal_absorption : COMPLETED, MAX_TURNS, and ERROR must be absorbing. A resume from any of these states should require an explicit operator transition back to PLANNING.

statechart.error_partition : A single ERROR state conflates recoverable provider faults with unrecoverable invariant violations. Split it into ERROR_RETRYABLE and ERROR_FATAL so resume logic stays deterministic.

statechart.verify_cycle : VERIFYING to PLANNING forms the primary cycle. Termination depends entirely on the turn counter, so the counter must persist in the checkpoint and accumulate across resumes. Otherwise repeated resume grants unbounded turns.

statechart.replay_hazard : A crash between tool dispatch and the OBSERVING checkpoint causes a resume to re-execute side-effecting tools. Persist a write-ahead intent record containing tool name, args hash, and call id before dispatch, then reconcile on resume.

checkpointer.atomicity : Session JSON writes need the same temp file plus os.replace discipline as write_file, with an fsync on the file and its parent directory. A torn checkpoint otherwise bricks the session.

checkpointer.concurrency : Two REPLs or a parent and subagent writing the same session_id race on the file. Acquire an advisory lock per session or derive a unique session_id for each subagent.

checkpointer.schema : Checkpoints carry no version field. Add schema_version and refuse or migrate mismatched payloads.

checkpointer.confidentiality : Session files contain raw tool outputs, which may include env values, tokens, and file contents. Create ~/.hydra/sessions with mode 0o700 and the files with 0o600, matching the .env policy.

memory.hypotheses_bound : The packet bounds hypotheses only in context, at 4. The in-memory list also needs a hard cap and eviction rule, or the scratchpad grows without limit across long sessions.

memory.notes_bound : The 15 in memory and 5 in context ratio holds. Define eviction as FIFO with pinned notes exempt, and cap the pinned count so the exemption cannot defeat the bound.

memory.entity_graph : The 20-entity cap needs a defined eviction policy, preferably LRU by reference recency. Evicting a node must also purge its incident edges so no dangling references remain.

memory.bound_units : All bounds count items, but context cost scales with bytes. A single note or entity holding a large blob bypasses the budget, so add per-item character caps.

context.budget_floor : The retained set of system prompt, user prompt, scratchpad block, and five full recent turns can exceed the window by itself. Add a degradation ladder that shrinks the recent window from 5 toward 1, then hard-truncates the user prompt with an explicit marker.

context.unit_mismatch : Truncation triggers on characters above 4000, while the budget counts tokens. Use one tokenizer-estimated metric for both and recompute after every model or provider switch.

context.truncation_ordering : read_file caps at 100k bytes while context truncation cuts at 4000 characters. A 100k read survives intact until the budget breaches, then collapses abruptly. Apply the per-observation cap at ingestion time.

context.tool_pairing : Summarizing completed tool turns must keep each assistant tool_call message matched to its tool response by call id. Orphaned tool role messages trigger 400 rejections, and the adapter regex may misclassify those as tool-unsupported errors.

context.cache_prefix : Project rules and tool schemas belong inside the immutable prefix and must serialize byte-stable, with sorted keys and fixed ordering. The scratchpad block must sit after the cache breakpoint, or every turn busts the cache.

context.summarizer : If summarization calls a model, it needs its own budget and a deterministic fallback. Recursive summarization failure must never block the main loop.

tools.path_confinement : No workspace jail appears for read_file, write_file, edit_file, list_dir, grep_search, or find_files. Resolve every path with realpath against a fixed workspace root and reject escapes through symlinks, ".." segments, and absolute paths.

tools.write_atomicity : The temp file must live in the destination directory, or os.replace crosses filesystems and fails. Also fsync before rename, preserve the original file mode, and unlink the temp file on any exception path.

tools.write_symlink : os.replace onto a symlink replaces the link itself and leaves the target untouched. Choose the semantics explicitly and confine the resolved target to the workspace.

tools.edit_uniqueness : The count == 1 rule holds. Add rejection of empty search strings, CRLF normalization parity between search and file content, and a stale-read guard that compares the mtime or hash recorded at the last read_file.

tools.read_encoding : Capping at a byte offset can split multibyte UTF-8 sequences. Decode with replacement, detect binary files via a null-byte probe, and return a typed binary marker for them.

tools.grep_redos : Model-supplied regex enables catastrophic backtracking. Enforce a wall-clock timeout per file or use a linear-time engine, and default to literal mode.

tools.list_dir_loops : Directory traversal follows symlink cycles unless it tracks visited inode and device pairs. The depth cap bounds the damage but does not stop duplicated output.

tools.run_command_inspector : Deny-list inspection of command strings is bypassable through shell metacharacters, encoding, and interpreter indirection such as python -c and sh -c. Treat the inspector as advisory and the sandbox as the real boundary.

tools.run_command_env : Child processes must receive a scrubbed environment. Do not let credentials loaded from ~/.hydra/.env reach executed commands.

tools.run_command_lifecycle : Enforce a timeout, kill the entire process group on expiry, and cap stdout and stderr at capture time to prevent memory exhaustion before truncation runs.

subagent.depth_authority : The subagent_depth counter must derive from the parent execution context. Accepting it as a tool argument lets the model reset its own recursion bound.

subagent.breadth_explosion : The depth cap of 3 alone leaves fanout unbounded. A 4-head swarm at each level yields 4^3 = 64 leaf agents. Enforce a global ledger shared across the agent tree that caps total spawned agents, tokens, cost, and concurrent provider calls.

subagent.write_contention : Concurrent heads editing the same file race on the stale-read check. Enforce single-writer per path through a lock table, or confine subagents to proposal mode with a parent-applied merge.

subagent.output_trust : Subagent results re-enter the parent as observations. Tag them as untrusted data so parent planning never executes embedded instructions from child output.

repl.undo_scope : /undo can reverse only edits made through file tools. Side effects from run_command and subagent writes fall outside its scope unless every writer records into the same undo journal. State this boundary in /help output.

repl.state_coherence : /clear, /model, and /tier must atomically reset or recompute the scratchpad, context budget, adapter mode, and checkpoint together. Partial resets leave a stale budget against a new context profile.

rules.discovery_boundary : Parent-directory search can ingest an AGENTS.md or CLAUDE.md from home, /tmp, or a hostile checkout ancestor. Stop traversal at the VCS root and require first-use confirmation for any rules file outside the workspace.

rules.injection_surface : The rules files enter the immutable system prefix with full system authority. Hash them, display the path and hash at session start, and invalidate the cache on hash change.

auth.mask_threshold : The s[:4]...s[-4:] mask reveals 8 characters, which exposes most of a short secret. Require a minimum length, such as 20, before partial masking and fall back to fully masked output below it.

auth.file_creation_race : chmod after write leaves a window of world-readable exposure. Create the temp file via os.open with O_CREAT and O_EXCL at mode 0o600, create the parent directory at 0o700, then os.replace.

auth.env_injection : Credential values containing newlines or "=" corrupt .env parsing and allow key smuggling. Validate values against the provider format regex before persisting.

auth.probe_leakage : HTTP client exception messages can embed request URLs and occasionally headers. Catch errors at the transport boundary and emit only status class and provider name. Set explicit connect and read timeouts.

auth.windows_gap : The 0o600 mode has no effect on Windows. Apply an ACL restriction through the platform API or emit an explicit warning at save time.

adapter.misclassification : The 400 and 422 regex heuristics overlap with context-length, schema, and content-policy errors. A false positive silently downgrades to prompt-mode tools. Match provider-specific error codes and cache the capability decision per endpoint and model pair, with a TTL.

adapter.tag_injection : Tool observations formatted as user messages can carry literal <tool_call> text from file contents or command output. Escape the tag delimiters in every observation and parse calls only from assistant-authored output.

adapter.provenance_loss : Rendering observations as user-role messages grants tool output user-level authority. Wrap observations in a fixed delimiter envelope labeled as untrusted data, and state that rule in the cached system prefix.

adapter.parse_robustness : Malformed JSON, multiple calls, nested tags, and truncated closing tags each need defined handling. Return a structured parse-error observation and count it against the turn budget to prevent infinite reformat loops.

router.failover_coherence : A mid-session provider switch can change the tool-calling mode, context window, tokenizer, and cache prefix validity at once. Treat failover as a state transition that triggers a budget recompute and an adapter mode re-resolution.

router.breaker_semantics : The circuit breaker needs explicit closed, open, and half-open states with a single probe in half-open. Honor Retry-After headers ahead of computed backoff, and add jitter to the exponential schedule to prevent synchronized retry storms across swarm heads.

router.thread_safety : The swarm and subagents mutate the ProviderHealthTracker concurrently. Guard the counters with a lock or move them into a single-owner actor, otherwise breaker trips become nondeterministic.

router.data_boundary : Free-tier and third-party routes receive repository contents, including any secrets present in files. Add a sensitivity flag per session that restricts routing to frontier or local backends, and enforce it in the router before dispatch.

config.catalog_validation : catalog.json loads without a declared schema. Validate alias uniqueness, provider existence, and positive context limits at startup, and fail closed on violations.

boundary.workspace_jail : Priority one. Use a single realpath-confined root for all file tools, run_command cwd, subagents, and rules discovery.

boundary.global_ledger : Priority two. Use a tree-wide budget object passed by reference through invoke_subagent and swarm_fanout, enforcing agent count, token spend, cost, and concurrency.

boundary.intent_log : Priority three. Use a write-ahead tool intent journal tied to checkpoints, enabling idempotent resume and complete /undo coverage.

boundary.provenance_tiers : Priority four. Assign every message one of four tiers: system, operator, model, or untrusted-data. Apply tier-aware delimiting to tool output, subagent output, and rules files.

boundary.invariant_tests : Priority five. Property tests should assert, after every turn, the scratchpad item caps, tool-call pairing integrity, checkpoint round-trip equality, legal state transitions, and a context size at or below the budget.
```


## 2. Head 2 Verbatim Audit : GPT-6.1 Sol
role : Formal Reasoning & Security Auditor.
focus : Sandbox containment, token masking, concurrency, race conditions, and verification gates.

```progen
Audit basis : Architectural descriptions establish intended controls; implementation inspection and adversarial tests determine enforcement strength.

Sandbox containment — critical : SandboxRunner must enforce isolation independently of CommandInspector. Shell syntax, interpreters, subprocesses, executable substitution, and environment expansion defeat command-name filtering.

Sandbox boundary — critical : Apply workspace confinement, least-privilege execution, restricted network egress, process limits, timeouts, and CPU, memory, and output quotas. Prevent access to host credentials, agent state, container sockets, and privileged services.

Native tools — critical : Apply the same filesystem policy to read_file, write_file, edit_file, list_dir, grep_search, and find_files. Command sandboxing alone leaves direct filesystem operations outside its boundary.

Path resolution — high : Resolve access through trusted directory handles and platform-supported symlink-resistant operations. String-prefix checks and separate validation followed by opening permit traversal and symlink races.

Secret masking — high : Four-character prefix and suffix disclosure exposes credential fragments and can expose an entire short credential through overlapping slices. Prefer fixed redaction or a keyed fingerprint for credential identification.

Secret propagation — high : Redact credentials before logs, exceptions, tool observations, diffs, scratchpad updates, checkpoint persistence, and provider requests. Display masking leaves underlying storage and transmission unchanged.

Credential probes — high : Keep credentials in authorization headers, restrict probe destinations to approved HTTPS origins, and prevent authorization forwarding across redirects. Test error handling against responses that echo headers or submitted secrets.

Credential persistence — high : Create temporary credential files with mode 0600 from inception and protect the containing directory with mode 0700. Atomic replacement alone provides neither confidentiality nor crash durability; sync file contents and the parent directory where durability matters.

Checkpoint storage — high : Validate session identifiers, prevent path traversal, restrict directory and file permissions, and version the persisted schema. Prompts, observations, and scratchpad contents can contain credentials and proprietary source code.

Atomic writes — high : os.replace provides atomic visibility of replacement; concurrent writers still lose updates. Preserve intended ownership and permissions, constrain destination resolution, and clean temporary files safely after failures.

Edit races — high : Unique-match validation applies only to the snapshot read. Protect the read-modify-write transaction with a shared lock or version-checked commit, and reject edits when the file has changed.

Session races — high : Serialize checkpoint updates per session or use revisioned compare-and-swap storage. Enforce valid state transitions and prevent stale writers from overwriting newer turns or terminal states.

Execution recovery — high : Persisting EXECUTING creates a replay hazard when a command succeeds before its observation is saved. Journal operation identifiers and results; require explicit recovery decisions for non-idempotent actions.

Fanout concurrency — high : Assign isolated worktrees or explicit file ownership to parallel heads. Require coordinated, conflict-checked commits when multiple agents modify shared files.

Subagent depth — high : Enforce depth centrally before spawning, increment it exactly once, and propagate it across every invocation path. Define root depth explicitly and test whether depth 3 permits execution while prohibiting further descendants.

Aggregate limits — high : Recursion depth bounds nesting while fanout can still multiply agents exponentially. Share invocation, token, time, process, and provider-spend budgets across the entire agent tree.

Project rules — high : Treat discovered AGENTS.md, .cursorrules, and CLAUDE.md files as untrusted instructions. Bound ancestor discovery and prevent project content from changing sandbox policy, credential access, tool authorization, or provider routing.

Tool adapter — high : Parse tool calls with strict schemas, argument limits, and complete framing validation. Treat parsed calls as proposals subject to authorization, and keep tool observations distinct from trusted user instructions.

Fallback detection — medium : Restrict adapter fallback to verified provider capability failures. Broad error-text matching can misclassify failures and change tool semantics unexpectedly; preserve authorization and validation across both execution paths.

Context budgeting — high : Token limits constrain context size rather than data sensitivity. Preserve security policy during compaction, sanitize summaries, and retain explicit records of authorization decisions instead of relying on lossy working memory.

Provider routing — high : Attach data-classification and destination-allowlist constraints to every routing decision. Health failures and tier fallback must preserve those constraints so sensitive context remains within approved destinations.

Health tracker concurrency — medium : Synchronize circuit-breaker state, retry deadlines, and rate-limit reservations across concurrent requests. Use monotonic time, jittered backoff, bounded retries, and coordinated half-open probes to prevent retry storms.

Verification gates : Require tests for symlink escapes, credential canaries, temporary-file permissions, concurrent edits, stale checkpoints, crash recovery, recursive fanout, malicious tool tags, and unauthorized provider fallback before accepting these controls as enforced.
```


## 3. Head 3 Verbatim Audit : GLM 5.3 Flash
role : Performance & Developer Ergonomics Auditor.
focus : Context pruning efficiency, prompt caching, REPL usability, slash command ergonomics, and latency optimization.

```progen
context pruning efficiency : bounded scratchpad retains last 5 turns verbatim while compressing older tool output into working memory notes.
context pruning efficiency : 4000 character output truncation gives predictable message-size ceilings independent of tool verbosity.
context pruning efficiency : entity graph cap at 20 entities with last 6 surfaced keeps context payload stable across long sessions.
context pruning efficiency : hypotheses bounds at 4 prevent speculative accumulation from displacing observation data.
context pruning efficiency : summary generation for completed turns costs a model call; recommend deterministic extractive compression for sub-linear token retention.
context pruning efficiency : recommend benchmarking pruning at 4k, 8k, and 16k tool output scales and publishing measured token deltas against catalog.json context profiles.

prompt caching performance : immutable invariant prefix at token zero establishes a single cache block per provider.
prompt caching performance : tool prompt injection through tool_adapter mutates the system prompt and invalidates the cache block.
prompt caching performance : recommend hoisting tool schemas and inject_tool_prompt contracts above the mutable prefix or splitting two cache blocks.
prompt caching performance : repeated session loads from SessionCheckpointer restore full state with zero re-warm cost.
prompt caching performance : provider health shuffles rebind sessions to different providers where cache keys differ; recommend cache-aware sticky routing on ProviderHealthTracker.
prompt caching performance : recommend emitting cache_reader_tokens metrics per provider to rank frontier and free tier cache hit rates.

developer REPL usability : slash command set covers diff, tokens, undo, auth, clear, tier, model, help with terse naming.
developer REPL usability : unified diff visualization on edit_file matches standard VCS expectations and lowers review friction.
developer REPL usability : token visibility via /tokens exposes budget consumption live and supports pruning tuning.
developer REPL usability : /undo lacks described depth semantics; recommend stacking snapshots per write_file and edit_file calls.
developer REPL usability : recommend autocompletion for slash commands and for file paths fed to read_file and grep_search.
developer REPL usability : /clear should print reclaimed token count and confirm cache invalidation side effect.
developer REPL usability : project rules detection across parent directories matches editor convention and needs no setup.

slash command ergonomics : single-key cpi matches terminal coding loop conventions seen in industry REPLs.
slash command ergonomics : /tier and /model accept free-form provider strings; recommend tab-completion over catalog.json aliases.
slash command ergonomics : missing /retry and /skip commands force turn-level failure handling through MAX_TURNS and ERROR states.
slash command ergonomics : recommend /context dump command exposing the exact bounded message payload for debugging pruning anomalies.
slash command ergonomics : recommend /grep and /find passthroughs for zero-turn tool previews.
slash command ergonomics : auth status lives behind a wizard; a bare /auth status read path fits loop ergonomics without interactive prompts.

latency optimization : run_command sandboxing through SandboxRunner and CommandInspector adds per-turn inspection overhead; recommend precompiling the inspector allowlist at session start.
latency optimization : probe_credential performs HTTP round trips serially across three providers; recommend parallel fanout with per-provider timeouts.
latency optimization : invoke_subagent recursion depth 3 multiplies end-to-end turn latency with zero parallelism; recommend bounded concurrency for sibling subagents.
latency optimization : swarm_fanout across architect, coder, auditor, synthesizer reads as sequential head nesting; concurrent dispatch cuts wall clock substantially.
latency optimization : circuit breaker on ProviderHealthTracker avoids dead provider retry stalls; recommend adaptive timeout tiers per provider class.
latency optimization : routing to free tiers trades latency for cost and needs an SLA-weighted router mode.
latency optimization : recommend streaming tool observations into the OBSERVING state as generated instead of blocking on full turn completion.

cost and token efficiency : bounded working memory and native library tools drive most turns toward zero Python-level token overhead.
cost and token efficiency : prompt-based tool adapter inflates every request with JSON contracts; recommend caching the injected block or native tool preference on tool-capable endpoints.
cost and token efficiency : recommend per-session token ledger persisted to the session checkpointer so REPL token accounting survives reloads.

tool usability : NativeToolRegistry zero-dependency design removes schema round trips and cuts first-turn cold start cost.
tool usability : edit_file unique match enforcement catches silent wrong-spot rewrites and shortens recovery loops.
tool usability : atomic writes via temp file and os.replace eliminate partial edit states that break follow-up verification turns.
tool usability : read_file line slicing plus byte cap gives surgeons ergonomic long-file access in one tool call.
tool usability : grep_search and find_files capping prevents context blowouts from wildcard queries.
```


## 4. Triumvirate Consensus Findings

workspace jail P0 : require strict realpath resolution against workspace root for all native file tools; reject directory traversal escapes through relative dots, symlinks, and absolute paths.

edit concurrency P0 : edit_file validates uniqueness on initial read snapshot only; concurrent writes from subagents or external tools cause lost updates; require file hash or mtime verification at commit time.

secret masking threshold P0 : masking secrets with four-character prefix and suffix exposes eight characters total; exposes significant entropy of short keys (15-20 characters); require minimum 20-character secret length before partial masking, otherwise emit fixed-length redaction.

atomic persistence permissions P0 : credential persistence via chmod 0600 after creation leaves a brief race window; require creation via os.open with O_CREAT and O_EXCL at mode 0600 on POSIX; require platform-appropriate ACLs on Windows.

checkpoint replay hazard P1 : crash occurring between tool dispatch and OBSERVING checkpoint causes re-execution of side-effecting tools upon session resume; require write-ahead tool intent logging with call identifiers to guarantee idempotency.

subagent tree-wide budget P1 : subagent_depth <= 3 bounds call stack depth but permits unbounded fanout breadth (e.g. 4^3 = 64 subagents); require global ledger shared across agent tree enforcing aggregate limits on agent count, tokens, cost, and concurrency.

tool adapter data envelope P1 : prompt-based tool adapter injects tool observations as user-role messages; creates prompt injection surface if tool output contains model instruction text; require fixed delimiter envelope tagging tool observations as untrusted data and escaping XML tag delimiters.

statechart and health tracker concurrency P1 : swarm fanout and parallel subagents access ProviderHealthTracker concurrently without synchronization; require thread locks around health counters and circuit breaker state transitions.


## 5. Divergent Critiques and Tradeoff Analysis

divergent focus 1 : Opus 5.5 demands statechart error partitioning (splitting ERROR into ERROR_RETRYABLE and ERROR_FATAL) to guarantee deterministic resume; Sol 6.1 focuses on write-ahead intent journaling to handle crash recovery regardless of error classification; synthesis: implement intent journaling first, then refine state taxonomy.

divergent focus 2 : Sol 6.1 emphasizes that CommandInspector is purely advisory and shell metacharacters easily bypass command name deny-lists; Opus 5.5 focuses on scrubbed child process environments preventing loaded credentials from leaking to subprocesses; synthesis: combine environment isolation with strict SandboxRunner containerization.

divergent focus 3 : GLM 5.3 Flash identifies prompt cache invalidation caused by dynamic tool prompt injection via tool_adapter; recommends splitting or hoisting tool prompt schemas above the immutable invariant prefix; Opus 5.5 identifies cache bust caused by placing scratchpad memory before the cache breakpoint; synthesis: place invariant system prompt and tool schemas before token-zero cache breakpoint, placing dynamic scratchpad and turn history strictly after.

divergent focus 4 : GLM 5.3 Flash recommends developer ergonomics enhancements (/retry, /skip, /context dump, /auth status, and tab completion); Opus and Sol emphasize boundary fences and untrusted input containment; synthesis: developer commands enhance productivity without violating security invariants if kept read-only or explicit.


## 6. Prioritized Action Matrix

priority P0 immediate :
- implement workspace path confinement for read_file, write_file, edit_file, list_dir, grep_search, find_files.
- add mtime and file-hash validation to edit_file before committing replacement.
- increase secret masking threshold to 20 characters; use fixed redaction below threshold.
- create credential files atomically with mode 0600 via os.O_CREAT | os.O_EXCL.

priority P1 high :
- implement global agent ledger tracking total child agent invocations and aggregate token budgets across subagent trees.
- implement write-ahead intent logging in SessionCheckpointer to prevent side-effect tool replays on resume.
- wrap prompt-adapter observations in untrusted data delimiters and escape literal <tool_call> tags in tool outputs.
- synchronize ProviderHealthTracker state transitions with threading locks.

priority P2 ergonomics and cache optimization :
- align prompt caching layout: place tool schemas into immutable prefix; place scratchpad strictly after cache breakpoint.
- add REPL slash commands: /context dump, /auth status, /retry, and snapshot-based /undo stack.
- parallelize pre-flight credential probes across configured providers.

