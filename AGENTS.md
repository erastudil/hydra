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

`install.sh` and `install.ps1` must list every runtime file under `hydra_cli/` (`.py` / `.json`) plus `bin/hydra` and `bin/hydra.js`. `tests/test_installers.py` fails if the lists drift.

## Tests before claiming done

Prefer: `python3 -m pytest tests/test_display.py tests/test_cli.py tests/test_installers.py tests/test_fixes.py -q` (plus any tests for modules you touched).
