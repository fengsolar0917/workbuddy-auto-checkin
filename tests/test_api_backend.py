# -*- coding: utf-8 -*-
"""Unit tests for the API-only backend (no browser, no UI fallback).

These tests never touch the network or the real WorkBuddy account: the
vendor REST call (``_vendor_buddy_station.api_call``) is monkey-patched with
canned responses, and ``api_backend.get_credentials`` is monkey-patched to
return a fake token. We verify:

  1) api_checkin / api_travel succeed on normal responses.
  2) idempotent "already done" states are reported as success (not failure).
  3) genuine API errors are reported as 'fail' (the caller surfaces them via
     exit code 5; there is no UI fallback layer).
  4) run_via_api() always returns an empty need_ui set — the API-only design
     has nothing to fall back to, so failed tasks are simply recorded as
     'fail' in results and surfaced by the caller.
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
            # 模拟服务端：补签成功 -> 连登天数 +1，达到档位天数的 locked 档解锁为 available
            st = self.state["streak"]
            st["streak"]["days"] = (st["streak"].get("days") or 0) + 1
            d = st["streak"]["days"]
            rs = st["redemption_status"]
            for tier, need in (("7d", 7), ("14d", 14), ("28d", 28)):
                k = "tier_%s_status" % tier
                if rs.get(k) == "locked" and d >= need:
                    rs[k] = "available"
            return 200, {"code": 0, "data": {}}
        return 200, {"code": 0, "data": {}}


def setup_module(fake, creds=("fake-token", "www.workbuddy.cn")):
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


def test_checkin_api_error_reported_as_fail():
    # daily-checkin POST raises -> checkin fails -> recorded as 'fail' (no UI fallback)
    fake = FakeAPI(state={"checked": False, "travel": {"state": "idle"}},
                   raise_on={CHEEKIN_DO})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["checkin"] == "fail"
    assert need == set(), need


def test_travel_status_error_reported_as_fail():
    # travel/status fetch fails -> travel fails -> recorded as 'fail' (no UI fallback)
    fake = FakeAPI(state={"checked": True, "travel": {"state": "arrived"}},
                   raise_on={TRAVEL_STATUS})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["travel"] == "fail"
    assert need == set(), need


def test_only_travel_defers_claim():
    fake = FakeAPI(state={"checked": True, "travel": {"state": "idle"}})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args(only_travel=True))
    # only travel enabled; claim (gift) must NOT be attempted
    claim_calls = [c for c in fake.calls if c[1] == TRAVEL_CLAIM]
    assert claim_calls == [], "only-travel must not claim"
    assert res["travel"] == "ok"


def test_no_credentials_returns_empty():
    fake = FakeAPI()
    setup_module(fake, creds=None)  # get_credentials raises
    auto_growth.api_backend.get_credentials = staticmethod(
        lambda: (_ for _ in ()).throw(RuntimeError("no login state")))
    res, need = auto_growth.run_via_api(make_args())
    # API-only: nothing to fall back to; the caller (main) surfaces via exit code 4
    assert need == set(), need
    assert res == {}, res


def test_missing_api_backend_module_returns_empty():
    fake = FakeAPI()
    setup_module(fake)
    saved = auto_growth.api_backend
    try:
        auto_growth.api_backend = None
        res, need = auto_growth.run_via_api(make_args())
        assert need == set(), need
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
        res, _ = auto_growth.run_via_api(make_args(makeup=True))
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
        auto_growth.run_via_api(make_args(makeup=True))
    finally:
        api_backend._today = saved_today
    assert [c for c in fake.calls if c[1] == MAKEUP] == []


def test_makeup_default_off():
    # 补签是 opt-in：默认（不加 --makeup）即使有断登日+有卡也不补
    st = default_streak(days=13, balance=2, s7="claimed", s14="locked", s28="locked")
    cells = [{"date": "2026-09-20", "score": 0}]
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"},
                          "streak": st, "cells": cells})
    setup_module(fake)
    saved_today = api_backend._today
    api_backend._today = lambda: FAKE_TODAY
    try:
        auto_growth.run_via_api(make_args())
    finally:
        api_backend._today = saved_today
    assert [c for c in fake.calls if c[1] == MAKEUP] == []


def test_makeup_skips_when_no_launch_date():
    # 接口未下发 launch_date 时宁可跳过，不用写死日期兜底
    st = default_streak(days=13, balance=2, s7="claimed", s14="locked", s28="locked")
    del st["launch_date"]
    cells = [{"date": "2026-09-20", "score": 0}]
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"},
                          "streak": st, "cells": cells})
    setup_module(fake)
    saved_today = api_backend._today
    api_backend._today = lambda: FAKE_TODAY
    try:
        res, _ = auto_growth.run_via_api(make_args(makeup=True))
    finally:
        api_backend._today = saved_today
    assert [c for c in fake.calls if c[1] == MAKEUP] == []
    assert res["streak"] == "ok"  # 安全跳过不算失败


def test_streak_makeup_runs_before_redeem():
    # 顺序契约：补签先于兑换。差 1 天到 14d、1 卡、1 断登日——
    # 补签后 14d 解锁，必须在同一次运行内被兑掉（月末场景不过夜）。
    st = default_streak(days=13, balance=1, s7="claimed", s14="locked", s28="locked")
    cells = [{"date": "2026-09-20", "score": 0}]
    fake = FakeAPI(state={"checked": True, "travel": {"state": "traveling"},
                          "streak": st, "cells": cells})
    setup_module(fake)
    saved_today = api_backend._today
    api_backend._today = lambda: FAKE_TODAY
    try:
        res, _ = auto_growth.run_via_api(make_args(makeup=True))
    finally:
        api_backend._today = saved_today
    paths = [c[1] for c in fake.calls]
    assert MAKEUP in paths and REDEEM in paths, paths
    assert paths.index(MAKEUP) < paths.index(REDEEM), paths
    redeem_calls = [c for c in fake.calls if c[1] == REDEEM]
    assert redeem_calls[0][2]["tier"] == "14d", redeem_calls
    assert res["streak"] == "ok"


def test_streak_api_error_reported_as_fail():
    # streak 读取失败 → streak 组 fail，无 UI 可补做 → need_ui 恒为空
    fake = FakeAPI(state={"checked": False, "travel": {"state": "idle"}},
                   raise_on={CHEEKIN_DO, STREAK})
    setup_module(fake)
    res, need = auto_growth.run_via_api(make_args())
    assert res["streak"] == "fail", res
    assert need == set(), need


# ---------- 目的地映射与 main() 退出码契约 ----------

def test_destination_maps_to_location_id():
    # --destination 中文名必须映射到官方 location_id（映射错了此前测不出来）
    fake = FakeAPI(state={"checked": True, "travel": {"state": "idle"}})
    setup_module(fake)
    auto_growth.run_via_api(make_args(destination="古镇客栈"))
    departs = [c for c in fake.calls if c[1] == TRAVEL_DEPART]
    assert departs and departs[0][2]["location_id"] == 4, departs


def test_main_exit_codes():
    # 退出码是定时任务告警依赖的对外契约：4=无登录态, 0=全部完成/安全跳过, 5=有失败
    ag = auto_growth
    saved_argv, saved_run = sys.argv, ag.run_via_api
    saved_creds = ag.api_backend.get_credentials
    try:
        sys.argv = ["auto_growth.py"]
        # 无法获取登录态 -> 4
        ag.api_backend.get_credentials = staticmethod(
            lambda: (_ for _ in ()).throw(RuntimeError("no login state")))
        assert ag.main() == 4
        # 有登录态且全部 ok -> 0
        ag.api_backend.get_credentials = staticmethod(lambda: ("t", "d"))
        ag.run_via_api = lambda args, creds=None: (
            {"checkin": "ok", "travel": "ok", "streak": "ok"}, set())
        assert ag.main() == 0
        # 任一步 fail -> 5
        ag.run_via_api = lambda args, creds=None: ({"checkin": "fail"}, set())
        assert ag.main() == 5
    finally:
        sys.argv = saved_argv
        ag.run_via_api = saved_run
        ag.api_backend.get_credentials = saved_creds


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
