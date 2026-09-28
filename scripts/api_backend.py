# -*- coding: utf-8 -*-
"""API 后端（默认执行方案）：直接调用 WorkBuddy 官方 REST 接口，无需浏览器 / Playwright。

AtRest 登录态解密与接口调用逻辑源自社区技能
totorosir-workbuddy-score v3.1.2（MIT-0，已获公共领域 dedication），重用于本
Skill 的「API 优先」后端。重活（AES-256-GCM 信封解密、Windows DPAPI /
进程内存密钥发现）都在 _vendor_buddy_station.py（逐字节照搬、不手改，
需要更新时整体重新搬运）。本文件只做薄封装，把社区能力收敛成我们
需要的 3 个动作：签到、领旅行奖励、派 Buddy 旅行。

触发回退：当取不到 token / 鉴权失败 / 接口异常时，调用方（auto_growth.py）
会回退到 UI（Playwright）方案。因此本模块只负责「能成功就成功，不能就
如实返回失败」，不自行兜底。
"""
from __future__ import annotations

import datetime
import uuid

import _vendor_buddy_station as _V
from _vendor_buddy_station import (
    load_credentials,
    do_checkin,
    travel_auto,
)
from _vendor_buddy_station import find_login_state as _find_login_state
from _vendor_buddy_station import TRAVEL_DOMAIN as _GROWTH_DOMAIN

# 连登奖励相关端点（www.workbuddy.cn，无 /v2 前缀，同旅行接口）
STREAK_PATH = "/activity/growth/streak"
REDEEM_PATH = "/activity/growth/redeem"
REDEEM_SUMMARY_PATH = "/activity/growth/redeem/summary"
MAKEUP_PATH = "/activity/growth/makeup-cards/use"
LOTTERY_SUMMARY_PATH = "/activity/growth/lottery/summary"
LOTTERY_DRAW_PATH = "/activity/growth/lottery/draw"
HEATMAP_PATH = "/activity/growth/heatmap"

# 档位 -> 需要的连登天数（官方页面硬编码，与 GET streak 返回一致）
TIER_DAYS = {"7d": 7, "14d": 14, "28d": 28}


def _today():
    """当前日期（独立函数，便于测试替换）。"""
    return datetime.date.today()


def _client_token():
    """官方页面同款幂等令牌: "u-" + UUID4。"""
    return "u-" + str(uuid.uuid4())


def _call(token, path, method="GET", payload=None):
    """调用成长域接口，返回 (http_code, body)。经 _V.api_call 动态分发（测试可打补丁）。"""
    return _V.api_call(_GROWTH_DOMAIN, token, path, method, payload)


def _unwrap(code, body, what):
    """把 (http_code, body) 解成 data dict；失败抛 RuntimeError。"""
    if code != 200 or not isinstance(body, dict) or body.get("code") != 0:
        msg = body.get("msg") if isinstance(body, dict) else str(body)
        raise RuntimeError("%s失败: HTTP %s %s" % (what, code, msg))
    return body.get("data") or {}


def get_credentials():
    """读取本机已登录态，返回 (token, domain)。失败抛异常（调用方据此判定 API 不可用）。"""
    path = _find_login_state()
    if not path:
        raise RuntimeError("未找到 WorkBuddy 登录态文件（请先登录客户端）")
    return load_credentials(path)


def api_checkin(token, domain):
    """每日签到（幂等）。返回 {'outcome': 'ok'|'skip'|'fail', 'message': str}。"""
    try:
        res = do_checkin(domain, token)
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "fail", "message": "签到接口异常: %s" % exc}
    action = res.get("action")
    if action in ("checked_in", "skipped"):
        return {"outcome": "ok", "message": res.get("message") or action}
    if action == "failed":
        return {"outcome": "fail", "message": res.get("message") or "签到失败"}
    return {"outcome": "ok", "message": str(res)}


def api_travel(token, location_id=None, allow_claim=True, allow_depart=True):
    """派猫猫旅行闭环（先领后派）。返回 {'outcome': 'ok'|'fail', 'message': str}。"""
    try:
        result = travel_auto(token, location_id=location_id,
                              allow_depart=allow_depart, allow_claim=allow_claim)
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "fail", "message": "旅行接口异常: %s" % exc}
    if result.get("success") is False:
        return {"outcome": "fail",
                "message": "; ".join(result.get("log") or ["查询旅行状态失败"])}
    return {"outcome": "ok", "message": "; ".join(result.get("log") or ["旅行状态正常"])}


# ---------- 连登奖励（月历 7/14/28 天里程碑：兑换 + 抽奖 + 补签） ----------

def get_streak(token):
    """只读：连登状态。返回 data dict；失败抛 RuntimeError。"""
    code, body = _call(token, STREAK_PATH, "GET")
    return _unwrap(code, body, "查询连登状态")


def api_redeem(token):
    """自动兑换：对所有 status 属于可兑集合的档位依序 POST redeem（幂等 token）。

    可兑状态：官方接口实测返回 `available`（达成天数，待领取）；社区代码里也
    出现过 `claimable`。两者等价处理。绝不碰 `locked`（未达天数）与
    `claimed`（已兑）。返回 {'outcome','message','redeemed': [tier]}。
    """
    try:
        data = get_streak(token)
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "fail", "message": "兑换: %s" % exc, "redeemed": []}
    rs = data.get("redemption_status") or {}
    redeemed, logs, failed = [], [], False
    for tier in ("7d", "14d", "28d"):
        status = rs.get("tier_%s_status" % tier)
        if status == "claimed":
            logs.append("%s已兑过" % tier)
        elif status == "locked":
            logs.append("%s未达成(差%d天)" % (tier, max(0, TIER_DAYS[tier] - (rs.get("remaining_days") or 0))))
        elif status in ("claimable", "available"):
            payload = {"tier": tier, "client_token": _client_token()}
            code, body = _call(token, REDEEM_PATH, "POST", payload)
            if code == 200 and isinstance(body, dict) and body.get("code") == 0:
                redeemed.append(tier)
                logs.append("%s兑换成功" % tier)
            else:
                failed = True
                msg = body.get("msg") if isinstance(body, dict) else str(body)
                logs.append("%s兑换失败: %s" % (tier, msg))
        else:
            logs.append("%s状态未知(%s)" % (tier, status))
    if not logs:
        logs.append("无可兑换档位")
    return {"outcome": "fail" if failed else "ok",
            "message": "兑换: " + "; ".join(logs), "redeemed": redeemed}


def api_lottery(token, max_draws=10):
    """自动抽奖：chances>0 且模块开启时逐次 draw（带幂等 token，封顶 max_draws 防失控）。"""
    try:
        code, body = _call(token, LOTTERY_SUMMARY_PATH, "GET")
        data = _unwrap(code, body, "查询抽奖次数")
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "fail", "message": "抽奖: %s" % exc, "drawn": 0}
    module = data.get("module")
    if module is not None and not module.get("enabled", True):
        return {"outcome": "ok", "message": "抽奖: 模块未开启，跳过", "drawn": 0}
    chances = data.get("chances") or 0
    if chances <= 0:
        return {"outcome": "ok", "message": "抽奖: 无可用次数，跳过", "drawn": 0}
    drawn, logs, failed = 0, [], False
    for _ in range(min(chances, max_draws)):
        code, body = _call(token, LOTTERY_DRAW_PATH, "POST", {"client_token": _client_token()})
        if code == 200 and isinstance(body, dict) and body.get("code") == 0:
            drawn += 1
            prize = (body.get("data") or {}).get("prize") or {}
            logs.append(str(prize.get("name") or "第%d次" % drawn))
        else:
            failed = True
            msg = body.get("msg") if isinstance(body, dict) else str(body)
            logs.append("第%d次失败: %s" % (drawn + 1, msg))
            break
    return {"outcome": "fail" if failed else "ok",
            "message": "抽奖: %d/%d 次 [%s]" % (drawn, chances, "; ".join(logs)),
            "drawn": drawn}


def api_makeup(token):
    """自动补签：仅当仍有 locked 档位且补签可促成时，用补签卡填当月断登日。

    消耗稀缺资源，默认按「刚好够到下一档」限量使用，绝不超花。
    """
    try:
        data = get_streak(token)
        rs = data.get("redemption_status") or {}
        locked = [t for t in ("7d", "14d", "28d") if rs.get("tier_%s_status" % t) == "locked"]
        if not locked:
            return {"outcome": "ok", "message": "补签: 无未达成档位，无需补签", "used": 0}
        streak = data.get("streak") or {}
        days = streak.get("days") or 0
        need = min(TIER_DAYS[t] for t in locked) - days
        if need <= 0:
            return {"outcome": "ok", "message": "补签: 天数已达最近未达成档，无需补签", "used": 0}
        balance = (data.get("makeup_cards") or {}).get("balance") or 0
        if balance <= 0:
            return {"outcome": "ok", "message": "补签: 无补签卡，跳过", "used": 0}
        # 找断登日
        launch = data.get("launch_date") or "2026-06-17"
        made = set(streak.get("makeup_dates") or [])
        today = _today()
        month = today.strftime("%Y-%m")
        code, body = _call(token, HEATMAP_PATH, "GET")
        hm = _unwrap(code, body, "查询活跃热力图")
        broken = []
        for cell in hm.get("cells") or []:
            d = cell.get("date") or ""
            if d.startswith(month) and d < today.isoformat() and d >= launch \
                    and not (cell.get("score") or 0) and d not in made:
                broken.append(d)
        if not broken:
            return {"outcome": "ok", "message": "补签: 当月无断登日，跳过", "used": 0}
        use = min(balance, need, len(broken))
        used, logs, failed = 0, [], False
        for d in broken[:use]:
            code, body = _call(token, MAKEUP_PATH, "POST", {"target_date": d})
            if code == 200 and isinstance(body, dict) and body.get("code") == 0:
                used += 1
                logs.append(d)
            else:
                failed = True
                msg = body.get("msg") if isinstance(body, dict) else str(body)
                logs.append("%s失败: %s" % (d, msg))
                break
        return {"outcome": "fail" if failed else "ok",
                "message": "补签: 用%d张卡 [%s]" % (used, "; ".join(logs)) if used or failed
                           else "补签: 未使用（配额=%d, 需要=%d, 断登=%d）" % (balance, need, len(broken)),
                "used": used}
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "fail", "message": "补签: %s" % exc, "used": 0}


def api_streak_all(token, do_redeem=True, do_lottery=True, do_makeup=True):
    """连登奖励总入口：兑换 -> 抽奖 -> 补签。任一子任务失败则整体 outcome=fail。"""
    subs = []
    if do_redeem:
        subs.append(api_redeem(token))
    if do_lottery:
        subs.append(api_lottery(token))
    if do_makeup:
        subs.append(api_makeup(token))
    if not subs:
        return {"outcome": "ok", "message": "连登任务全部关闭"}
    failed = any(s["outcome"] == "fail" for s in subs)
    return {"outcome": "fail" if failed else "ok",
            "message": " | ".join(s["message"] for s in subs)}
