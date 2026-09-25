# -*- coding: utf-8 -*-
"""Unit tests for the API-first backend and the auto/UI fallback decision.

These tests never touch the network or the real WorkBuddy account: the
vendor REST call (``_vendor_buddy_station.api_call``) is monkey-patched with
canned responses, and ``api_backend.get_credentials`` is monkey-patched to
return a fake token. We verify:

  1) api_checkin / api_travel succeed on normal responses.
  2) idempotent "already done" states are reported as success (not failure).
  3) genuine API errors are reported as 'fail' so the caller can fall back.
  4) run_via_api() returns the correct need_ui set (which tasks must be
     retried via UI) for every combination above, plus the
     "no credentials" and "no api_backend module" cases.
"""
import argparse
import sys
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import _vendor_buddy_station as V
import api_backend
import auto_growth  # noqa: E402

CHEEKIN_STATUS = "/v2/billing/meter/checkin-activity-status"
CHEEKIN_DO = "/v2/billing/meter/daily-checkin"
TRAVEL_STATUS = "/activity/growth/buddy/travel/status"
TRAVEL_CLAIM = "/activity/growth/buddy/travel/claim"
TRAVEL_DEPART = "/activity/growth/buddy/travel/depart"


def make_args(**over):
    a = argparse.Namespace()
    a.skip_checkin = False
    a.skip_buddy = False
    a.only_checkin = False
    a.only_claim = False
    a.only_travel = False
    a.destination = "咖啡馆"
    for k, v in over.items():
        setattr(a, k, v)
    return a


class FakeAPI:
    """Reusable fake for _vendor_buddy_station.api_call."""
    def __init__(self, state=None, raise_on=None):
        # state: {"checked": bool, "travel": dict or None}
        self.state = state or {"checked": False, "travel": {"state": "idle"}}
        # raise_on: set of paths that should raise
        self.raise_on = set(raise_on or [])
        self.calls = []

    def __call__(self, domain, token, path, method="POST", payload=None):
        self.calls.append((method, path, payload))
        if path in self.raise_on:
            raise RuntimeError("simulated network error on %s" % path)
        if path == CHEEKIN_STATUS:
            return 200, {"code": 0, "data": {"today_checked_in": self.state["checked"]}}
        if path == CHEEKIN_DO:
            return 200, {"code": 0, "data": {"today_checked_in": True}}
        if path == TRAVEL_STATUS:
            return 200, {"code": 0, "data": self.state["travel"] or {}}
        if path == TRAVEL_CLAIM:
            return 200, {"code": 0, "data": {"reward_credit": 8}}
        if path == TRAVEL_DEPART:
            return 200, {"code": 0, "data": {"state": "traveling", "location": {"name": "咖啡馆"}}}
        return 200, {"code": 0, "data": {}}


def setup_module(fake, creds=("fake-token", "www.codebuddy.cn")):
    V.api_call = fake
    auto_growth.api_backend.get_credentials = staticmethod(lambda: creds)


def test_checkin_and_travel_ok():
    fake = FakeAPI(state={"checked": False, "travel": {"state": "arrived", "daily_limit_reached": False}})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["checkin"] == "ok", res
    assert res["travel"] == "ok", res
    assert need == set(), need


def test_already_done_is_success():
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"}})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["checkin"] == "ok"
    assert res["travel"] == "ok"
    assert need == set()


def test_checkin_api_error_triggers_ui_fallback():
    # daily-checkin POST raises -> checkin fails -> must fall back to UI
    fake = FakeAPI(state={"checked": False, "travel": {"state": "idle"}},
                   raise_on={CHEEKIN_DO})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["checkin"] == "fail"
    assert "checkin" in need, need


def test_travel_status_error_triggers_ui_fallback():
    # travel/status fetch fails -> travel fails -> gift+travel fall back to UI
    fake = FakeAPI(state={"checked": True, "travel": {"state": "arrived"}},
                   raise_on={TRAVEL_STATUS})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["travel"] == "fail"
    assert "gift" in need and "travel" in need, need


def test_only_travel_defers_claim():
    fake = FakeAPI(state={"checked": True, "travel": {"state": "idle"}})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args(only_travel=True))
    # only travel enabled; claim (gift) must NOT be attempted
    claim_calls = [c for c in fake.calls if c[1] == TRAVEL_CLAIM]
    assert claim_calls == [], "only-travel must not claim"
    assert res["travel"] == "ok"


def test_no_credentials_falls_back_everything():
    fake = FakeAPI()
    setup_module(fake, creds=None)  # get_credentials raises
    auto_growth.api_backend.get_credentials = staticmethod(
        lambda: (_ for _ in ()).throw(RuntimeError("no login state")))
    res, need = auto_growth.run_via_api(make_args())
    assert need == {"checkin", "gift", "travel"}, need


def test_missing_api_backend_module_falls_back_everything():
    fake = FakeAPI()
    setup_module(fake)
    saved = auto_growth.api_backend
    try:
        auto_growth.api_backend = None
        res, need = auto_growth.run_via_api(make_args())
        assert need == {"checkin", "gift", "travel"}, need
    finally:
        auto_growth.api_backend = saved


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
            passed += 1
        except Exception:
            print("FAIL", t.__name__)
            traceback.print_exc()
            failed += 1
    print("\n=== %d passed, %d failed ===" % (passed, failed))
    sys.exit(0 if failed == 0 else 1)
