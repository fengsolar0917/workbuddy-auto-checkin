#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 成长计划一站式自动化 (cross-platform / zero hardcoded paths).

把用户最早要求的一整套操作做成一个 Skill:
  1. 打开成长计划页面 (https://www.workbuddy.cn/profile/growth-center)
  2. 领取礼物  (Buddy 旅行带回的报告 + 积分)
  3. 派「当前展示的 Buddy」去旅行 (页面主卡正在展示的那只宠物, 选目的地, 确定派出)
      —— WorkBuddy 的 Buddy 没有"等级"字段, 只有稀有度(如 SSR); "按等级选最高"不成立.
         故改为直接派当前展示的 Buddy 出行, 不写死名字, 换号也能用.
  4. 每日签到领积分 (100 积分/天, 第 7 天 1000)  —— 同一套操作里的另一件事

通过复用已登录的浏览器 Profile (或附着运行中的浏览器) 继承登录态,
全程不启动 WorkBuddy 桌面客户端。

三种运行模式 (都不必关闭日常浏览器):
  1. CDP 附着 (推荐): 浏览器用 --remote-debugging-port=9222 启动后,
     `python auto_growth.py --cdp-url 127.0.0.1:9222` 直接附着, 不抢锁.
  2. 独立 bot profile (默认): 用隔离目录 ~/.workbuddy-growth-bot, 永不冲突,
     首次用 `--open` 登录一次.
  3. 显式复用真实 Profile (--user-data-dir + --profile): 仅当该浏览器关闭时.

依赖:
    pip install playwright
    python -m playwright install chromium   # 仅当本机无 Edge/Chrome

用法:
    python auto_growth.py                                  # 全部: 礼物+旅行+签到
    python auto_growth.py --cdp-url 127.0.0.1:9222         # 附着运行中的浏览器
    python auto_growth.py --open                            # 首次可见窗口登录一次
    python auto_growth.py --destination 古镇客栈            # 指定旅行目的地
    python auto_growth.py --skip-checkin                    # 只做当前 Buddy(礼物+旅行)
    python auto_growth.py --only-claim                      # 只领礼物
    python auto_growth.py --only-travel                     # 只派旅行
    python auto_growth.py --only-checkin                    # 只签到
"""

import argparse
import json
import platform
import sys
from datetime import datetime
from pathlib import Path

WORKBUDDY_BASE = "https://www.workbuddy.cn/"
GROWTH_URL = WORKBUDDY_BASE + "profile/growth-center"
CHECKIN_URL = WORKBUDDY_BASE  # 工作台(签到入口在此)

# 每日签到
CHECKIN_STATUS_API = "/billing/meter/checkin-status"
CLAIM_TEXTS = ["领取今日礼包", "每日签到", "去签到", "签到", "领取", "立即领取"]

# 龙焰喵: 领取礼物
GIFT_CLAIM_TEXTS = ["领取礼物", "收下礼物", "领取"]
GIFT_POINTS_TEXTS = ["领取", "收下", "开心收下"]
GIFT_CLOSE_TEXTS = ["关闭", "完成", "收下", "好的"]
# 派去旅行: 触发按钮文案候选. 优先用「派{当前Buddy名}旅行」(从页面解析),
# 以下为兜底/历史文案. 注意: WorkBuddy 当前版本主卡上通常没有常驻旅行按钮
# (Buddy 多为每日自动出行); 找不到时安全跳过, 不报错.
TRAVEL_BTN_TEXTS = ["派猫猫旅行", "派龙焰喵旅行", "派去旅行", "去旅行", "派它去旅行", "派喵去旅行"]
TRAVEL_CONFIRM_TEXTS = ["确定派出", "确认派出", "派出", "确定"]
TRAVELING_INDICATORS = ["采风", "距离回家", "出发啦", "正在外面", "已出发"]
DESTINATIONS = ["咖啡馆", "商场店铺", "健身房", "古镇客栈"]
DEFAULT_DEST = "咖啡馆"

NOT_LOGIN_TEXTS = ["立即登录", "登录", "注册并登录"]

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


# ---------- portable browser framework (no hardcoded user/path) ----------
def default_bot_profile_dir():
    return Path.home() / ".workbuddy-growth-bot"


def candidate_user_data_dirs():
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
        dirs = [app / "Microsoft Edge", app / "Google" / "Chrome"]
    else:
        dirs = [
            home / ".config" / "microsoft-edge",
            home / ".config" / "google-chrome",
        ]
    return [d for d in dirs if d.exists() and d.is_dir()]


def browser_channel_for(dir_path):
    name = dir_path.as_posix().lower()
    if "edge" in name:
        return "msedge"
    if "chrome" in name:
        return "chrome"
    return None


def detect_environment(args):
    # Mode 1: attach to a running browser via CDP
    if args.cdp_url:
        url = args.cdp_url if "://" in args.cdp_url else "http://" + args.cdp_url
        return {"mode": "cdp", "cdp_url": url}

    # Mode 3: explicit reuse of an existing profile
    if args.user_data_dir:
        ud = Path(args.user_data_dir).expanduser()
        if not ud.exists():
            log("ERROR: --user-data-dir not found: %s" % ud)
            return None
        channel = None
        if args.browser:
            channel = {"edge": "msedge", "chrome": "chrome", "chromium": None}.get(args.browser)
        if channel is None and args.browser != "chromium":
            channel = browser_channel_for(ud)
        return {"mode": "persistent", "user_data_dir": ud,
                "profile": args.profile or "Default", "channel": channel}

    # Mode 2 (default): dedicated, isolated bot profile — never locks your browser
    bot = (Path(args.bot_profile_dir).expanduser()
           if args.bot_profile_dir else default_bot_profile_dir())
    return {"mode": "persistent", "user_data_dir": bot,
            "profile": "Default", "channel": None}


# ---------- UI helpers ----------
def click_by_text(page, candidates, timeout=6000, prefix=""):
    """Click the first visible element whose text contains any candidate.
    Returns the matched text, or None."""
    for txt in candidates:
        try:
            loc = page.get_by_text(txt, exact=False).first
            if loc.count() > 0 and loc.is_visible(timeout=timeout):
                loc.click(timeout=timeout)
                log("%sclicked '%s'" % (prefix, txt))
                return txt
        except Exception:
            continue
    return None


def has_text(page, candidates, timeout=3000):
    for txt in candidates:
        try:
            loc = page.get_by_text(txt, exact=False).first
            if loc.count() > 0 and loc.is_visible(timeout=timeout):
                return txt
        except Exception:
            continue
    return None


def is_logged_out(page):
    return has_text(page, NOT_LOGIN_TEXTS, timeout=2500) is not None


# ---------- task 1: daily check-in ----------
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
        # non-JSON (e.g. HTML login page) means not logged in
        return None, "non-json-response:%s" % text[:60]


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


def run_checkin(page, args, prefix=""):
    """Read status, optionally claim, verify. Returns code (0/4/5)."""
    page.goto(args.checkin_url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(5000)

    if is_logged_out(page):
        log("%s[checkin] ERROR: not logged in to workbuddy.cn in this profile." % prefix)
        log("%s[checkin] Action: run with --open once to sign in, then retry." % prefix)
        return 4

    data, err = get_status_via_page(page)
    if err:
        log("%s[checkin] ERROR: Cannot read check-in status (%s). Not logged in or API changed." % (prefix, err))
        return 4

    active = data.get("active")
    checked = data.get("today_checked_in")
    log("%s[checkin] Status: active=%s today_checked_in=%s" % (prefix, active, checked))

    if active is False:
        log("%s[checkin] INFO: Check-in activity is inactive now. Skipping safely." % prefix)
        return 0
    if checked is True:
        log("%s[checkin] INFO: Already claimed today. Skipping." % prefix)
        return 0

    claimed = False
    if args.claim_api:
        resp, e = post_claim_via_page(page, args.claim_api)
        if e:
            log("%s[checkin] WARN: claim API call failed: %s" % (prefix, e))
        else:
            log("%s[checkin] Claim API responded: %s" % (prefix, str(resp)[:200]))
            claimed = True
    else:
        clicked = click_by_text(page, CLAIM_TEXTS, prefix=prefix)
        if clicked:
            log("%s[checkin] OK: clicked claim button '%s'" % (prefix, clicked))
            claimed = True
        else:
            log("%s[checkin] WARN: No claim button found on page (maybe UI changed)." % prefix)

    # verify
    data_after, _ = get_status_via_page(page)
    if data_after and data_after.get("today_checked_in") is True:
        claimed = True
        log("%s[checkin] Verified: today_checked_in=true" % prefix)
    elif not claimed:
        log("%s[checkin] WARN: Claim not confirmed. Check login state / activity / UI." % prefix)
        return 5
    return 0


# ---------- task 2 + 3: 龙焰喵 领取礼物 + 派去旅行 ----------
def claim_gift(page, prefix):
    """Return 'claimed' / 'already' / 'failed'."""
    btn = click_by_text(page, GIFT_CLAIM_TEXTS, prefix=prefix)
    if btn is None:
        # 可能当天已领过, 按钮已是「派猫猫旅行」, 礼物视为已处理
        log("%s[gift] no '领取礼物' button (maybe already claimed). Skipping." % prefix)
        return "already"

    page.wait_for_timeout(2500)
    # 弹窗: 领取积分 (可选) + 关闭
    pts = click_by_text(page, GIFT_POINTS_TEXTS, prefix=prefix)
    if pts:
        page.wait_for_timeout(1500)
    # 关闭弹窗 (尝试多种关闭文案)
    closed = click_by_text(page, GIFT_CLOSE_TEXTS, prefix=prefix)
    if not closed:
        page.wait_for_timeout(1000)
        click_by_text(page, ["关闭", "完成", "好的"], prefix=prefix)
    page.wait_for_timeout(1500)
    log("%s[gift] gift claimed (points=%s)." % (prefix, bool(pts)))
    return "claimed"


def detect_current_buddy(page):
    """从成长计划页面解析当前主卡展示的 Buddy 名字.

    WorkBuddy 的 Buddy 没有"等级"字段, 只有稀有度(SSR 等). 因此不再按等级挑选,
    而是直接取页面上正在展示的那只 (hero 文案形如 '专属 Buddy 龙焰喵 SSR').
    返回名字字符串, 或 None.
    """
    js = r"""
    (function(){
      try {
        var t = (document.body.innerText || '');
        var m = t.match(/专属\s*Buddy\s*([一-龥]{1,6}喵)/);
        if (m) return m[1];
        var m2 = t.match(/([一-龥]{1,6}喵)/);
        return m2 ? m2[1] : null;
      } catch(e) { return null; }
    })()
    """
    try:
        return page.evaluate(js)
    except Exception as e:
        log("[buddy] detect_current_buddy failed: %s" % e)
        return None


def dispatch_travel(page, args, prefix):
    """派当前页面展示的 Buddy 去旅行 (Plan A).

    返回:
      'dispatched'  —— 成功派出
      'already'     —— 已在旅行 (页面有'采风中/距离回家'等提示)
      'skipped'     —— 页面上没有派去旅行的按钮 (当前账号的 Buddy 多为每日自动
                       出行 / 冷却中). 这是正常态, 不是错误, 退出码按 0 处理.
      'failed'      —— 点到了按钮但确认/派出行提示缺失 (UI 可能已变)
    """
    name = detect_current_buddy(page)
    if name:
        log("%s[travel] current displayed Buddy = %r" % (prefix, name))
    else:
        log("%s[travel] could not read current Buddy name from page." % prefix)

    # 触发按钮候选: 优先 '派{name}旅行', 再兜底通用文案
    candidates = []
    if name:
        candidates.append("派%s旅行" % name)
    candidates += TRAVEL_BTN_TEXTS

    btn = click_by_text(page, candidates, timeout=6000, prefix=prefix)
    if btn is None:
        if has_text(page, TRAVELING_INDICATORS, timeout=2500):
            log("%s[travel] already traveling (indicator found). Skipping." % prefix)
            return "already"
        log("%s[travel] INFO: no '派去旅行' button on page "
            "(当前账号的 Buddy 可能为每日自动出行/处于冷却). 安全跳过, 不报错." % prefix)
        return "skipped"

    page.wait_for_timeout(2500)
    # 选目的地 (若指定且非默认, 先点对应选项; 否则默认咖啡馆已选中)
    dest = args.destination or DEFAULT_DEST
    if dest != DEFAULT_DEST and dest in DESTINATIONS:
        picked = click_by_text(page, [dest], prefix=prefix)
        if picked:
            log("%s[travel] destination selected: %s" % (prefix, dest))
        page.wait_for_timeout(800)
    # 确定派出
    confirm = click_by_text(page, TRAVEL_CONFIRM_TEXTS, prefix=prefix)
    if not confirm:
        log("%s[travel] WARN: no confirm button found after travel dialog." % prefix)
        return "failed"
    page.wait_for_timeout(2500)
    # 验证
    if has_text(page, TRAVELING_INDICATORS, timeout=4000):
        label = name or btn
        log("%s[travel] dispatched! %s is now traveling (indicator found)." % (prefix, label))
        return "dispatched"
    log("%s[travel] WARN: confirm clicked but no traveling indicator." % prefix)
    return "failed"


def run_buddy(page, args, prefix="", do_gift=True, do_travel=True):
    """Execute the growth-center Buddy tasks. Returns code (0/4/5)."""
    page.goto(args.growth_url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(4000)

    if is_logged_out(page):
        log("%s[buddy] ERROR: not logged in to workbuddy.cn in this profile." % prefix)
        log("%s[buddy] Action: run with --open once to sign in, then retry." % prefix)
        return 4

    summary = {"gift": "skipped", "travel": "skipped"}
    if do_gift:
        summary["gift"] = claim_gift(page, prefix)
    else:
        log("%s[buddy] [skip-gift] skipping gift claim." % prefix)

    if do_travel:
        summary["travel"] = dispatch_travel(page, args, prefix)
    else:
        log("%s[buddy] [skip-travel] skipping travel dispatch." % prefix)

    if summary["gift"] == "failed" or summary["travel"] == "failed":
        return 5
    return 0


# ---------- overall orchestration ----------
def run_tasks(page, args):
    """Run whichever tasks are enabled; return overall exit code."""
    # resolve task switches
    skip_buddy = args.skip_buddy or args.only_checkin
    skip_checkin = args.skip_checkin or args.only_claim or args.only_travel
    do_gift = (not skip_buddy) and (not args.only_travel)
    do_travel = (not skip_buddy) and (not args.only_claim)
    do_checkin = not skip_checkin

    codes = []
    if do_checkin:
        log("[task] daily check-in ...")
        codes.append(run_checkin(page, args, prefix="[ck] "))
    if do_gift or do_travel:
        log("[task] 宠物 Buddy (gift=%s travel=%s, 派当前展示的 Buddy) ..." % (do_gift, do_travel))
        codes.append(run_buddy(page, args, prefix="[bd] ", do_gift=do_gift, do_travel=do_travel))

    if not codes:
        log("WARN: no task selected (all skipped). Nothing to do.")
        return 0
    if 5 in codes:
        return 5
    if 4 in codes:
        return 4
    return 0


# ---------- entry ----------
def main():
    reconfigure_stdout()
    parser = argparse.ArgumentParser(
        description="WorkBuddy 成长计划一站式自动化: 每日签到 + 领取礼物 + 派当前展示的 Buddy 去旅行 (headless, no client).")
    # browser framework
    parser.add_argument("--browser", choices=["edge", "chrome", "chromium"],
                        help="Force a browser; default = auto-detect")
    parser.add_argument("--user-data-dir", help="[mode 3] explicit User Data dir (folder containing 'Default')")
    parser.add_argument("--profile", default="Default", help="[mode 3] profile name")
    parser.add_argument("--bot-profile-dir", help="[mode 2] override the default isolated bot profile dir")
    parser.add_argument("--cdp-url", help="[mode 1] attach to a running browser, e.g. 127.0.0.1:9222")
    parser.add_argument("--open", action="store_true",
                        help="Open the resolved profile in a visible window to log in once, then exit")
    # task toggles
    parser.add_argument("--skip-checkin", action="store_true", help="Skip daily check-in (Buddy only)")
    parser.add_argument("--skip-buddy", action="store_true", help="Skip Buddy gift+travel (check-in only)")
    parser.add_argument("--only-checkin", action="store_true", help="Only do daily check-in")
    parser.add_argument("--only-claim", action="store_true", help="Only claim the Buddy gift")
    parser.add_argument("--only-travel", action="store_true", help="Only dispatch Buddy travel")
    parser.add_argument("--destination", choices=DESTINATIONS, default=DEFAULT_DEST,
                        help="Travel destination (default: 咖啡馆)")
    parser.add_argument("--claim-api", help="Explicit check-in claim endpoint to POST instead of clicking UI")
    # url overrides
    parser.add_argument("--url", default=None, help="Override base URL for BOTH growth and check-in pages")
    parser.add_argument("--growth-url", default=None, help="Growth-center URL override")
    parser.add_argument("--checkin-url", default=None, help="Check-in (workbench) URL override")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect env + open page + report login state, no action")
    args = parser.parse_args()

    # resolve urls
    base = args.url or WORKBUDDY_BASE
    args.growth_url = args.growth_url or (base + "profile/growth-center")
    args.checkin_url = args.checkin_url or base

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

    # manual login setup (--open)
    if args.open:
        kwargs = dict(headless=False,
                      args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        if env["mode"] == "persistent":
            user_dir = str(Path(env["user_data_dir"]) / env["profile"])
            kwargs["user_data_dir"] = user_dir
            if env.get("channel"):
                kwargs["channel"] = env["channel"]
            log("Opening profile for manual login: %s" % user_dir)
        else:
            log("ERROR: --open is not meaningful with --cdp-url (browser already running).")
            return 2
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(**kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.growth_url, wait_until="networkidle", timeout=60000)
            log("Please sign in to workbuddy.cn in this window, then close it.")
            try:
                input("Press Enter after signing in (or just close the window)...")
            except EOFError:
                pass
            context.close()
        log("Setup done. Now run without --open (headless).")
        return 0

    if args.dry_run:
        log("DRY-RUN mode. Resolved environment:")
        log("  mode        = %s" % env["mode"])
        if env["mode"] == "cdp":
            log("  cdp_url     = %s" % env["cdp_url"])
        else:
            log("  profile     = %s" % (Path(env["user_data_dir"]) / env["profile"]))
            log("  channel     = %s" % (env.get("channel") or "bundled-chromium"))
        log("  growth_url  = %s" % args.growth_url)
        log("  checkin_url = %s" % args.checkin_url)
        with sync_playwright() as p:
            if env["mode"] == "cdp":
                try:
                    browser = p.chromium.connect_over_cdp(env["cdp_url"])
                except Exception as e:
                    log("ERROR: cannot attach CDP: %s" % e)
                    return 3
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                page.goto(args.growth_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                logged = is_logged_out(page)
                log("  login state = %s" % ("LOGGED OUT — run --open first" if logged else "logged in"))
                try:
                    page.close()
                except Exception:
                    pass
            else:
                user_dir = str(Path(env["user_data_dir"]) / env["profile"])
                kwargs = dict(user_data_dir=user_dir, headless=True,
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
                if env.get("channel"):
                    kwargs["channel"] = env["channel"]
                try:
                    context = p.chromium.launch_persistent_context(**kwargs)
                except Exception as e:
                    log("ERROR: cannot launch: %s" % e)
                    return 3
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.growth_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                logged = is_logged_out(page)
                log("  login state = %s" % ("LOGGED OUT — run --open first" if logged else "logged in"))
                context.close()
        log("DRY-RUN: no action. Exiting.")
        return 0

    # real run
    code = 0
    with sync_playwright() as p:
        if env["mode"] == "cdp":
            try:
                browser = p.chromium.connect_over_cdp(env["cdp_url"])
            except Exception as e:
                log("ERROR: Cannot attach via CDP (%s)." % e)
                log("Tip: start Edge/Chrome with --remote-debugging-port=9222 and retry.")
                return 3
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                code = run_tasks(page, args)
            finally:
                try:
                    page.close()  # only our tab; user's browser keeps running
                except Exception:
                    pass
            log("Done (CDP attach). Your browser was left running.")
            return code

        # Mode 2/3: persistent profile launch
        user_dir = str(Path(env["user_data_dir"]) / env["profile"])
        kwargs = dict(
            user_data_dir=user_dir,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        if env.get("channel"):
            kwargs["channel"] = env["channel"]
        try:
            context = p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            log("ERROR: Browser launch failed (profile locked or missing?): %s" % e)
            if env.get("channel"):
                log("Tip: close the browser using this profile, or use --cdp-url to attach instead.")
            else:
                log("Tip: the bot profile should be free; if another copy is running, wait/retry.")
            return 3
        try:
            page = context.pages[0] if context.pages else context.new_page()
            code = run_tasks(page, args)
        finally:
            try:
                context.close()
            except Exception:
                pass

    log("Done. Exit code %d." % code)
    return code


if __name__ == "__main__":
    sys.exit(main())
