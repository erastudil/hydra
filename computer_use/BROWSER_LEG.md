# browser leg (Playwright, laptop-local)

scope : a dedicated Playwright browser on the owner's laptop for downloads
that require a logged-in web session (first job: COVER's official MMD
models on Nico Nico 3D, which the sealed Muse panel browser cannot land
on a reachable disk).

dialect : plain notes; the genome files keep progen.

## invariants

dedicated profile : C:\Users\jpm05\hydra\browser-profile. Never the owner's
daily Chrome/Edge profile; never automate his existing windows.

persistence : launch_persistent_context on that profile. A login done once
persists on disk and is reused by every later run.

credentials : the tools never type, read, or store credentials. A logged-out
profile yields an AUTH-NEEDED result and a clean stop; the owner signs in
once through the login launcher.

browser : installed Microsoft Edge via channel "msedge" (fallback order:
msedge, chrome, bundled Chromium).

runtime : C:\Users\jpm05\hydra\browser-venv (Python 3.14, playwright). Run
tools from the hydra checkout root so the computer_use package resolves;
note the laptop quirk: never run Python with C:\Users\jpm05 as the working
directory (a stray inspect.py there shadows the stdlib module).

## tools

fetch_work.py : single-work and batch downloads from 3d.nicovideo.jp.

  Invocation is script form with -P, deliberately not -m: the
  computer_use package __init__ imports the desktop stack (and its
  cryptography dependency), which this lean leg does not carry.

  single :
    browser-venv\Scripts\python.exe -P computer_use\fetch_work.py ^
      --work td94990 --expect-size 19893829 ^
      --out C:\Users\jpm05\Documents\dump

  batch (drains the warehouse's LOGIN-GATED official rows) :
    browser-venv\Scripts\python.exe -P computer_use\fetch_work.py ^
      --manifest C:\path\to\MANIFEST.tsv ^
      --out C:\Users\jpm05\Documents\dump

  A manifest row qualifies when format=zip, source_url is a
  3d.nicovideo.jp/works/tdNNN page, drive_verified is not yes, and the
  terms note carries LOGIN-GATED with a "Declared size N bytes" figure.
  One JSON line per work on stdout; the batch stops immediately on
  AUTH-NEEDED.

browser_login.py : one-time headed login window on the dedicated profile.
Owner-facing launcher: C:\Users\jpm05\hydra\login-niconico.bat.
