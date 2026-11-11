# The per-family dialect layer

HYDRA-PASS §8 named this the next build after the 2026-11-01 pass: one
global `stdio` format shim is not enough, because each model family
speaks a different native envelope. The layer lives in
`hydra_cli/dialects.py` and is wired into the agent loop
(`hydra_cli/agent.py`): for every provider attempt the loop classifies
the serving envelope, sends the family's form, and parses the reply in
the same family form.

Hydra's internal envelope stays the single canonical shape everywhere
inside the harness: OpenAI-style messages, assistant `tool_calls`, and
`tool` role observations. The shims translate at the boundary only.

## The families

| Family | Seats | Native envelope |
| --- | --- | --- |
| `gemma` | Local Gemma E2B (named seat) and E4B (heavy escalation) on llama.cpp | Gemma 4 turn tokens (`<\|turn>role ... <turn\|>`), tool declarations/calls/responses in `<\|tool>` / `<\|tool_call>` / `<\|tool_response>` blocks, every string literal delimited with `<\|"\|>`, thought channels kept across tool calls inside one turn and stripped between turns |
| `bonsai` | Local Bonsai 2B (default), 4B and 9B on demand, on the Prism llama.cpp fork | Qwen/Hermes ChatML (`<\|im_start>role ... <\|im_end>`), tool specs in a `<tools>` block in the system turn, calls as `<tool_call>` JSON blocks, results in `<tool_response>` blocks; multimodal content parts pass through in standard API form, as the vendor serving docs specify |
| `api` | Remote OpenAI-compatible endpoints: OpenRouter, the free routes (Nemotron, Gemma 4 26B/31B, Ling), frontier providers | The API dialect is Hydra's internal envelope itself: native `tools` / `tool_calls` / `tool` messages pass through unchanged |

Dialect follows the **serving envelope, not the weights**. A Gemma
checkpoint behind a remote API speaks the API dialect, because the
endpoint defines the wire format. `family_for_route(model, url,
format_shim)` encodes this: an explicit catalog `format_shim` naming a
family wins, a remote URL is always `api`, and a local endpoint is
classified by the model it serves.

Classification is not activation. The in-envelope treatment (no native
`tools` field) engages only when the provider is Hydra's **managed
llama.cpp endpoint** (`is_managed_local_url`: the base from
`hydra_cli.local_server`), where Hydra owns the server and the model's
template. Any other endpoint — an Ollama or custom local base, a test
double on an ephemeral port — keeps the native envelope exactly as
before, so nothing that already works can regress.

## What each shim translates

Outbound (`adapt_messages` / `render_tool_index` / `render_prompt`):

- The tool catalog is rendered once per family: Gemma declaration
  blocks with delimited string literals, a Bonsai `<tools>` block with
  the call contract, or the native tools array for API.
- Assistant `tool_calls` become the family's call blocks; `tool` role
  observations become the family's response blocks (Gemma
  `<|tool_response>`, Bonsai `<tool_response>`, API native `tool`
  message).
- Gemma thought channels are stripped from replayed history, per
  Google's formatting guidance.

Inbound (`parse_tool_calls`):

- Gemma native call blocks are parsed by a real parser for the Gemma
  literal syntax: delimited strings, bare keys, nested values. A regex
  over JSON that the model never promised to emit would not be a
  parser.
- Bonsai and API text replies go through the shared text-form parser
  from the universal tool adapter, which remains the reactive fallback
  for any endpoint that rejects a native tool schema: the dialect path
  never removes that escape hatch, and API/native behavior for models
  already working is unchanged: the `api` shim is the identity.

## Why this layer exists: the rescope

The ADAPTER-RUN-2026-11-01 rescope classified 9,313 adapter dataset
rows and found 5,234 of them (56.2%) were not behavior at all:

- `envelope_exemplar` (20 rows): the wire envelopes themselves. The
  shims generate them deterministically; they are owned here.
- `lookup_render` (5,214 rows): deterministic renderings of tool specs
  and tool observations. `render_tool_index` and `render_observation`
  generate them per family; they are owned here.

Neither category belongs in adapter training data. What remains for
future adapters is the behavior residue (`hydra_knowledge`, 171 rows,
1.8%, plus real on-mission behavior) — which is what adapters are for.

## Catalog wiring

The catalog's `format_shim` field names the family per seat:
`gemma` on the four Gemma local aliases, `bonsai` on the thirteen
Bonsai aliases, unset elsewhere (remote routes classify as `api` by
URL). `resolve_route` already surfaced the field; the loop now
consumes it.

## Verification

`python scripts/verify.py dialect` runs the gate check
`dialect_layer_per_family`, which executes the real shims: family
classification including the remote-Gemma-is-API rule, catalog pins,
the Gemma round trip with lossless arguments, the Bonsai round trip,
API identity, and the agent-loop wiring.
