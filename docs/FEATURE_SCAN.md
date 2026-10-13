# hydra feature scan — Python build surface plus local usage evidence

status : Draft scan 2026-10-12; input to HYDRA_NEXT_SPEC keep-list; operator strike-review pending.

## usage evidence — actually exercised

chat turns : Alice conversation dominant 2026-10-07 in TUI; frontier planning turn on `sol 6.1 high` same day.

model switching : `/model`, `/model alice`, `/model alice-emap`, `/model sol 6.1 high` repeated; model selection core loop.

session commands : `/new`, `/help`, `/clear` exercised 2026-10-07.

hands queries : pool number asked twice 2026-10-11 and 2026-10-12; local deterministic answers valued.

status queries : repo status report, credit balances across providers 2026-10-12; inventory behavior valued.

pain evidence : 2026-10-11 verbatim complaint output truncated, missing topics, only comments visible; truncation defect confirmed operator-experienced.

desktop : sessions active 2026-10-12 with ledger and checkpoint stores; desktop state tracks sessions, active, model, tokens, cost.

## shell surface — proven strongest

commands : direct alias summon, `free`, `hf`, `local`, `swarm`, `hands`, `agent`, `serve`, `desktop`, `mcp list|test|init|config`, `sandbox run`, `banner`, `auth`, `stdio`, pipe protocol, `--json`, `--no-stream`, `--effort`, `--tier`.

aliases : ~90 registered; frontier opus/sonnet/sol/gemini/grok classes; free forge 4 models; local qwen coder models.

## repl and tui surface

slash set : `/model`, `/effort`, `/system`, `/models`, `/banner`, `/status`, `/clear`, `/quit`, `/exit`, `/q`, `/help`, `/shortcuts`, `/alias`, `/history`, `/page`, `/exit-confirm`; shortcuts `!m !s !c !b !h !q`, `/m /e /sys`.

tui features : slash completion on Tab, steer injection, glowing prompt bar, Hydra banner art, streaming render, thinking spinner with elapsed timer.

## desktop surface

modules : 34 Python modules including secret_vault, playwright_codegen, browser_storage, session replay and checkpointing, swarm session logger, system metrics, notification hub, theme manager, multi-workspace, model router balancer, fallback mesh, speculative decoder, ensemble voter, evaluator, context compressor, local vector indexer, clipboard, audio transcriber.

state : `~/.hydra/desktop/state.json` keys sessions, active, counter, nav, model, tokens, cost; per-session ledger.db plus checkpoints under `~/.hydra/sessions/`.

## unused or aspirational — keep per operator direction

vm execution : specced, zero local usage evidence.

computer use : desktop branch exists; zero session evidence.

credits top-up : specced from operator pain; balances checked via chat 2026-10-12.

genomes : legacy org-chart genomes exist as documents; zero runtime dispatch evidence in Hydra.

nexus evolution : future; scoring fields only in v1.

## scan verdict

core loop proven : summon, switch model, chat, check status, ask local hands.

fragile loop : long agent output truncation; Alice coupling inside TUI history.

dead weight candidates : speculative model_* desktop modules pending operator strike-review.
