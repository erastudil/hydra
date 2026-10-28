"""
fetch_work: download a Nico Nico 3D work file through Hydra's dedicated
Playwright browser profile.

The profile at C:\\Users\\jpm05\\hydra\\browser-profile is Hydra-owned and
dedicated: it is never the owner's daily Chrome/Edge profile, and no other
tool may point a browser at it. A niconico login performed once in this
profile (see browser_login.py) persists on disk and is reused by every run.

Contract:
  - The tool never types credentials. If the profile session is logged
    out, it prints an AUTH-NEEDED JSON line and exits 0.
  - On success it prints exactly one JSON line:
    {"status": "OK", "path": ..., "bytes": N, "matches_expected": bool}
  - All diagnostics go to stderr; stdout carries JSON lines only.

Usage:
  python -m computer_use.fetch_work --work td94990 --expect-size 19893829 \
      --out C:\\Users\\jpm05\\Documents\\dump
  python -m computer_use.fetch_work --manifest MANIFEST.tsv \
      --out C:\\Users\\jpm05\\Documents\\dump
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

DEFAULT_PROFILE = r"C:\Users\jpm05\hydra\browser-profile"
DEFAULT_CHANNEL = "msedge"
WORK_URL = "https://3d.nicovideo.jp/works/{work}"
AGREE_TEXT = "利用規約に同意してダウンロード"
DOWNLOAD_TEXT = "ダウンロード"

STATUS_OK = "OK"
STATUS_AUTH = "AUTH-NEEDED"
STATUS_SIZE = "SIZE-MISMATCH"
STATUS_ERROR = "ERROR"


def log(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def launch_context(pw, profile: str, channel: str, headless: bool):
    """Launch the persistent context, preferring an installed browser.

    Tries the requested channel first, then the other installed channel,
    then Playwright's bundled Chromium. Returns (context, channel_used).
    """
    channels = [channel]
    for alt in ("msedge", "chrome", None):
        if alt not in channels:
            channels.append(alt)
    errors = []
    for ch in channels:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=profile,
                channel=ch,
                headless=headless,
                accept_downloads=True,
                locale="ja-JP",
                viewport={"width": 1280, "height": 900},
            )
            return ctx, ch
        except Exception as exc:  # noqa: BLE001 - fallback chain by design
            errors.append(f"{ch}: {exc}")
            log(f"launch failed for channel={ch}: {exc}")
    raise RuntimeError("all browser channels failed: " + " | ".join(errors))


def session_logged_in(context, page) -> bool:
    """A niconico session exists iff a user_session cookie is present.

    The DOM login link is a secondary signal only; the cookie is decisive
    because the download session endpoint keys on it.
    """
    try:
        cookies = context.cookies("https://3d.nicovideo.jp/")
    except Exception:  # noqa: BLE001
        cookies = []
    for cookie in cookies:
        if cookie["name"].startswith("user_session") and cookie.get("value"):
            return True
    return False


def click_first_visible(page, text: str, exact: bool = True) -> bool:
    loc = page.get_by_text(text, exact=exact)
    count = loc.count()
    for i in range(count):
        item = loc.nth(i)
        try:
            if item.is_visible():
                item.click()
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def fetch_one(context, page, work: str, expect_size, out_dir: Path) -> dict:
    """Download one work. Assumes the session check already happened."""
    url = WORK_URL.format(work=work)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 - page is usable without idle
        pass

    download = None
    # First click: may start the download directly (terms already accepted
    # in a prior session) or open the license dialog.
    try:
        with page.expect_download(timeout=10_000) as dl_info:
            if not click_first_visible(page, DOWNLOAD_TEXT, exact=True):
                raise RuntimeError("download button not found on work page")
        download = dl_info.value
    except Exception as first_exc:  # noqa: BLE001
        log(f"first click produced no download ({first_exc}); trying dialog")
        agree = page.get_by_text(AGREE_TEXT, exact=False).first
        agree.wait_for(state="visible", timeout=20_000)
        # Some dialogs gate the button behind an agree checkbox.
        checkbox = page.locator("label:has-text('同意')").first
        try:
            if checkbox.count() > 0 and checkbox.is_visible():
                checkbox.click()
        except Exception:  # noqa: BLE001
            pass
        with page.expect_download(timeout=300_000) as dl_info:
            agree.click()
        download = dl_info.value

    failure = download.failure()
    if failure is not None:
        return {"status": STATUS_ERROR, "work": work,
                "error": f"download failed: {failure}"}
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / download.suggested_filename
    download.save_as(str(target))
    size = target.stat().st_size
    matches = None if expect_size is None else (size == expect_size)
    status = STATUS_OK if matches is not False else STATUS_SIZE
    return {"status": status, "work": work, "path": str(target),
            "bytes": size, "matches_expected": matches}


def open_session(pw, profile: str, channel: str, headless: bool):
    context, used = launch_context(pw, profile, channel, headless)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://3d.nicovideo.jp/", wait_until="domcontentloaded",
              timeout=60_000)
    return context, page, used


def run_single(args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context, page, used = open_session(
            pw, args.profile, args.channel, not args.headed)
        log(f"browser channel in use: {used}")
        try:
            if not session_logged_in(context, page):
                emit({"status": STATUS_AUTH, "work": args.work,
                      "detail": "profile session is logged out of niconico; "
                                "run the one-time login launcher first"})
                return 0
            result = fetch_one(context, page, args.work,
                               args.expect_size, Path(args.out))
            emit(result)
            return 0 if result["status"] == STATUS_OK else 2
        finally:
            context.close()


def iter_manifest_works(manifest_path: str):
    """Yield gated official works from the warehouse MANIFEST.tsv.

    A row qualifies when it is an un-downloaded zip hosted on
    3d.nicovideo.jp whose terms note carries a LOGIN-GATED marker. The
    expected size is parsed from the note's 'Declared size N bytes'.
    """
    with open(manifest_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if (row.get("format") or "").strip().lower() != "zip":
                continue
            match = re.search(r"3d\.nicovideo\.jp/works/(td\d+)",
                              row.get("source_url") or "")
            if not match:
                continue
            if (row.get("drive_verified") or "").strip().lower() == "yes":
                continue
            note = row.get("terms_note") or ""
            if "LOGIN-GATED" not in note:
                continue
            size_match = re.search(r"Declared size ([\d,]+) bytes", note)
            expect = (int(size_match.group(1).replace(",", ""))
                      if size_match else None)
            yield {"work": match.group(1), "expect_size": expect,
                   "talent": row.get("talent"),
                   "asset_name": row.get("asset_name")}


def run_batch(args) -> int:
    from playwright.sync_api import sync_playwright

    works = list(iter_manifest_works(args.manifest))
    log(f"manifest yielded {len(works)} gated works")
    ok = failed = 0
    with sync_playwright() as pw:
        context, page, used = open_session(
            pw, args.profile, args.channel, not args.headed)
        log(f"browser channel in use: {used}")
        try:
            if not session_logged_in(context, page):
                emit({"status": STATUS_AUTH, "works_pending": len(works),
                      "detail": "profile session is logged out of niconico; "
                                "batch stopped before the first work"})
                return 0
            for item in works:
                try:
                    result = fetch_one(context, page, item["work"],
                                       item["expect_size"], Path(args.out))
                except Exception as exc:  # noqa: BLE001 - keep batch moving
                    result = {"status": STATUS_ERROR, "work": item["work"],
                              "error": str(exc)}
                result["talent"] = item["talent"]
                emit(result)
                if result["status"] == STATUS_OK:
                    ok += 1
                else:
                    failed += 1
        finally:
            context.close()
    emit({"status": "BATCH-DONE", "ok": ok, "failed": failed,
          "total": len(works)})
    return 0 if failed == 0 else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_work")
    parser.add_argument("--work", help="Nico Nico 3D work id, e.g. td94990")
    parser.add_argument("--expect-size", type=int, default=None,
                        help="expected byte size of the downloaded file")
    parser.add_argument("--manifest",
                        help="warehouse MANIFEST.tsv for batch mode")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="dedicated Playwright profile directory")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL,
                        help="browser channel: msedge (default), chrome")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser window (default headless)")
    args = parser.parse_args(argv)
    if bool(args.work) == bool(args.manifest):
        parser.error("give exactly one of --work or --manifest")
    try:
        if args.manifest:
            return run_batch(args)
        return run_single(args)
    except Exception as exc:  # noqa: BLE001 - one JSON line, always
        emit({"status": STATUS_ERROR, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
