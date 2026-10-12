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

thread : the conversation key, letters digits dash underscore dot. it is the session id on disk and the ledger key, so a thread survives restarts.

call : a counter per turn, starting at 1, tying a `tool_start` to its `tool_result`. tools run one at a time.


## handshake

1. the agent writes `hello` first, before reading anything.
2. the app may answer with `hello`. if its protocol differs from the agent's, the agent writes `error` code version and exits 2.
3. an app that sends no `hello` is treated as speaking the agent's version.

agent hello : `{"type":"hello","protocol":1,"agent":"hydra","capabilities":[...],"context_modes":["recall","compact","sliding"]}`

app hello : `{"type":"hello","protocol":1}`

capabilities : turn, cancel, approval, permission, sessions, history, action.


## messages from the app to the agent

turn : start a turn.
- turn : string, required.
- prompt : string, required, non-empty.
- thread : string, required.
- cwd : string, required, an existing directory. the project dir the tools work in.
- mode : recall, compact or sliding. default recall.
- model : alias string. default is the orchestrator model. `local` and `free` pick those tiers.
- permission : manual, accept_edits, plan or bypass. default bypass, or manual when approve is true. may differ on every turn.
  - manual : every tool call asks the app, except tools the app allowed for good with `allow_always`.
  - accept_edits : read only tools and write_file and edit_file run on their own, every other tool asks.
  - plan : read only tools run on their own, every other tool is skipped without asking and the model is told it was skipped.
  - bypass : nothing asks.
- approve : boolean, older spelling. true means manual, false means bypass. permission wins when both are sent.
- context_chars : integer of at least 4096. the context budget in characters. default is four times the model window.

cancel : stop a turn. fields turn. the turn ends with `done` cancelled true. a pending approval is released.

approval : answer an `approval_request`. fields id, decision. decision is allow, allow_always, skip or abort.
- allow runs the tool.
- allow_always runs the tool and lets that tool run unasked for the rest of the process, in that thread only.
- skip does not run it, the turn continues and the model sees that it was skipped.
- abort ends the turn. `done` has ok false and cancelled false.

shutdown : finish cleanly. a running turn is cancelled and awaited, then the process exits 0.

sessions : list threads on disk. fields id, optional. reply `session_list`.

history : read stored turns of a thread. fields id, thread, limit optional with default 200. reply `history_result`.

action : run a desktop service by command palette name, for the old panels. fields id, action, params object optional. the names are the keys of COMMAND_PALETTE_ACTIONS in hydra_cli/desktop_core.py. reply `action_result`. actions run off the reader thread, so several may be in flight and replies can arrive out of order, match them by id.


## messages from the agent to the app

hello : see handshake.

token : fields turn, text. streamed answer text in arrival order. concatenated they are the model reply of the current round.

tool_start : fields turn, call, tool, arguments. arguments is the parsed object, or the raw string if it was not JSON. sent after approval, just before the tool runs. skipped tools never produce it.

tool_result : fields turn, call, tool, is_error, output, truncated. output is cut at 4000 characters and truncated says so. always follows its `tool_start`.

auto_decision : fields turn, tool, decision, reason. decision is allow or skip. sent instead of an `approval_request` when the permission mode or an earlier allow_always settled the call. comes before the matching `tool_start`.

diff : fields turn, path, diff. the unified diff of a file change made by write_file or edit_file. sent after the write and before the matching `tool_result`.

usage : fields turn, model, prompt_tokens, completion_tokens, cost_usd, priced by desktop_core.TokenAccountingManager for that round. the provider's own usage numbers for one completion round, as reported by the endpoint. sent once per round, only when the provider reports usage. never estimated. the app sums them for the token and cost tally.

approval_request : fields turn, id, tool, arguments. sent only when the permission mode asks for that tool. the turn is blocked until the matching `approval`, a `cancel`, `shutdown` or stdin EOF. the last three count as abort.

done : fields turn, text, ok, cancelled. exactly one per turn and always the last message of that turn. text is the final answer, or the cancel marker text when cancelled. ok is false on cancel, abort and failure.

error : fields code, message, redacted, and turn or id when the failure belongs to one. redacted is true when secrets or url paths were scrubbed from message. a failure inside a turn writes `error` with turn, then `done` with ok false.

session_list : fields id, sessions. each session has thread and updated, in epoch seconds.

history_result : fields id, thread, turns. each turn has role and content, oldest first.

action_result : fields id, action, result. result is whatever the service returned, an object with isError.


## ordering

per turn : tokens, tool events, diffs and approval requests come in the order the agent produced them. nothing for a turn follows its `done`.

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
