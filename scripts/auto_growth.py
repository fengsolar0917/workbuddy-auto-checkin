#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 成长计划自动化 (API-only, cross-platform, zero hardcoded paths).

用 WorkBuddy 官方 REST 接口直接完成成长计划页的一整套操作，无需启动
WorkBuddy 桌面客户端，也无需浏览器 (Playwright)：

  1. 每日签到 (POST /v2/billing/meter/daily-checkin, 幂等)
  2. 领取 Buddy 礼物 (Buddy 旅行带回的报告 + 积分)
  3. 派当前展示的 Buddy 去旅行 (选目的地, 确定派出; 仅当 Buddy 处于待派出状态)
  4. 月历连登奖励 (7/14/28 天里程碑自动兑换 · 自动抽奖 · 限量补签)

复用本机已登录的 WorkBuddy 登录态 (AES-256-GCM 信封解密, DPAPI/进程内存),
直连 https://www.workbuddy.cn 官方接口。

用法:
    python auto_growth.py                                  # 全部: 签到+礼物+旅行+连登
    python auto_growth.py --destination 古镇客栈            # 指定旅行目的地
    python auto_growth.py --skip-checkin                    # 只做 Buddy(礼物+旅行)+连登
    python auto_growth.py --only-claim                      # 只领礼物
    python auto_growth.py --only-travel                     # 只派旅行
    python auto_growth.py --only-checkin                    # 只签到
    python auto_growth.py --no-redeem --no-lottery --no-makeup  # 关闭连登奖励组

依赖: 仅 Python 标准库 (无需 pip install 任何包)。
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure sibling modules (api_backend, _vendor_buddy_station) are importable
# no matter the current working directory (the script may be copied elsewhere).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import api_backend  # API-only backend (the only execution path)
except ImportError:  # pragma: no cover - only when script is copied without companions
    api_backend = None

WORKBUDDY_BASE = "https://www.workbuddy.cn/"

# 旅行目的地名称 -> travel/status 的 location_id（与官方 4 个地点一致）
DEST_ID = {"咖啡馆": 1, "商场店铺": 2, "健身房": 3, "古镇客栈": 4}
DEFAULT_DEST = "咖啡馆"

LOG_PATH = Path(__file__).with_suffix(".log")


# ---------- logging (safe under pythonw / headless) ----------
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


# ---------- task resolution ----------
def dest_to_id(dest):
    return DEST_ID.get(dest or DEFAULT_DEST)


def resolve_tasks(args):
    """解析任务开关，返回 (do_checkin, do_gift, do_travel, do_redeem, do_lottery, do_makeup)。"""
    skip_buddy = args.skip_buddy or args.only_checkin
    skip_checkin = args.skip_checkin or args.only_claim or args.only_travel
    do_gift = (not skip_buddy) and (not args.only_travel)
    do_travel = (not skip_buddy) and (not args.only_claim)
    do_checkin = not skip_checkin
    # 连登奖励组（默认全开；getattr 兼容测试用的简易 Namespace）
    do_redeem = not getattr(args, "no_redeem", False)
    do_lottery = not getattr(args, "no_lottery", False)
    do_makeup = not getattr(args, "no_makeup", False)
    return do_checkin, do_gift, do_travel, do_redeem, do_lottery, do_makeup


def run_via_api(args):
    """用官方 API 完成各任务。返回 (results, need_ui)；API-only 模式下 need_ui 恒为空。
    results 的任务值为 'ok' / 'already' / 'claimed' / 'skipped' / 'fail' 之一。"""
    results = {}
    need_ui = set()
    do_checkin, do_gift, do_travel, do_redeem, do_lottery, do_makeup = resolve_tasks(args)

    if api_backend is None:
        log("[api] API 后端模块缺失（api_backend.py / _vendor_buddy_station.py）。")
        return results, need_ui
    try:
        token, domain = api_backend.get_credentials()
    except Exception as e:
        log("[api] 登录态/Token 不可用: %s" % e)
        return results, need_ui

    if do_checkin:
        r = api_backend.api_checkin(token, domain)
        log("[api][签到] %s — %s" % (r["outcome"], r["message"]))
        results["checkin"] = r["outcome"]

    if do_gift or do_travel:
        r = api_backend.api_travel(token, location_id=dest_to_id(args.destination),
                                    allow_claim=do_gift, allow_depart=do_travel)
        log("[api][Buddy旅行] %s — %s" % (r["outcome"], r["message"]))
        results["travel"] = r["outcome"]

    # 连登奖励组（兑换/抽奖/补签）：仅 API 可完成
    if do_redeem or do_lottery or do_makeup:
        r = api_backend.api_streak_all(token, do_redeem=do_redeem,
                                        do_lottery=do_lottery, do_makeup=do_makeup)
        log("[api][连登奖励] %s — %s" % (r["outcome"], r["message"]))
        results["streak"] = r["outcome"]
    return results, need_ui


# ---------- entry ----------
def main():
    reconfigure_stdout()
    parser = argparse.ArgumentParser(
        description="WorkBuddy 成长计划自动化 (API-only): 每日签到 + 领取礼物 + 派 Buddy 去旅行 + 连登奖励。")
    # task toggles
    parser.add_argument("--skip-checkin", action="store_true", help="Skip daily check-in (Buddy only)")
    parser.add_argument("--skip-buddy", action="store_true", help="Skip Buddy gift+travel (check-in only)")
    parser.add_argument("--only-checkin", action="store_true", help="Only do daily check-in")
    parser.add_argument("--only-claim", action="store_true", help="Only claim the Buddy gift")
    parser.add_argument("--only-travel", action="store_true", help="Only dispatch Buddy travel")
    parser.add_argument("--destination", choices=list(DEST_ID.keys()), default=DEFAULT_DEST,
                        help="Travel destination (default: 咖啡馆)")
    parser.add_argument("--no-redeem", action="store_true", help="不做连登里程碑自动兑换 (7/14/28天)")
    parser.add_argument("--no-lottery", action="store_true", help="不做连登奖励自动抽奖")
    parser.add_argument("--no-makeup", action="store_true", help="不做自动补签（默认按需限量使用补签卡）")
    parser.add_argument("--backend", choices=["api"], default="api",
                        help="执行后端: api(仅API, 默认)")
    args = parser.parse_args()

    if api_backend is None:
        log("ERROR: API 后端模块缺失（api_backend.py / _vendor_buddy_station.py）。")
        return 4
    try:
        _tok, _dom = api_backend.get_credentials()
    except Exception as e:
        log("[api] 无法获取本机登录态: %s" % e)
        return 4

    _res, _need = run_via_api(args)
    _code = 5 if ("fail" in (_res.get("checkin", ""), _res.get("travel", ""), _res.get("streak", ""))) else 0
    log("Done (API only). Exit code %d." % _code)
    return _code


if __name__ == "__main__":
    sys.exit(main())
