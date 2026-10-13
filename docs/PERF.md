# Hydra desktop — Rust release-bar measurements

box : headless Xvfb and llvmpipe software GL via `python3 scripts/perf_rust.py`.

stale frames : `scripts/stale_frame.py` reports 0/6 stale.

| measure | value | budget | result |
|---|---|---|---|
| cold start: first interactive frame, median of 10 | 120 ms (min 112, max 138) | <= 1000 ms | pass |
| cold start: backend hello, median of 10 | 227 ms (min 208, max 283) | <= 3000 ms | pass |
| idle: CPU over 20 s after start-up (app process, llvmpipe) | 0.1% of a core | < 2% | pass |
| idle: frames drawn over 20 s | 1 (0.05/s) | 0/s apart from animations (<= 1 heartbeat) | pass |
| memory: idle app, 3 projects / 20 threads | 126 MB | < 150 MB | pass |
| memory: app + backend | 156 MB | < 350 MB | pass |
| frames: streaming + scrolling 2,000 messages, CPU frame time p95 | 0.89 ms over 309 frames | <= 16.7 ms | pass |
| frames: same, p99 | 0.97 ms | < 33 ms | pass |
| frames: same, frame-to-frame interval p95 (xvfb, software GL) | 7.50 ms | <= 16.7 ms | pass |
| memory: 2,000 message thread (app) | 124 MB | < 400 MB | pass |
| kill -9 app x50 during streaming turns | no loss | every committed message and thread survives, state parses | pass |
| kill -9: streamed text kept across kills (journal) | 5559 chars of streamed replies kept over 50 kills, 50 replies marked interrupted | > 0 | pass |
| kill -9 backend child mid-turn | app alive=True, respawned=True, turn shown failed=True | app survives, respawns, turn marked failed | pass |
| truncated state.json recovers from .bak | recovered=True, rewritten state.json parses=True | all threads back, state.json rewritten | pass |
| clean-folder bundle: no system python, no checkout, PATH empty, HYDRA_REPO unset, full turn | system python hidden=True, reply streamed=True, exit 0 | turn completes | pass |
