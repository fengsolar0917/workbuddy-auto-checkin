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
STREAK = "/activity/growth/streak"
REDEEM = "/activity/growth/redeem"
LOT_SUM = "/activity/growth/lottery/summary"
LOT_DRAW = "/activity/growth/lottery/draw"
HEATMAP = "/activity/growth/heatmap"
MAKEUP = "/activity/growth/makeup-cards/use"

# 连登任务固定「今天」（补签按当月过滤，需确定性）
import datetime as _dt
FAKE_TODAY = _dt.date(2026, 9, 26)


def default_streak(days=30, balance=0,
                   s7="claimed", s14="claimed", s28="claimed",
                   madeup=()):
    return {"streak": {"days": days, "makeup_dates": list(madeup)},
            "makeup_cards": {"balance": balance, "max": 4},
            "redemption_status": {"tier_7d_status": s7, "tier_14d_status": s14,
                                   "tier_28d_status": s28, "remaining_days": days,
                                   "tiers": []},
            "launch_date": "2026-06-17"}


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
        # state: {"checked": bool, "travel": dict, "streak": dict,
        #         "chances": int, "cells": [heatmap cell dicts]}
        self.state = state or {"checked": False, "travel": {"state": "idle"}}
        self.state.setdefault("streak", default_streak())
        self.state.setdefault("chances", 0)
        self.state.setdefault("cells", [])
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
        if path == STREAK:
            return 200, {"code": 0, "data": self.state["streak"]}
        if path == REDEEM:
            return 200, {"code": 0, "data": {}}
        if path == LOT_SUM:
            return 200, {"code": 0, "data": {"chances": self.state["chances"],
                                              "module": {"enabled": True}}}
        if path == LOT_DRAW:
            return 200, {"code": 0, "data": {"prize": {"name": "谢谢参与"}}}
        if path == HEATMAP:
            return 200, {"code": 0, "data": {"cells": self.state["cells"]}}
        if path == MAKEUP:
            return 200, {"code": 0, "data": {}}
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


# ---------- 连登奖励（兑换 / 抽奖 / 补签） ----------

def test_redeem_only_claimable_tiers():
    st = default_streak(days=15, s7="claimed", s14="claimable", s28="locked")
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"}, "streak": st})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    redeem_calls = [c for c in fake.calls if c[1] == REDEEM]
    assert len(redeem_calls) == 1, redeem_calls
    assert redeem_calls[0][2]["tier"] == "14d"
    assert "client_token" in redeem_calls[0][2]
    assert res["streak"] == "ok", res
    assert need == set(), need


def test_redeem_skips_when_nothing_claimable():
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"}})
    setup_module(fake)  # default: all claimed
    res, _ = auto_growth.run_via_api(make_args())
    assert [c for c in fake.calls if c[1] == REDEEM] == []


def test_redeem_handles_available_status():
    # 官方接口实测返回 available（达成天数待领取），必须与 claimable 等价处理
    st = default_streak(days=28, s7="claimed", s14="claimed", s28="available")
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"}, "streak": st})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    redeem_calls = [c for c in fake.calls if c[1] == REDEEM]
    assert len(redeem_calls) == 1, redeem_calls
    assert redeem_calls[0][2]["tier"] == "28d"
    assert res["streak"] == "ok", res
    assert need == set(), need


def test_lottery_draws_available_chances():
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"}, "chances": 2})
    setup_module(fake)
    res, _ = auto_growth.run_via_api(make_args())
    draws = [c for c in fake.calls if c[1] == LOT_DRAW]
    assert len(draws) == 2, draws
    assert res["streak"] == "ok"


def test_lottery_skips_without_chances():
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"}, "chances": 0})
    setup_module(fake)
    res, _ = auto_growth.run_via_api(make_args())
    assert [c for c in fake.calls if c[1] == LOT_DRAW] == []


def test_makeup_uses_card_for_broken_date():
    st = default_streak(days=10, balance=2, s7="claimed", s14="locked", s28="locked")
    cells = [{"date": "2026-09-20", "score": 0}, {"date": "2026-09-21", "score": 1}]
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"},
                          "streak": st, "cells": cells})
    setup_module(fake)
    saved_today = api_backend._today
    api_backend._today = lambda: FAKE_TODAY
    try:
        res, _ = auto_growth.run_via_api(make_args())
    finally:
        api_backend._today = saved_today
    makeups = [c for c in fake.calls if c[1] == MAKEUP]
    # days=10, 最近未达成档 14d → need=4，但断登只有 1 天 → 只用 1 张
    assert len(makeups) == 1, makeups
    assert makeups[0][2]["target_date"] == "2026-09-20"
    assert res["streak"] == "ok"


def test_makeup_skips_when_all_tiers_claimed():
    cells = [{"date": "2026-09-20", "score": 0}]
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"},
                          "streak": default_streak(days=30, balance=4), "cells": cells})
    setup_module(fake)
    saved_today = api_backend._today
    api_backend._today = lambda: FAKE_TODAY
    try:
        auto_growth.run_via_api(make_args())
    finally:
        api_backend._today = saved_today
    assert [c for c in fake.calls if c[1] == MAKEUP] == []


def test_streak_api_error_reported_without_ui_fallback():
    # streak 读取失败 → streak 组 fail，但 UI 无法补做 → 不得进 need_ui
    fake = FakeAPI(state={"checked": False, "travel": {"state": "idle"}},
                   raise_on={CHEEKIN_DO, STREAK})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["streak"] == "fail", res
    assert need == {"checkin"}, need  # checkin fail 回退 UI；streak 不回退


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
