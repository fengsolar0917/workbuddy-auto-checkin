#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy daily check-in auto-claimer (cross-platform / zero hardcoded paths).

Design rules (portable to any machine):
- Never hardcode username or absolute path: use Path.home() and auto-detection.
- Never assume a specific browser: auto-detect Edge / Chrome / Chromium.
- Never assume Playwright is installed: print a clear install hint, then exit.
- Degrade safely: inactive activity / already claimed / not logged in /
  profile locked are all handled without crashing.

How it works:
Reuse an already-logged-in browser profile (persistent user data dir) in
headless mode, open the workbench, read the check-in status via an
in-page fetch (which carries the same-site session cookie), then click the
claim button or POST an optional explicit claim API.

Dependencies:
    pip install playwright
    python -m playwright install chromium   # only if no Edge/Chrome present

Usage:
    python auto_checkin.py
    python auto_checkin.py --browser edge
    python auto_checkin.py --user-data-dir "C:/.../User Data" --profile "Profile 1"
    python auto_checkin.py --claim-api "/billing/meter/checkin"
    python auto_checkin.py --dry-run
"""

import argparse
import json
import platform
import sys
from datetime import datetime
from pathlib import Path

WORKBUDDY_URL = "https://www.workbuddy.cn/"
CHECKIN_STATUS_API = "/billing/meter/checkin-status"
CLAIM_TEXTS = ["领取今日礼包", "每日签到", "去签到", "签到", "领取", "立即领取"]

LOG_PATH = Path(__file__).with_suffix(".log")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def reconfigure_stdout():
    for s in (sys.stdout, sys.stderr):
        if s is None:
            continue
        try:
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def candidate_user_data_dirs():
    """Return existing default User Data dirs per OS (no hardcoded username)."""
    home = Path.home()
    sysname = platform.system()
    if sysname == "Windows":
        local = home / "AppData" / "Local"
        dirs = [
            local / "Microsoft" / "Edge" / "User Data",
            local / "Google" / "Chrome" / "User Data",
        ]
    elif sysname == "Darwin":
        app = home / "Library" / "Application Support"
        dirs = [
            app / "Microsoft Edge",
            app / "Google" / "Chrome",
        ]
    else:
        dirs = [
            home / ".config" / "microsoft-edge",
            home / ".config" / "google-chrome",
        ]
    return [d for d in dirs if d.exists() and d.is_dir()]


def browser_channel_for(dir_path):
    """Map a detected User Data dir to a Playwright channel, or None for bundled chromium."""
    name = dir_path.as_posix().lower()
    if "edge" in name:
        return "msedge"
    if "chrome" in name:
        return "chrome"
    return None


def detect_environment(args):
    """Resolve (user_data_dir, profile, channel). Explicit flags win."""
    if args.user_data_dir:
        ud = Path(args.user_data_dir).expanduser()
        if not ud.exists():
            log("ERROR: --user-data-dir not found: %s" % ud)
            return None
    else:
        found = candidate_user_data_dirs()
        if not found:
            log("ERROR: No Edge/Chrome user-data directory found. Install a browser "
                "or pass --user-data-dir. You can also rely on bundled Chromium "
                "by passing --browser chromium with no profile.")
            return None
        ud = found[0]
        log("Auto-detected user-data dir: %s" % ud)

    profile = args.profile or "Default"
    channel = None
    if args.browser:
        channel = {"edge": "msedge", "chrome": "chrome", "chromium": None}.get(args.browser)
    if channel is None and args.browser != "chromium":
        channel = browser_channel_for(ud)
    return {"user_data_dir": ud, "profile": profile, "channel": channel}


def get_status_via_page(page):
    """Read checkin-status inside the page so the session cookie is sent."""
    js = (
        "(async () => {"
        "  try {"
        "    const r = await fetch('%s', {method:'POST', credentials:'include',"
        "      headers:{'Content-Type':'application/json'}, body:'{}'});"
        "    return await r.text();"
        "  } catch(e) { return 'ERR:' + e.message; }"
        "})()" % CHECKIN_STATUS_API
    )
    try:
        text = page.evaluate(js)
    except Exception as e:
        return None, "evaluate-failed:%s" % e
    if isinstance(text, str) and text.startswith("ERR:"):
        return None, text
    try:
        return json.loads(text), None
    except Exception as e:
        return None, "json-parse-failed:%s" % e


def post_claim_via_page(page, claim_api):
    js = (
        "(async () => {"
        "  try {"
        "    const r = await fetch('%s', {method:'POST', credentials:'include',"
        "      headers:{'Content-Type':'application/json'}, body:'{}'});"
        "    return await r.text();"
        "  } catch(e) { return 'ERR:' + e.message; }"
        "})()" % claim_api
    )
    try:
        return page.evaluate(js), None
    except Exception as e:
        return None, str(e)


def click_claim_button(page):
    for txt in CLAIM_TEXTS:
        try:
            loc = page.get_by_text(txt, exact=False).first
            if loc.count() and loc.is_visible(timeout=3000):
                loc.click()
                return txt
        except Exception:
            continue
    return None


def main():
    reconfigure_stdout()
    parser = argparse.ArgumentParser(
        description="Auto-claim WorkBuddy daily check-in credits (headless, no client).")
    parser.add_argument("--browser", choices=["edge", "chrome", "chromium"],
                        help="Force a browser; default = auto-detect")
    parser.add_argument("--user-data-dir", help="Explicit User Data dir (folder containing 'Default')")
    parser.add_argument("--profile", default="Default", help="Profile name (default: Default)")
    parser.add_argument("--claim-api", help="Explicit claim endpoint to POST instead of clicking UI")
    parser.add_argument("--url", default=WORKBUDDY_URL, help="WorkBuddy base URL override")
    parser.add_argument("--dry-run", action="store_true", help="Detect env + report state only")
    args = parser.parse_args()

    # dependency check
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("ERROR: Playwright not installed. Run:")
        log("  python -m pip install playwright")
        log("  python -m playwright install chromium   # only if no Edge/Chrome present")
        return 2

    env = detect_environment(args)
    if env is None:
        return 2

    log("Target: browser=%s profile=%s user_data=%s" % (
        env["channel"] or "bundled-chromium", env["profile"], env["user_data_dir"]))

    user_dir = str(Path(env["user_data_dir"]) / env["profile"])
    kwargs = dict(
        user_data_dir=user_dir,
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    if env["channel"]:
        kwargs["channel"] = env["channel"]

    code = 0
    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            log("ERROR: Browser launch failed (profile locked or browser missing?): %s" % e)
            log("Tip: close the browser using this profile, or pick a dedicated profile.")
            return 3

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            log("WARN: Navigation issue, continuing: %s" % e)

        page.wait_for_timeout(5000)

        data, err = get_status_via_page(page)
        if err:
            log("ERROR: Cannot read check-in status (%s). Not logged in or API changed." % err)
            log("Action: open this profile in the browser, sign in to workbuddy.cn once, then retry.")
            context.close()
            return 4

        active = data.get("active")
        checked = data.get("today_checked_in")
        log("Status: active=%s today_checked_in=%s" % (active, checked))

        if args.dry_run:
            log("DRY-RUN: no claim performed.")
            context.close()
            return 0

        if active is False:
            log("INFO: Check-in activity is inactive now. Skipping safely.")
            context.close()
            return 0
        if checked is True:
            log("INFO: Already claimed today. Skipping.")
            context.close()
            return 0

        # attempt claim
        claimed = False
        if args.claim_api:
            resp, e = post_claim_via_page(page, args.claim_api)
            if e:
                log("WARN: claim API call failed: %s" % e)
            else:
                log("Claim API responded: %s" % str(resp)[:200])
                claimed = True
        else:
            clicked = click_claim_button(page)
            if clicked:
                log("OK: clicked claim button '%s'" % clicked)
                claimed = True
            else:
                log("WARN: No claim button found on page. If you know the real "
                    "claim endpoint, rerun with --claim-api.")

        page.wait_for_timeout(3000)

        data_after, err2 = get_status_via_page(page)
        if data_after:
            log("After: today_checked_in=%s today_credit=%s" % (
                data_after.get("today_checked_in"), data_after.get("today_credit")))
            if data_after.get("today_checked_in") is True:
                claimed = True

        if not claimed:
            log("WARN: Claim not confirmed. Check login state / activity / UI.")
            code = 5

        context.close()

    log("Done.")
    return code


if __name__ == "__main__":
    sys.exit(main())
