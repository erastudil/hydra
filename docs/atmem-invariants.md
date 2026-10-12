atmem invariants : two tiers, invariants are probed gates and episodes are the ledger

purpose : keep stale or wrong remembered context out of current work, with rules that cannot be forgotten
tier one : invariants, a small typed file, loaded every turn, never retrieved by similarity, enforced by probes
tier two : episodes, the append-only ledger in hydra_cli/memory.py, history that must be re-proved before it is trusted
precedence : invariant beats episode, a live probe beats a stored state fact, a newer episode beats an older one on the same subject
design source : atmem design review DESIGN.md, sections 4.2 and 4.3 for the ledger and verify before use
code : hydra_cli/invariants.py holds loader, probes, gate and context builder, stdlib only
gate check : scripts/verify.py function atmem_invariants_gate_context_and_episodes
command line : python -m hydra_cli.invariants check, or context, from any repo

file path : hydra_cli/invariants.json, tracked in git, shipped by install.sh and install.ps1 and package data
file shape : one JSON object with keys schema, token_cap, invariants, retired and nothing else
schema : the integer 1, any other value fails the load
token_cap : object with positive integers for invariants and episodes, today 900 and 1500
invariants : non-empty list of invariant objects, listed below
retired : list of objects with id, rule and retired_by, ids that were replaced and must never come back

invariant field id : INV-AREA-NNN, area is GIT or DOC or CODE or FS, NNN is three digits
invariant field scope : global for every repo, repo for the repo being worked in
invariant field rule : one plain sentence, at most 160 characters, no parentheses in prose
invariant field source : user:ref for a message from Pat, or doc:path for a repo document, never agent:anything
invariant field supersedes : null, or the id of an entry under retired that this one replaces
invariant field probe : name of an implemented probe, the loader rejects any name it cannot run
invariant field contradiction_reason : a code from the closed ledger vocabulary, used when an episode is superseded for contradicting this invariant
invariant field forbids : list of regex strings, an episode whose body matches one is superseded before injection, may be empty
unknown fields : rejected, a missing field is rejected too

id naming : areas group by what the probe touches, GIT for push and remote rules, DOC for docs and progen, CODE for shipped source, FS for file moves and 5S
id reuse : forbidden, a retired id stays under retired forever and no active entry may take it
id change : a rule that changes in meaning gets a new id with supersedes pointing at the retired one

reason codes : the closed vocabulary is memory.REASONS, stale_probe_failed, contradicted_by_user, contradicted_by_probe, superseded, expired, unverifiable
reason use : invariants are Pat's word, so an episode that contradicts one is superseded with contradicted_by_user unless the entry names another code
reason check : any code outside the set fails schema validation and the ledger refuses it as well
reason trail : the supersede line stays in the ledger with the invariant id in its body, so the cut is retraceable

who may change : only Pat, or an explicit rule he states in a message, an agent may propose a diff and never merge one
how a change lands : a normal branch and PR, the PR names the Pat message that authorises it, source on the new entry cites that message
pinned set : REQUIRED_IDS in hydra_cli/invariants.py lists the ids the gate demands, so deleting an entry from the file fails the gate
pin change : editing REQUIRED_IDS is a change to an invariant and needs Pat as well
retire : move the entry to retired with retired_by set, add its replacement with supersedes set, update REQUIRED_IDS in the same commit
agent write path : none, no command writes the file, the ledger commands cannot touch it

how checks run : each invariant names one probe, a probe takes the repo path and returns ok plus a detail of at most 80 characters
probe rule : read-only against the live repo, any scratch write goes to a throwaway ledger and temp dir
probe failure : an exception inside a probe counts as a violation, never as a pass
turn start : build_context runs every probe and renders every invariant with ok or VIOLATED and the detail
turn start wiring : hydra_cli/agent.py function _memory_block calls build_context, so the agent loop gets invariants then episodes
gate run : check_gate returns problems for a bad file, a missing required id, or a violated probe, the CLI exits 1 on any
broken file : build_context prints INVARIANTS BROKEN with the reason and tells the agent to stop and report, it never goes quiet
violated invariant : stays in the context flagged VIOLATED so the agent fixes it before other work
negative proof : verify gives every probe a fixture that breaks it and asserts the probe fails there

episodes interaction : build_context first supersedes live non-state episodes that match any forbids pattern
episodes interaction : preturn_block then re-proves every state fact with its probe and drops the ones the live repo contradicts
episodes interaction : superseded and dropped facts never reach the context as current, the old line stays readable in the ledger
episodes interaction : unprobed notes appear only under the UNVERIFIED header
episodes archive : trimmed lines are named in a marker line and fetched on demand with python scripts/gate.py log, which prints timestamp, id and kind, and the full line with source stays in the ledger
state facts : the ledger already rejects a state fact with no probe, so a claim like no remote cannot be stored as state without a live check
note facts : a stored no-remote claim that is not a probed state fact is superseded by INV-GIT-003

token cap rule : each tier has a hard cap in token_cap, tokens are estimated as bytes divided by four rounded up
invariants cap : checked at load against the worst case where every probe is violated with a full detail, a file that could overflow fails to load
invariants overflow : never trimmed at run time, the fix is shorter rules or Pat raising the cap
episodes cap : the block is cut from the end until it fits, header and repo status line always survive, a marker line records the cut
no crowd out : invariants render first and own their tier, so episodes cannot push them out
estimate bias : bytes over four never undercounts, so the real tokenizer count stays at or under the cap

starter INV-GIT-001 : never push to main or master, probe push_main_refused
starter INV-GIT-001 probe : pre-push hook installed, and memory.check_push refuses refs/heads/main and refs/heads/master
starter INV-GIT-002 : never force push, probe push_force_refused
starter INV-GIT-002 probe : pre-push hook installed, and check_push refuses a push whose remote sha is not an ancestor, built from a real unrelated commit
starter INV-GIT-003 : run git remote get-url origin before claiming no remote, probe remote_claims_probed
starter INV-GIT-003 probe : no live ledger fact that is not a probed state fact claims no remote for this repo or global
starter INV-DOC-001 : progen lint runs on staged docs and refuses the commit, probe progen_lint_enforced
starter INV-DOC-001 probe : pre-commit hook installed, progen found, a doc with a parenthetical is flagged and a clean doc passes, a missing progen fails closed
starter INV-CODE-001 : no stubs or work markers in shipped code, probe no_todo_markers
starter INV-CODE-001 probe : scans tracked source outside docs and tests for comment markers and raise NotImplementedError
starter INV-CODE-001 exemption : scripts/verify.py, it holds detector fixtures with marker text inside string literals
starter INV-FS-001 : a move leaves no stub at the old path, probe no_move_stubs
starter INV-FS-001 probe : no tracked file of 300 characters or fewer that says moved to, renamed to, now lives in, redirects to or see X instead

not yet probeable : commit on main refused, needs a detached fixture clone because check_commit reads the live branch
not yet probeable : every change lands in git and is pushed before a turn ends, needs a turn-end hook that does not exist yet
not yet probeable : one PR slot, needs a GitHub API probe and a credential the gate does not hold
not yet probeable : no direct main commits on the remote, needs branch protection read access
not yet probeable : swap or transfer to a wrong wallet is rejected, the Phantom guard lives outside this repo
not yet probeable : tests are real and never mocked, only the narrow AST mock ban exists and it is not mapped to an id
not yet probeable : purgatory is emptied only by Pat, needs a ledger event for every purge and a command that does not exist yet
not yet probeable : invariant changes carry a Pat message id, needs a commit trailer check that is not built

wired : memory.check_commit and the verify command of scripts/gate.py call check_gate, so a violated invariant blocks the commit
source ref : user:t117u is Pat's message t117u in the Grok Bot chat, 2026-10-11 12:56 PM CT, "approve, full gas today", approving the atmem design
source scope : that approval covers the design in DESIGN.md; a new invariant needs its own Pat message id in source
