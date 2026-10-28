"""
browser_login: one-time interactive niconico login for Hydra's dedicated
Playwright browser profile.

Opens the dedicated profile (C:\\Users\\jpm05\\hydra\\browser-profile) in a
visible Edge window at the niconico login page. The owner signs in by hand;
nothing in this tool types, reads, or stores credentials. The session
cookie lands in the profile on disk, and every later fetch_work run reuses
it. Close the browser window when the login is done.

Run it via C:\\Users\\jpm05\\hydra\\login-niconico.bat, or directly:
  browser-venv\\Scripts\\python.exe -P computer_use\\browser_login.py
"""

from __future__ import annotations

import sys

PROFILE = r"C:\Users\jpm05\hydra\browser-profile"
LOGIN_URL = "https://account.nicovideo.jp/login"


def main() -> int:
    from playwright.sync_api import sync_playwright

    print("Hydra browser login - niconico")
    print(f"Profile: {PROFILE}")
    print("A browser window opens at the niconico login page.")
    print("Sign in as bluebarrels in that window, then CLOSE the window.")
    print("The login persists in the profile for all later fetch_work runs.")
    with sync_playwright() as pw:
        context = None
        for channel in ("msedge", "chrome", None):
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=PROFILE,
                    channel=channel,
                    headless=False,
                    locale="ja-JP",
                    viewport={"width": 1280, "height": 900},
                )
                break
            except Exception as exc:  # noqa: BLE001 - try next channel
                print(f"channel {channel} failed: {exc}", file=sys.stderr)
        if context is None:
            print("ERROR: no browser channel could launch", file=sys.stderr)
            return 1
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            context.wait_for_event("close", timeout=0)
        except KeyboardInterrupt:
            context.close()
    print("Browser closed. Login state saved in the profile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
