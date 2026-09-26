# API & Endpoint Notes (observed, for debugging only)

These were captured live from the WorkBuddy web app via the browser network
panel and in-page `fetch`. They are **not** a stable public contract — front-end
routes can change. Prefer clicking the UI button over hardcoding URLs; this
document exists only to help debug.

All requests go to the same origin `https://www.workbuddy.cn` and rely on the
same-site session cookie. No separate Bearer token was observed.

---

## API backend (the default `--backend auto` path)

The bundled API backend in `scripts/api_backend.py` (thin wrapper over the
vendored `scripts/_vendor_buddy_station.py`, from community skill
**totorosir-workbuddy-score v3.1.2, MIT-0**) calls the **official REST API**
directly — no browser, no Playwright, stdlib-only. It needs the WorkBuddy
**desktop login state**, which lives at:

```
C:\Users\<user>\AppData\Local\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
```

### Login token (since WorkBuddy 3.1.0: AES-256-GCM envelope, NOT plaintext JWT)

The `accessToken` field in that file is now an envelope:

```json
{ "$wbEncrypted": 1, "envelope": "<base64 aes-256-gcm blob>" }
```

The envelope key is derived from `atRestSecretKey` (a 44-char base64 value)
via SHA-256, and `atRestSecretKey` itself is discovered by one of:
- **Windows DPAPI** — unprotecting the `Local State` blob (Chromium-style
  `os_crypt`-like protection), or
- **scanning the running `WorkBuddy.exe` process memory** for the key material.

The vendored module does the read-side decryption only (it re-implements the
client's envelope unpacking). This is why the skill works **without launching
the WorkBuddy client** — it reads and decrypts the on-disk token.

> Verification note (2026-09-25): a community comment claimed the skill
> "breaks after a WorkBuddy upgrade". That referred to **pre-3.1.0** versions
> that assumed a plaintext JWT; on **WorkBuddy 5.6.2.0 / token envelope
> 3.1.2** the vendored module resolves the token correctly and the API path
> works. The **Playwright/UI path** is the more fragile one (DOM/text changes).

### REST endpoints used by the API backend

All calls carry the decrypted `accessToken` as a Bearer token.

| Method | Path (domain) | Purpose |
|---|---|---|
| POST | `/v2/billing/meter/checkin-activity-status` (www.workbuddy.cn) | Daily check-in **status** |
| POST | `/v2/billing/meter/daily-checkin` (www.workbuddy.cn) | Daily check-in **claim** — idempotent; `code 10001` = already done today |
| GET  | `/v2/activity/growth/buddy/info` (www.workbuddy.cn) | Current Buddy name / rarity |
| GET  | `/activity/growth/buddy/travel/status` (**www.workbuddy.cn, no `/v2`**) | Travel state (idle / traveling / arrived + daily_limit_reached) |
| POST | `/activity/growth/buddy/travel/depart` (no `/v2`) | Depart: body `{"location_id": N}` (1=咖啡馆 2=商场店铺 3=健身房 4=古镇客栈) |
| POST | `/activity/growth/buddy/travel/claim` (no `/v2`) | Claim returned-travel reward: body `{}` |

Travel state machine: `idle → depart → traveling → arrived → claim → idle`.
The API travel call is a no-op (and reported as success) when already in the
right state, so it is safe to run every day.

### API-first, UI-fallback decision

`auto_growth.py` runs `run_via_api()` first. For each enabled task
(checkin / gift / travel) an `outcome` of `fail` (no token, auth error, API
exception) adds that task to `need_ui`. Only those tasks are then retried via
the Playwright/UI path (`run_via_ui`). Already-completed tasks are never
repeated. `--backend api` disables the fallback (failures return non-zero);
`--backend ui` forces the original browser path.

## Endpoints seen during workbench / growth-center load (UI / in-page-fetch path)

> These are the endpoints hit by the **Playwright/UI path** (`--backend ui` /
> fallback), where the script does `page.evaluate(fetch(...))` inside an
> already-logged-in page (same-site cookie). They are **different** from the
> API backend's `/v2/...` REST calls documented above (Bearer-token, no
> browser). Keep both in mind when debugging either path.

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

> **注意（2026-09-25 实测更正）**：WorkBuddy 的 Buddy **没有「等级/level」字段**，
> 只有稀有度（SSR 等）。因此"按等级选最高的宠物"是**不成立**的，原 `detect_pets()`
> 按 `Lv.12 / 12级` 解析等级的逻辑是**基于虚构 mock 的想象，真实页面上拿不到**。
> 已改为 `detect_current_buddy()`：从页面 hero（`专属 Buddy 龙焰喵 SSR`）读出**当前展示**
> 的那只 Buddy 名，直接派它去旅行，名字不写死。关于旅行——**这是用户每天「手动」派去
> 的，并非自动出行**（旅行记录页连续多日的出行记录，正是每天手动派的结果）。出发按钮
> **只在「Buddy 在家、待派出」时出现**；Buddy 正在旅行、或待领礼物时按钮隐藏。因此脚本
> 在**找不到旅行按钮时安全跳过**（exit 0），绝不伪造派发成功。下面是实测 UI 流程。

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

### 2) 派宠物去旅行（当前展示的 Buddy）
- 脚本用 `detect_current_buddy()` 读取页面 hero 里的 Buddy 名（如 `龙焰喵`），
  点击 **「派<宠物名>去旅行」**（用户口语即"派龙焰喵去旅行"；兜底文案含
  派龙焰喵旅行 / 派猫猫旅行 / 派它去旅行 / 去旅行 等）。
- **按钮出现条件（实测）**：出发按钮**只在「Buddy 在家、待派出」时出现**。具体循环是——
  领完上一次旅行带回的礼物 → Buddy 回家 → 「派<宠物名>去旅行」按钮出现 → 手动点击派出 →
  Buddy 进入「采风中 / 距离回家」状态（此时该按钮隐藏）→ 约 2 小时回来，又产生新礼物可领。
  所以脚本在 Buddy 正在旅行、或待领礼物（按钮隐藏）时走到"未找到 → 安全跳过 exit 0"，是正常态。
- 当按钮存在时：弹出目的地选择框 **「想让 Buddy 今天去哪里逛逛？」**
  - 可选项：**咖啡馆 / 商场店铺 / 健身房 / 古镇客栈**；默认 **咖啡馆**。
- 点 **「确定派出」**（或「确认派出 / 派出」）。
- 结果：**「Buddy 正在 咖啡馆 采风中… 距离回家 HH:MM:SS」**（若没出现此提示，脚本记 WARN 并 exit 5）。

> 注：早期记录的"关闭礼物弹窗后按钮变成派猫猫旅行"在现网**已不成立**——现网领取礼物后
> 主卡不再就地变出旅行按钮；需等 Buddy 回家后，出发按钮才会重新出现，由用户手动点。脚本已按此实现。

## Button-text candidates (script matches text, not brittle handles)

- 签到领取：`领取今日礼包` / `每日签到` / `去签到` / `签到` / `领取` / `立即领取`
- 领取礼物：`领取礼物` / `收下礼物` / `领取`
- 礼物积分：`领取` / `收下` / `开心收下`
- 关闭弹窗：`关闭` / `完成` / `收下` / `好的`
- 派旅行：`派龙焰喵去旅行` / `派龙焰喵旅行` / `派猫猫旅行` / `派去旅行` / `去旅行` / `派它去旅行` / `派喵去旅行`
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
- **已在旅行中 / 待领礼物**：点不到「派去旅行」按钮（隐藏），但页面有「采风 / 距离回家」指示 → 脚本安全跳过（退出码 0）。
- **活动/按钮缺失**：签到活动 `active:false`，或旅行弹窗未渲染/文案变更 → 找不到确认按钮 → 退出码 5（告警，不静默成功）。
- **未登录**：页面渲染登录入口 → 退出码 4，提示先用 `--open` 登录一次。
- 真实「点击领取/派出」的写接口未抓到（弹窗没渲染 + 活动未激活），故脚本默认走 **点击 UI 按钮**，最稳。若抓到真实写接口，可后续增加 `--claim-api` 式参数（签到已支持）。

---

## 连续登录奖励（月历签到页：7 / 14 / 28 天里程碑兑换）— 2026-09-26 逆向实测

> 来源：官网 usercenter SPA（`/profile/growth-center` 页面 chunk 反编译 +
> 真实账号只读请求验证）。页面即「当前连续登录 N 天 / 兑换奖励 / 补签卡」那个月历页。
> 与每日签到（`/v2/billing/meter/*`）是**两套独立活动**：这里按「自然月连登天数」计算。

### 端点（域名 `www.workbuddy.cn`，无 `/v2` 前缀，仅需 Bearer Token，同旅行接口）

| Method | Path | Body | 用途 |
|---|---|---|---|
| GET  | `/activity/growth/streak` | — | 连登天数、补签卡余额、三档兑换状态（核心读） |
| GET  | `/activity/growth/redeem/summary` | — | 本月已兑次数（starter/advanced/legendary_count） |
| POST | `/activity/growth/redeem` | `{"tier":"7d\|14d\|28d","client_token":"u-<uuid>"}` | **兑换奖励**（幂等：client_token 客户端生成） |
| POST | `/activity/growth/makeup-cards/use` | `{"target_date":"YYYY-MM-DD"}` | **补签**（消耗 1 张补签卡） |
| GET  | `/activity/growth/lottery/summary` | — | 抽奖 chances / module 开关 |
| POST | `/activity/growth/lottery/draw` | `{"client_token":"u-<uuid>"}` | 抽奖（消耗 1 次机会） |
| GET  | `/activity/growth/lottery/prizes` / `draws` / `chances` / `chances/logs` / `rewards` | query `page,page_size` | 奖品/记录 |

### 档位（官方页面硬编码，与 GET streak 返回一致）

| tier | 名称 | 天数 | 奖励 |
|---|---|---|---|
| `7d`  | 入门档 | 7  | 0 积分 + 2 能量 + 1 补签卡 + 1 抽奖 |
| `14d` | 进阶档 | 14 | 50 积分 + 3 能量 + 1 补签卡 + 1 抽奖 |
| `28d` | 巅峰档 | 28 | 150 积分 + 5 能量 + 1 补签卡 + 1 抽奖 |

⚠️ 更正：totorosir 源码里 `STREAK_TIERS` 推断的「7天=+50积分」是**错的**（那是 14d 档）；
7d 档给的是 0 积分 + 2 能量 + 1 补签卡 + 1 抽奖。

### GET /activity/growth/streak 响应结构（实测 2026-09-26）

```json
{
  "streak": {"days": 26, "month_total_days": 26, "month_consumed_days": 0,
             "next_tier": "28d", "next_tier_remaining": 2, "makeup_dates": []},
  "makeup_cards": {"balance": 4, "max": 4},
  "redemption_status": {"tier_7d_count": 1, "tier_14d_count": 1, "tier_28d_count": 0,
    "tier_7d_status": "claimed", "tier_14d_status": "claimed", "tier_28d_status": "locked",
    "remaining_days": 26,
    "tiers": [{"tier":"7d","days":7,"credit":0,"energy":2,"cards":1,"chances":1}, ...]},
  "timezone": "Asia/Shanghai", "launch_date": "2026-06-17"
}
```

`tier_*_status` 取值：`claimed`（已兑）/ `claimable`（可兑）/ `locked`（未达天数）。

### 补签接口错误语义（页面源码）

- HTTP 403 + `no makeup card` → 没有补签卡（可先兑 7d 档获取）
- HTTP 400 + `future date` → 不能补未来
- HTTP 400 + `cannot makeup history month` → 仅限当前自然月
- HTTP 400 + `target date before launch` → 早于活动上线日（2026-06-17）

### 兑换/抽奖的 client_token

客户端生成：`"u-" + crypto.randomUUID()`（降级 `"u-" + 时间戳 + 随机串`），作幂等键。
服务端按 token 去重，重试安全。

### 自动化可行性结论

✅ 完全可行，纯 REST + 现有 accessToken 即可，无需浏览器。建议策略（若实现）：
- **自动兑换**：读 streak → 对所有 `status=="claimable"` 的 tier 依序 POST redeem（带随机 UUID）。
  绝不碰 `locked` 档；`claimed` 跳过。
- **自动补签**：仅当 `streak.days < 下档要求` 且断登日 ≤ 今天、当月、卡余额>0 时可选启用
  （有争议：补签是为「保连登」，是否值得花卡由用户决定，建议默认关）。
- **抽奖**：chances>0 时可自动 draw（可选）。
