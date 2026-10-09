---
title: "hydra — execution genome"
summary: "sovereign multi-headed model summoning engine and router domain contract. commit discipline, verification gates, and ponytail invariants."
version: "2.0.0"
layer: genome
home: hydra/AGENTS.md
dialect: progen syntax
status: canon
---

# hydra

scope : sovereign multi-headed CLI AI engine and model router v1.2.0 at C:\Users\jpm05\documents\hydra.

canonical source of truth : origin/main on GitHub.


## commit discipline

branch isolation : stage and commit every intentional code mutation on a designated feature branch before turn end.

push invariant : execute git push -u origin <branch> after each logical commit series; reject local-only completion claims.

pull request gate : open or update pull request into main; never leave finished work on orphan branches or local disk.

remote truth invariant : treat cloud VM, bastion containers, and desktop Documents paths as transient scratch until mirrored to origin/main.

root tree protection : never delete or rewrite workspace roots under cleanup policies; scope deletions strictly to session-created disposable files.

turn completion gate : verify git status clean of uncommitted intentional work before session exit.

cryptographic signature : sign commits using configured user.name and user.email; never create unsigned shadow copies outside repository.


## entrypoints and manifestations

canonical cli entrypoints : posix bin/hydra, node bin/hydra.js, windows bin/hydra.bat and hydra.bat.

interactive repl : python REPL in hydra_cli/repl.py triggered via hydra, hydra chat, or hydra tui.

manifest sync : install.sh and install.ps1 must enumerate every runtime file under hydra_cli/ plus bin/hydra and bin/hydra.js; drift triggers verification failure.


## verification and ponytail doctrine

verification command : python scripts/verify.py.

prerequisite : execute python -m pip install -e .; require Node.js 18+ for bin/hydra.js checks.

exit condition : gate exits 0 only when every check passes; unsupported platform checks emit skip without failing process.

extension rule : define checks as functions in scripts/verify.py decorated with @check; execute real code or real subprocesses; never add files under tests/.

ponytail wu wei : pull all verification complexity into single-grip deterministic runner scripts/verify.py; reject sprawling mock catalogs and multi-file test suites.

zero fake tests : synthetic mocks asserting mocked return values denote zero truth value; banned universally; verification requires real runnable execution gates.

zero stubs : either code executes against real subprocesses and interfaces returning exit code 0 or code rejected from main branch.
