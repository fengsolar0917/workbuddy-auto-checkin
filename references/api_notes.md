# API & Endpoint Notes (observed, for debugging only)

These were captured live from the WorkBuddy web app via the browser network
panel and in-page `fetch`. They are **not** a stable public contract — front-end
routes can change. Prefer clicking the UI button over hardcoding URLs; this
document exists only to help debug.

All requests go to the same origin `https://www.workbuddy.cn` and rely on the
same-site session cookie. No separate Bearer token was observed.

## Endpoints seen during workbench / growth-center load

| Method | Path | Purpose |
|---|---|---|
| POST | `/billing/meter/checkin-status` | Daily check-in **status** (core read) |
| GET  | `/activity/growth/buddy/info` | Buddy / growth center |
| GET  | `/portal/msg-center/message/summary` | Message center |

## checkin-status response shape

```json
{
  "code": 0, "msg": "OK",
  "data": {
    "active": false,
    "today_checked_in": false,
    "streak_days": 0,
    "daily_credit": 0,
    "today_credit": 0,
    "total_credits": 0,
    "week_progress": [false, false, false, false, false, false, false],
    "action_button": { "show": false, "text": "", "action": "" }
  }
}
```

Fields that matter to the automation:

- `active` — whether the check-in campaign is currently live. When `false`, the
  claim button is absent; the script must skip, not error.
- `today_checked_in` — already claimed today.
- `action_button.action` — the front-end action name executed on click. This is
  the only hint about the real claim write endpoint, but it was empty in the
  captured sample (campaign inactive). If you capture a live value, you can pass
  it via `--claim-api` to bypass UI-button hunting.

## 龙焰喵成长计划 — 实测 UI 流程（browser-skill 实跑记录）

来源：账号「无透视的梦」，Buddy = 龙焰喵 SSR。

### 入口
- 成长计划页面：`https://www.workbuddy.cn/profile/growth-center`
- 需登录态（同域会话 Cookie）。未登录时页面显示「登录 / 立即登录」。

### 1) 领取礼物
- 页面龙焰喵卡片上有按钮 **「领取礼物」**。
- 点击后弹窗：**「Buddy 满载而归啦～」**
  - 这是龙焰喵上次旅行带回的「Buddy 报告」（一份项目流产品调研预览）。
  - 弹窗内含按钮 **「领取 9 积分」**（礼物附带的积分，可选领取）与 **「关闭」**。
- 操作顺序：点「领取礼物」 → 等弹窗 → 点「领取 9 积分」(可选) → 点「关闭」。
- 关闭弹窗后，原「领取礼物」按钮**变成「派猫猫旅行」**。

### 2) 派龙焰喵去旅行
- 点击 **「派猫猫旅行」**（或类似文案：派龙焰喵旅行 / 派它去旅行 / 去旅行）。
- 弹出目的地选择框：**「想让 Buddy 今天去哪里逛逛？」**
  - 可选项：**咖啡馆 / 商场店铺 / 健身房 / 古镇客栈**
  - 默认选中 **咖啡馆**，预计出行 1~4 小时。
- 点 **「确定派出」**（或「确认派出 / 派出」）。
- 结果弹窗：**「Buddy 正在 咖啡馆 采风中… 距离回家 HH:MM:SS」**（约 2 小时后回来，回来会有新礼物可领）。

## Button-text candidates (script matches text, not brittle handles)

- 签到领取：`领取今日礼包` / `每日签到` / `去签到` / `签到` / `领取` / `立即领取`
- 领取礼物：`领取礼物` / `收下礼物` / `领取`
- 礼物积分：`领取` / `收下` / `开心收下`
- 关闭弹窗：`关闭` / `完成` / `收下` / `好的`
- 派旅行：`派猫猫旅行` / `派龙焰喵旅行` / `派去旅行` / `去旅行` / `派它去旅行` / `派喵去旅行`
- 确定派出：`确定派出` / `确认派出` / `派出` / `确定`
- 旅行中指示：`采风` / `距离回家` / `出发啦` / `正在外面`
- 未登录指示：`立即登录` / `登录` / `注册并登录`

## Why read via in-page fetch, not urllib/requests

An independent HTTP client (urllib, requests, curl) does **not** carry the
browser's logged-in session cookie. The status/claim calls must run **inside
the page** (Playwright `page.evaluate` with `fetch(..., {credentials:'include'})`)
so the cookie is attached automatically. `auto_growth.py` does this correctly.

## Capturing the real claim endpoint yourself

When the campaign is `active:true` and you click "领取今日礼包":

1. Open the workbench in a normal browser, signed in.
2. Open DevTools → Network, filter XHR.
3. Click the claim button.
4. Note the POST URL that fires (likely a sibling of `checkin-status`).
5. Re-run the script with `--claim-api "/that/path"` to claim deterministically.

Until then, the default UI-click strategy is the safe, version-tolerant choice.

## Known pitfalls

- **当天已领过签到 / 礼物**：对应按钮不存在 → 脚本安全跳过（视为 already），退出码 0。
- **已在旅行中**：点不到「派猫猫旅行」，但页面有「采风 / 距离回家」指示 → 脚本安全跳过（退出码 0）。
- **活动/按钮缺失**：签到活动 `active:false`，或旅行弹窗未渲染/文案变更 → 找不到确认按钮 → 退出码 5（告警，不静默成功）。
- **未登录**：页面渲染登录入口 → 退出码 4，提示先用 `--open` 登录一次。
- 真实「点击领取/派出」的写接口未抓到（弹窗没渲染 + 活动未激活），故脚本默认走 **点击 UI 按钮**，最稳。若抓到真实写接口，可后续增加 `--claim-api` 式参数（签到已支持）。
