# API & Endpoint Notes (observed, for debugging only)

> 2026-10-09 清理：UI 回退层（Playwright / 按钮文案匹配）已整体移除，本文档中
> `--backend ui` / `--open` / `--claim-api` 等参数与按钮文案表均已删除。
> 当前技能为**纯 API 实现**；「旅行循环的行为语义」一节为历史 UI 实测观察，
> 保留仅供理解 API 状态机。

These were captured live from the WorkBuddy web app via the browser network
panel and in-page `fetch`. They are **not** a stable public contract — front-end
routes can change. The skill now calls the official REST endpoints directly
(API-only); this document exists only to help debug the endpoint contracts.

All requests go to the same origin `https://www.workbuddy.cn` and rely on the
same-site session cookie. No separate Bearer token was observed.

---

## API backend (the API-only path)

The bundled API backend in `scripts/api_backend.py` (thin wrapper over the
vendored `scripts/_vendor_buddy_station.py`, from community skill
**totorosir-workbuddy-score v3.1.2, MIT-0**) calls the **official REST API**
directly — no browser, no Playwright, stdlib-only. It needs the WorkBuddy
**desktop login state**, which lives at:

登录态文件是 WorkBuddy 桌面端写入的 `workbuddy-desktop.info`（AES-256-GCM 信封）。
**Windows 实测路径（2026-10-09 验证）**：
`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`
——CodeBuddy 与 WorkBuddy 同源、共用该扩展宿主目录，**这不是"另一款产品的干扰"**
（本文档早前版本的"澄清"是错的，当时基于沙箱内文件搜索不可见得出）。脚本自动探测，
不要硬编码绝对路径。登录态内 `domain` 字段官方签发为 `www.codebuddy.cn`，签到接口
在该域实测可用：`www.codebuddy.cn` 与 `www.workbuddy.cn` 后端同源，token 通用。

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
  captured sample (campaign inactive). The API backend claims via
  `POST /v2/billing/meter/daily-checkin` directly, so no UI hint is needed.

## 旅行循环的行为语义（历史 UI 实测观察，供理解 API 状态机）

来源：某账号（Buddy 为 SSR 稀有度）2026-09-25 实跑记录。UI 层虽已移除，
以下行为语义对理解 `travel/status → depart → claim` 状态机仍有价值：

> **注意（2026-09-25 实测更正）**：WorkBuddy 的 Buddy **没有「等级/level」字段**，
> 只有稀有度（SSR 等）。"按等级选最高的宠物"不成立；API 路径直接派当前 Buddy，
> 无需也不应解析等级。

- 旅行是用户每天**手动**派出的循环，Buddy 不会自动出行（旅行记录页的连续出行
  记录正是每天手动派的结果）。
- 循环：领走上次旅行带回的礼物 → Buddy 回家、进入可派出状态 → 派出 →
  「采风中 / 距离回家」（约 2 小时）→ 回家并产生新礼物。
- 对应 API 状态机：`idle → depart → traveling → arrived → claim → idle`。
  Buddy 正在旅行或待领礼物时不可派出；脚本在这些状态下安全跳过（exit 0），
  绝不伪造派发成功。
- 目的地 4 个：咖啡馆 / 商场店铺 / 健身房 / 古镇客栈（location_id 1–4）。
- 派出成功标志：状态变为「正在 <目的地> 采风中… 距离回家 HH:MM:SS」。

## Known pitfalls（API-only 现况）

- **当天已签过 / 已领过 / 已在旅行**：接口幂等返回对应状态 → 安全跳过，退出码 0。
- **活动未开启**：签到活动 `active:false` → 安全跳过，退出码 0。
- **未登录 / 登录态解密失败**：退出码 4——先在 WorkBuddy 桌面端登录一次再跑。
- **任一步 API 失败**（网络、鉴权、接口变更）：退出码 5，定时任务应据此告警，
  不要与「已领（0）」混为一谈。

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

`tier_*_status` 取值：`claimed`（已兑）/ `available`（达成天数、待领取，实测值）/ `claimable`（同 available，社区代码中亦出现）/ `locked`（未达天数）。**自动化需把 `available` 与 `claimable` 一并视为可兑**。

### 补签接口错误语义（页面源码）

- HTTP 403 + `no makeup card` → 没有补签卡（可先兑 7d 档获取）
- HTTP 400 + `future date` → 不能补未来
- HTTP 400 + `cannot makeup history month` → 仅限当前自然月
- HTTP 400 + `target date before launch` → 早于活动上线日（2026-06-17）

### 兑换/抽奖的 client_token

客户端生成：`"u-" + crypto.randomUUID()`（降级 `"u-" + 时间戳 + 随机串`），作幂等键。
服务端按 token 去重，重试安全。

### 自动化可行性结论

✅ 完全可行，纯 REST + 现有 accessToken 即可，无需浏览器。已实现策略：
- **自动兑换**（默认开）：读 streak → 对所有 `status in ("available","claimable")` 的 tier
  依序 POST redeem（带随机 UUID client_token）。绝不碰 `locked` 档；`claimed` 跳过。
- **自动补签**（**默认关**，`--makeup` 显式开启）：补签卡是稀缺资源（每月最多 4 张），
  是否花卡由用户决定。开启后仅当断登日 ≤ 今天、当月、卡余额 > 0 时，
  按「刚好够到下一档」限量使用，绝不超花。
- **执行顺序**：补签 → 兑换 → 抽奖。补签可能解锁新档位，必须最先执行，
  保证当月解锁的档位在同一次运行内兑掉（月末场景不过夜）。
- **抽奖**（默认开）：chances > 0 时逐次 draw，封顶 10 次防失控。
