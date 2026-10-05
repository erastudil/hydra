# Hydra Announcement — X (Twitter) Launch Post

## Post Draft (Single / Hook Tweet)

Terminal AI tools shouldn't require 400MB node_modules or 12 different bespoke SDKs.

Introducing Hydra 🐉 — the sovereign multi-headed AI shell utility.

One command. Zero dependencies.

• Frontier models (Opus 5.5, Sol 6.1, Sonnet 3.7, Gemini 2.5)
• Zero-cost Free Forge routing
• Offline local inference (Ollama / llama.cpp)
• Multi-agent parallel swarm fan-out
• Pure UNIX pipes

```bash
# Direct model summoning
hydra opus 5.5 "Explain zero-cost abstractions"

# Zero-cost cloud routing
hydra free "List Raft invariants"

# Fully offline local weights
hydra local "Write a lock-free queue in C"

# Parallel multi-agent swarm
hydra swarm "Architect a high-throughput event pipeline"

# Pipe anything from your terminal
git diff | hydra sol 6.1 "Audit this patch for security flaws"
```

Install in 3 seconds:
`curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash`

Or Windows:
`irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex`

Or Python: `pip install hydra-ai-cli`
Or Node: `npx hydra-cli opus 5.5 "..."`

Open-source on GitHub:
👉 https://github.com/erastudil/hydra

---

## Thread / Follow-up Details

### Tweet 2: The Multi-Agent Swarm
Cut off one head, and three arise.
`hydra swarm "<task>"` spawns concurrent worker heads:
🏛️ Architect (Opus 5.5): Invariants, state boundaries, failure modes
⚡ Implementer (Sonnet 3.7): Production-grade, zero-dependency code
🛡️ Inspector (Sol 6.1): Security auditing, race conditions, edge cases
🔮 Synthesizer (Gemini 2.5): Unified execution roadmap

All running in parallel threads, streaming results directly to your shell.

### Tweet 3: Zero-Cost & Offline Fallback
Running on an airplane or air-gapped server?
`hydra local` automatically probes your local ports for Ollama, llama.cpp, or EasyLM WebGPU.
Want free cloud compute?
`hydra free` routes to Cloudflare Workers AI or OpenRouter free tiers.

### Tweet 4: Zero Dependencies
Pure Python standard library. Pure Node standard library.
No heavy dependencies to download. No telemetry. Apache-2.0.

Star and fork: https://github.com/erastudil/hydra
