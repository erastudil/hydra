# Agent contract (Hydra)

`origin/main` on GitHub is the canonical source of truth for this repo.

## Commit discipline (non-negotiable)

This project previously lost real work when agents edited trees locally and never pushed — including a monorepo-cleanup that wiped a container workspace because nothing had landed in git. Do not repeat that.

1. **Every intentional change lands in git.** If you edit a file that should survive, stage and commit it on a feature branch before you consider the task done.
2. **Push to GitHub.** Local-only commits are not done. `git push -u origin <branch>` after each logical commit series.
3. **Open or update a PR into `main`.** Do not leave finished work on an orphan branch or only on disk.
4. **Never treat the cloud VM, a bastion/devbox container, or a desktop Documents path as the source of truth.** Those are scratch until mirrored to `origin/main`.
5. **Do not delete or rewrite workspace roots** (Documents, home, container `/`, monorepo roots) under “cleanup”, “not a monorepo”, or similar policies. Scope deletions to files you created in this session, and only after they are committed or explicitly disposable.
6. **Before ending a turn with code changes:** `git status` must be clean of intentional work (or the remaining files must be listed as explicitly local/temp). Untracked runtime modules (`hydra_cli/*.py`, launchers, installers, tests) are never “optional leftovers”.
7. **Sign the work in git.** Use the environment’s configured `user.name` / `user.email`. Do not invent unsigned shadow copies of the CLI outside this repository.

## Launchers

- Canonical CLI entrypoints: `bin/hydra` (POSIX), `bin/hydra.js` (Node), `bin/hydra.bat` / `hydra.bat` (Windows).
- Interactive TUI + slash commands: `hydra`, `hydra chat`, or `hydra tui` (Python REPL in `hydra_cli/repl.py`).
- Desktop/bastion bats that live outside this repo are not Hydra. Do not confuse Bastion launchers with Hydra TUI work — recover and port wanted behavior into this repo, then commit.

## Installer manifests

`install.sh` and `install.ps1` must list every runtime file under `hydra_cli/` (`.py`, `.json`, `.jsonl`, and `skills/*.md`) plus `bin/hydra` and `bin/hydra.js`. `python scripts/verify.py` fails if the lists drift.

## Verification and Ponytail doctrine

Primary verification gate:
`python scripts/verify.py`

Install first: `python3 -m pip install -e .` (pulls `prompt_toolkit`). Node.js 18+ is required for the `bin/hydra.js` checks. The gate exits 0 only when every check passes. A platform check that cannot run prints `skip` and does not fail the process.

Add a check: define a function in `scripts/verify.py` and decorate it with `@check`. Call real code or a real subprocess. Raise `Skip("reason")` only for a platform that cannot run it. Do not add files under `tests/`.

Invariants:
1. Zero fake tests: synthetic mocks asserting mocked return values denote zero truth value; banned universally; verification requires real runnable execution gates.
2. Zero stubs / zero placeholders: either code executes with real subprocesses/interfaces returning exit code 0 or it does not enter main.
3. Ponytail Wu Wei: pull all complexity into single-grip deterministic runners; reject sprawling mock catalogs and multi-file test suites.
