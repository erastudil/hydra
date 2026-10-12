---
title: "hydra stdio protocol"
summary: "process boundary between a native app and the hydra agent. one json object per line over stdin and stdout."
version: "1.0.0"
layer: contract
home: hydra/docs/STDIO_PROTOCOL.md
dialect: progen syntax
status: canon
---

# hydra stdio protocol

scope : the native app spawns `python -m hydra_cli stdio` and talks to it over pipes. implemented by hydra_cli/stdio_adapter.py. gated by scripts/verify.py, every check named stdio_*.

version : protocol 1.


## framing

encoding : UTF-8, no byte order mark.

frame : one JSON object per line, terminated by `\n`. a trailing `\r` is tolerated on input and never written.

type : every object has a string `type`. field names are lowercase with underscores. unknown fields are ignored in both directions.

stdout : protocol lines only. at start the adapter moves the real stdout to a private handle and points descriptor 1 at stderr, so prints, tools and child processes cannot reach the protocol stream.

stderr : free text logs. the app may show or discard them. there is no `log` message.

stdin : the adapter moves the real stdin to a private handle and points descriptor 0 at the null device, so tools never read protocol bytes.

line limit : input lines longer than 1048576 bytes are dropped and answered with `error` code line_too_long. output lines have no limit, the app must read whole lines of any length.

backpressure : writes to stdout block when the pipe is full, which pauses the agent. the app must keep draining stdout, including while it waits on anything else. the adapter reads stdin on its own thread, so cancel and approval always arrive mid turn.


## ids

turn : chosen by the app, a non-empty string, unique per process. every agent message that belongs to a turn echoes it.

id : chosen by the app on request messages that get a reply, echoed in the reply. approval requests carry an id chosen by the agent, a1, a2 and so on.

thread : the conversation key, letters digits dash underscore dot. it is the session id on disk and the ledger key, so a thread survives restarts. the adapter saves cwd, mode, model, permission and a title taken from the first prompt in thread.json next to the ledger on every accepted turn. after a restart a turn that sends only thread and prompt resumes with the same project, settings and context.

call : a counter per turn, starting at 1, tying a `tool_start` to its `tool_result`. tools run one at a time.


## handshake

1. the agent writes `hello` first, before reading anything.
2. the app may answer with `hello`. if its protocol differs from the agent's, the agent writes `error` code version and exits 2.
3. an app that sends no `hello` is treated as speaking the agent's version.

agent hello : `{"type":"hello","protocol":1,"agent":"hydra","capabilities":[...],"context_modes":["recall","compact","sliding"]}`

app hello : `{"type":"hello","protocol":1}`

capabilities : turn, cancel, approval, permission, rules, checkpoint, skills, sessions, history, action.


## messages from the app to the agent

turn : start a turn.
- turn : string, required.
- prompt : string, required, non-empty.
- thread : string, required.
- cwd : string, an existing directory, the project dir the tools work in. required for a new thread. each thread keeps its own cwd, so parallel threads work in different projects.
- mode : recall, compact or sliding. default is the thread's last mode, or recall for a new thread.
- model : alias string. default is the thread's last model, or the orchestrator model for a new thread. `local` and `free` pick those tiers.
- permission : manual, accept_edits, plan or bypass. default bypass, or manual when approve is true. may differ on every turn. when omitted the thread's last permission is used.
  - manual : every tool call asks the app, except tools the app allowed for good with `allow_always`.
  - accept_edits : read only tools and write_file and edit_file run on their own, every other tool asks.
  - plan : read only tools run on their own, every other tool is skipped without asking and the model is told it was skipped.
  - bypass : nothing asks.
- skills : list of skill ids from `skill_list`, default empty. the body of each picked skill is added to the system prompt of this turn only. an unknown id fails the turn with `bad_request`.
- checkpoint : boolean, default true. when true the project is snapshotted before the turn runs, see `checkpoint`.
- approve : boolean, older spelling. true means manual, false means bypass. permission wins when both are sent.
- context_chars : integer of at least 4096. the context budget in characters. default is four times the model window.

cancel : stop a turn. fields turn. the turn ends with `done` cancelled true. a pending approval is released.

approval : answer an `approval_request`. fields id, decision. decision is allow, allow_always, skip or abort.
- allow runs the tool.
- allow_always runs the tool and saves a rule so the same call runs unasked from then on. optional fields prefix and scope.
  - scope : thread, the default, or project. a thread rule holds for that thread. a project rule holds for every thread whose cwd is the same directory. rules are saved on disk, in rules.json next to the thread ledger for thread scope and under ~/.hydra/projects for project scope, so they survive restarts.
  - prefix : a command prefix, only for run_command. the rule then covers commands that start with the prefix at a word boundary. a command that chains, redirects or substitutes, with any of && || ; | & < > a backtick, $( or a newline, never matches a prefix rule. the prefix must match the command being approved, else the adapter answers `error` code bad_request, still applies the decision and saves no rule.
  - without prefix the rule covers every call of that tool.
  - plan mode ignores rules.
- skip does not run it, the turn continues and the model sees that it was skipped.
- abort ends the turn. `done` has ok false and cancelled false.

shutdown : finish cleanly. a running turn is cancelled and awaited, then the process exits 0.

checkpoints : list the checkpoints of a thread, newest first. fields id, thread. reply `checkpoint_list`.

rewind : put the project files back to a checkpoint. fields id, thread, checkpoint. code only, the conversation is not rewound. the thread must not be running a turn, else `error` code busy. an unknown checkpoint or thread gets `error` code bad_request. before touching anything the adapter snapshots the current state, so a rewind can be undone by rewinding to the backup id in the reply. reply `rewind_result`.

skills : list the skills a turn may pick. field id. reply `skill_list`.

rules : list or remove the approval rules that apply to a thread. fields id, thread, op, rule. op is list, the default, or remove, which also needs rule, the rule id. reply `rules_result`.

sessions : list threads on disk. fields id, optional. reply `session_list`.

history : read stored turns of a thread. fields id, thread, limit optional with default 200. reply `history_result`.

action : run a desktop service by command palette name, for the old panels. fields id, action, params object optional. the names are the keys of COMMAND_PALETTE_ACTIONS in hydra_cli/desktop_core.py. reply `action_result`. actions run off the reader thread, so several may be in flight and replies can arrive out of order, match them by id.


## messages from the agent to the app

hello : see handshake.

token : fields turn, text. streamed answer text in arrival order. concatenated they are the model reply of the current round.

context : fields turn, rules, skills. sent once per turn after `checkpoint` and before the model runs. rules is the path of the AGENTS.md, .cursorrules or CLAUDE.md file found in cwd or up to five parents above it, or null. its text goes into the system prompt. skills is the list of skill ids added to this turn.

tool_start : fields turn, call, tool, arguments. arguments is the parsed object, or the raw string if it was not JSON. sent after approval, just before the tool runs. skipped tools never produce it.

tool_result : fields turn, call, tool, is_error, output, truncated. output is cut at 4000 characters and truncated says so. always follows its `tool_start`.

checkpoint : fields turn, thread, ok, id, reason. sent once per turn before anything else of that turn, unless the turn set checkpoint false. id is the checkpoint id, the state of the project just before the turn. ok false means the snapshot failed, for example git is missing, id is null, reason says why and the turn runs anyway.

checkpoint_list : fields id, thread, checkpoints. each has id, turn, time in epoch seconds.

rewind_result : fields id, thread, checkpoint, backup, restored, removed. restored lists files written back, removed lists files deleted because the checkpoint did not have them. paths are relative to the project, in sorted order.

auto_decision : fields turn, tool, decision, reason, and rule when a saved rule settled the call. decision is allow or skip. sent instead of an `approval_request` when the permission mode or a saved rule settled the call. comes before the matching `tool_start`.

diff : fields turn, path, diff. the unified diff of a file change made by write_file or edit_file. sent after the write and before the matching `tool_result`.

usage : fields turn, model, prompt_tokens, completion_tokens, cost_usd, priced by desktop_core.TokenAccountingManager for that round. the provider's own usage numbers for one completion round, as reported by the endpoint. sent once per round, only when the provider reports usage. never estimated. the app sums them for the token and cost tally.

approval_request : fields turn, id, tool, arguments. sent only when the permission mode asks for that tool. the turn is blocked until the matching `approval`, a `cancel`, `shutdown` or stdin EOF. the last three count as abort.

done : fields turn, text, ok, cancelled. exactly one per turn and always the last message of that turn. text is the final answer, or the cancel marker text when cancelled. ok is false on cancel, abort and failure.

error : fields code, message, redacted, and turn or id when the failure belongs to one. redacted is true when secrets or url paths were scrubbed from message. a failure inside a turn writes `error` with turn, then `done` with ok false.

skill_list : fields id, skills. each skill has id, keys, the words it is about, and title, the first line of its body.

rules_result : fields id, thread, rules. each rule has id, tool, prefix or null, and scope.

session_list : fields id, sessions. each session has thread, updated in epoch seconds, and cwd, mode, model, permission and title as last saved, null when unknown.

history_result : fields id, thread, turns, meta. each turn has role and content, oldest first. meta is the saved thread settings, empty for a thread without any.

action_result : fields id, action, result. result is whatever the service returned, an object with isError.


## ordering

per turn : tokens, tool events, diffs and approval requests come in the order the agent produced them. nothing for a turn follows its `done`.

checkpoint : the `checkpoint` message comes first in its turn.

tool order : approval_request or auto_decision, then tool_start, then diff if any, then tool_result.

parallel turns : up to 8 turns run at once, one per thread. a `turn` for a thread that is still running, with a turn id already in use, or past the limit of 8 gets `error` code busy and nothing running is touched. every message of a turn carries its turn id, approval ids are unique per process, and each turn owns its approval wait, cancel flag, diff, tool and usage hooks, so turns never see each other's events. messages of different turns interleave freely, order holds only within one turn. a turn sent after the `done` of the previous one is always accepted, and none of its messages come before that `done`.

replies : `session_list`, `history_result` and `action_result` echo the request id and may interleave with turn messages.


## errors

malformed line : not UTF-8, not JSON, or not an object with a string type. reply `error` code malformed. the adapter keeps running.

unknown type : reply `error` code unknown_type, with id when the line had one. the adapter keeps running.

bad request : a known type with a bad field. reply `error` code bad_request naming every problem, with turn or id when known.

unknown ids : cancel for a turn that is not running gets code unknown_turn. approval for a request that is not pending gets code unknown_approval.

turn failure : provider failure, missing credentials or any exception in the loop. `error` code turn_failed then `done`.

action failure : `error` code action_failed with the request id.

internal : a handler raised, for example a damaged ledger file. `error` code internal with turn or id when known. the adapter keeps running.

codes : malformed, unknown_type, bad_request, line_too_long, busy, unknown_turn, unknown_approval, turn_failed, action_failed, internal, fatal, version.


## exit codes

0 : shutdown message or stdin EOF. a running turn is cancelled first.

2 : protocol version mismatch.

70 : the app closed its end of stdout. the adapter notes it on stderr and exits at once, without finishing running turns. there is nobody left to hear them. a failure of the stdin reader itself also exits 70, after `error` code fatal.

kill : the app should send `shutdown` and wait before it kills the child.


## checkpoints

store : a bare git repository under ~/.hydra/shadow, one per project directory, using the project as work tree. the project's own repository, branches, stashes and files are never written. the project's .gitignore rules apply, so ignored files are neither saved nor restored. .git and .hydra directories are always skipped. git must be on the path, else every checkpoint reports ok false.

limits : a rewind restores and removes files only. it does not undo commands run outside the project directory and it does not rewind the conversation ledger.
