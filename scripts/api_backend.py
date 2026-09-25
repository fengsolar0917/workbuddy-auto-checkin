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
from _vendor_buddy_station import (
    load_credentials,
    do_checkin,
    travel_auto,
)
from _vendor_buddy_station import find_login_state as _find_login_state


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
