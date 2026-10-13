# hydra desktop specification

scope : contract for the native hydra desktop app. there is no server and no http route. the wire format is docs/STDIO_PROTOCOL.md.

entry : `hydra-desktop-rs/src/main.rs` opens one eframe window and spawns the agent child.
model : `hydra-desktop-rs/src/model.rs` folds agent events and action replies into UI state without any window code, so tests drive it directly.
turns : chat and Agent ReAct Loop send `turn`; Stop sends `cancel`; tool approval answers `approval`.
actions : terminal, swarm, computer use, agent runner, templates and jobs go through `action`.
tally : the Tokens and Cost readout sums `usage` messages, which carry the provider's own numbers. nothing is estimated.
missing : see INVENTORY notes in the progen report; swarm heads need gateway credentials, computer use is simulated off Windows.
