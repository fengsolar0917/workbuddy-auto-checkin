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


# (name, growth_query, checkin_query, extra_args, expected_code, expect_out)
# growth_query / checkin_query are query strings appended to /growth and
# /workbench respectively (empty string = not relevant because skipped).
# expect_out (optional) = a substring that must appear in the script's stdout
#   (used to verify the "派当前展示的 Buddy" behaviour).
SCENARIOS = [
    # --- combined (default: everything on) ---
    ("combined_full", "gift=done", "ck=available_button&btn=1", [], 0, None),
    ("combined_loggedout", "logged=1", "logged=1", [], 4, None),
    # --- check-in only ---
    ("ck_inactive", "", "ck=inactive&btn=0", ["--skip-buddy"], 0, None),
    ("ck_claimed", "", "ck=claimed&btn=0", ["--skip-buddy"], 0, None),
    ("ck_available_button", "", "ck=available_button&btn=1", ["--skip-buddy"], 0, None),
    ("ck_available_nobutton", "", "ck=available_nobutton&btn=0", ["--skip-buddy"], 5, None),
    ("ck_loggedout", "", "logged=1", ["--skip-buddy"], 4, None),
    ("ck_claim_api", "", "ck=available_button&btn=0",
     ["--skip-buddy", "--claim-api", "/billing/meter/checkin"], 0, None),
    # --- buddy only ---
    ("bd_full", "", "", ["--skip-checkin"], 0, None),
    ("bd_gift_claimed", "gift=done", "", ["--skip-checkin"], 0, None),
    ("bd_loggedout", "logged=1", "", ["--skip-checkin"], 4, None),
    # --- travel: Plan A (派当前展示的 Buddy) ---
    # 1) 页面有派去旅行按钮 -> 成功派出
    ("bd_travel_dispatch", "gift=done&dispatch=1", "", ["--only-travel"], 0, "dispatched"),
    # 2) 页面没有派去旅行按钮 (真实常态: 每日自动出行) -> 安全跳过
    ("bd_travel_skipped", "gift=done", "", ["--only-travel"], 0, "安全跳过"),
    # 3) 已在旅行 (采风中) -> already
    ("bd_travel_already", "gift=done&traveling=1", "", ["--only-travel"], 0, "already"),
    # 4) 点到按钮但派发未出现提示 (模拟 UI 异常) -> 失败告警
    ("bd_travel_fail", "gift=done&dispatch=1&failtravel=1", "", ["--only-travel"], 5, "WARN"),
    # --- buddy sub-tasks ---
    ("bd_only_claim", "", "", ["--only-claim"], 0, None),
    ("bd_only_travel", "gift=done&dispatch=1", "", ["--only-travel"], 0, "dispatched"),
    ("bd_only_checkin", "", "ck=available_button&btn=1", ["--only-checkin"], 0, None),
]


def main():
    if not SCRIPT.exists():
        print("FATAL: script not found at", SCRIPT)
        return 2

    passed = 0
    failed = 0
    for name, gq, cq, extra, expect, expect_out in SCENARIOS:
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
                proc = subprocess.run(cmd, timeout=90, capture_output=True, text=True)
                rc = proc.returncode
                out = proc.stdout + proc.stderr
            except subprocess.TimeoutExpired:
                rc = -1
                out = ""
        finally:
            httpd.shutdown()

        ok = (rc == expect)
        if ok and expect_out and expect_out not in out:
            ok = False
            print("    [out-check] expected substring %r not found in stdout" % expect_out)
        passed += ok
        failed += (not ok)
        print("--- %-20s expect %d -> got %d  %s" %
              (name, expect, rc, "PASS" if ok else "FAIL"))
        if not ok:
            print("    cmd:", " ".join(cmd))
            if expect_out:
                print("    --- captured stdout (tail) ---")
                print("\n".join(out.splitlines()[-25:]))

    print("\n=== %d passed, %d failed ===" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
