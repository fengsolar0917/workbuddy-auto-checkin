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
