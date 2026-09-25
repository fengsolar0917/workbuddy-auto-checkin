#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for auto_growth.py against the local mock WorkBuddy server.

Runs the script (real headless Chromium) per scenario and asserts the exit
code. Covers BOTH the daily check-in and the 龙焰喵 Buddy actions, plus
combined runs, so the single unified skill is verified end-to-end.

Usage:  .venv/Scripts/python run_growth_tests.py
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_server import start_free_server  # noqa: E402

VENV_PY = (Path.home() / ".workbuddy/binaries/python/envs/checkin_test/Scripts/python.exe")
SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "auto_growth.py"


def wait_port(port, tries=40):
    import socket
    for _ in range(tries):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


# (name, growth_query, checkin_query, extra_args, expected_code)
# growth_query / checkin_query are query strings appended to /growth and
# /workbench respectively (empty string = not relevant because skipped).
SCENARIOS = [
    # --- combined (default: everything on) ---
    ("combined_full", "gift=done", "ck=available_button&btn=1", [], 0),
    ("combined_loggedout", "logged=1", "logged=1", [], 4),
    # --- check-in only ---
    ("ck_inactive", "", "ck=inactive&btn=0", ["--skip-buddy"], 0),
    ("ck_claimed", "", "ck=claimed&btn=0", ["--skip-buddy"], 0),
    ("ck_available_button", "", "ck=available_button&btn=1", ["--skip-buddy"], 0),
    ("ck_available_nobutton", "", "ck=available_nobutton&btn=0", ["--skip-buddy"], 5),
    ("ck_loggedout", "", "logged=1", ["--skip-buddy"], 4),
    ("ck_claim_api", "", "ck=available_button&btn=0",
     ["--skip-buddy", "--claim-api", "/billing/meter/checkin"], 0),
    # --- buddy only ---
    ("bd_full", "", "", ["--skip-checkin"], 0),
    ("bd_gift_claimed", "gift=done", "", ["--skip-checkin"], 0),
    ("bd_loggedout", "logged=1", "", ["--skip-checkin"], 4),
    ("bd_travel_fail", "gift=done&failtravel=1", "", ["--skip-checkin"], 5),
    # --- buddy sub-tasks ---
    ("bd_only_claim", "", "", ["--only-claim"], 0),
    ("bd_only_travel", "gift=done", "", ["--only-travel"], 0),
    ("bd_only_checkin", "", "ck=available_button&btn=1", ["--only-checkin"], 0),
]


def main():
    if not SCRIPT.exists():
        print("FATAL: script not found at", SCRIPT)
        return 2

    passed = 0
    failed = 0
    for name, gq, cq, extra, expect in SCENARIOS:
        httpd, port = start_free_server()
        try:
            if not wait_port(port):
                print("[%s] ERROR: mock server did not start" % name)
                failed += 1
                continue

            growth_url = "http://127.0.0.1:%d/growth?%s" % (port, gq) if gq else \
                "http://127.0.0.1:%d/growth" % port
            checkin_url = "http://127.0.0.1:%d/workbench?%s" % (port, cq) if cq else \
                "http://127.0.0.1:%d/workbench" % port

            # copy script to a temp dir so its .log doesn't pollute the skill dir
            tmp = Path(tempfile.mkdtemp(prefix="growth_test_"))
            script_copy = tmp / "auto_growth.py"
            script_copy.write_bytes(SCRIPT.read_bytes())
            bot_dir = tmp / "botprofile"

            cmd = [
                str(VENV_PY), str(script_copy),
                "--growth-url", growth_url,
                "--checkin-url", checkin_url,
                "--bot-profile-dir", str(bot_dir),
            ] + extra
            try:
                rc = subprocess.run(cmd, timeout=90, capture_output=True, text=True).returncode
            except subprocess.TimeoutExpired:
                rc = -1
        finally:
            httpd.shutdown()

        ok = (rc == expect)
        passed += ok
        failed += (not ok)
        print("--- %-20s expect %d -> got %d  %s" %
              (name, expect, rc, "PASS" if ok else "FAIL"))
        if not ok:
            print("    cmd:", " ".join(cmd))

    print("\n=== %d passed, %d failed ===" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
