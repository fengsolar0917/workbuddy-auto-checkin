# WorkBuddy 成长计划一站式自动化（workbuddy-auto-checkin）

> 一个跨平台、零硬编码的 WorkBuddy Skill：**不启动 WorkBuddy 桌面客户端**，
> 把成长计划页的一整套操作做成一个 Skill —— 打开成长计划页、**领取礼物**、
> **派龙焰喵去旅行**、以及**每日签到领积分（100 积分/天）**。
> 复用你已登录 `workbuddy.cn` 的浏览器 Profile，靠同域会话 Cookie 鉴权，无需手动提取 token。

**English summary:** A single cross-platform, zero-hardcoded WorkBuddy skill that
auto-runs the whole growth-center routine in the background — daily check-in
credits, claim the 龙焰喵 Buddy gift, and dispatch the Buddy on a trip — without
launching the WorkBuddy desktop client. It reuses an already-logged-in browser
profile and authenticates via the same-site session cookie.

---

## ✨ 特性

- **一个 Skill 完成全部**：签到 + 领礼物 + 派旅行，统一入口 `auto_growth.py`，可整体跑也可按需 `--skip-*` / `--only-*` 拆分。
- **零硬编码**：不写死用户名、路径、浏览器。自动探测操作系统（Windows / macOS / Linux）+ 已安装浏览器（Edge / Chrome / Chromium）+ 用户目录 + Profile。
- **依赖缺失不崩**：没装 Playwright 会给出明确安装命令后退出，而不是静默失败。
- **优雅降级**：活动未开启 / 今日已领 / 已领过礼物 / 已在旅行 / 未登录，全部安全跳过并写日志（退出码 0/4）。
- **跨平台调度**：内置 Windows 任务计划、macOS launchd、Linux cron 三种挂法；支持 `pythonw` 无窗口后台。
- **可复用**：直接作为 WorkBuddy Skill 复制到 `~/.workbuddy/skills/` 使用，或独立 Python 脚本运行。

## ⚙️ 原理

1. 每日签到有**专门的领取动作**，不会登录即自动到账（需手动点「领取今日礼包」）。核心接口：`POST /billing/meter/checkin-status`（读状态），领取在 Web 工作台点按钮触发。
2. 龙焰喵「领取礼物 / 派去旅行」在成长计划页（`/profile/growth-center`）点按钮触发：领礼物 → 弹窗领积分+关闭 → 按钮变「派猫猫旅行」 → 选目的地 → 确定派出 → 显示「采风中」。
3. 鉴权 = 同域会话 Cookie。脚本用 Playwright 以**持久化用户目录**启动已登录的浏览器（headless），或**附着运行中的浏览器（CDP）**，在页面内 `fetch` 读状态并点按钮——Cookie 自动带上。
4. 关键坑：不能用 `urllib`/`requests` 直打（带不上浏览器 Cookie），必须在页面内 `fetch`。

## 📦 安装 / 使用

### 方式 A：作为 WorkBuddy Skill 使用

把整个 `workbuddy-auto-checkin/` 目录复制到：

```bash
# 用户级（所有项目可用）
~/.workbuddy/skills/workbuddy-auto-checkin/

# 或项目级（团队共享）
<你的仓库>/.workbuddy/skills/workbuddy-auto-checkin/
```

WorkBuddy 下次启动自动加载，无需安装步骤。

### 方式 B：作为独立 Python 脚本运行

```bash
python -m pip install playwright
# 仅当本机没有 Edge/Chrome 时才需要下载浏览器：
python -m playwright install chromium

# 全部跑：礼物 + 旅行 + 签到
python scripts/auto_growth.py

# 附着正在运行的浏览器（推荐，不用关浏览器）
python scripts/auto_growth.py --cdp-url 127.0.0.1:9222

# 指定旅行目的地
python scripts/auto_growth.py --destination 古镇客栈

# 只做龙焰喵（跳过签到）
python scripts/auto_growth.py --skip-checkin

# 只签到
python scripts/auto_growth.py --only-checkin
```

**前提**：目标机器上至少有一个浏览器 Profile 已登录 `https://www.workbuddy.cn/`。
没登录就先 `python scripts/auto_growth.py --open` 在该 Profile 登录一次。

### 退出码（供定时任务判断是否需要告警）

| 码 | 含义 |
|---|---|
| 0 | 全部完成 / 安全跳过（已领、已旅行、活动未开启） |
| 2 | Playwright 未安装 |
| 3 | 浏览器启动失败（Profile 被占用或未装浏览器）/ CDP 附着失败 |
| 4 | 未登录（需先在该 Profile 登录一次） |
| 5 | 某步失败（按钮缺失，需关注） |

> ⚠️ 退出码 **5 不是安全跳过**，定时任务应对它单独告警，别和「已领(0)」混为一谈。

## ⏰ 定时后台调度（不启动客户端）

**Windows — 任务计划（可脱机运行，无窗口）：**

```bat
schtasks /create /tn "WorkBuddy成长计划" ^
  /tr "pythonw \"C:\path\to\scripts\auto_growth.py\"" ^
  /sc daily /st 09:00 /rl limited
```

**macOS — launchd**（建一个 plist，`StartCalendarInterval` 设 Hour=9，ProgramArguments 指向脚本）。

**Linux — cron：**

```cron
0 9 * * * /usr/bin/python3 /path/to/scripts/auto_growth.py >> /path/to/growth.log 2>&1
```

## 🛡️ 排坑清单

- **不用关浏览器**：推荐 CDP 模式（`--remote-debugging-port=9222` + `--cdp-url`）附着运行中的浏览器；默认 bot Profile 用隔离目录 `~/.workbuddy-growth-bot`，永不和你日常浏览器抢锁。
- **Profile 锁**：目标浏览器正在运行时，另一进程用同 Profile 启动会报「资料夹被占用」。给机器人用独立 Profile，或用 CDP 附着。
- **活动未开启**：`checkin-status` 返回 `active:false` 时按钮不出现，脚本会跳过（退出码 0），不会崩。
- **Cookie 失效**：Profile 退出登录 / 会话过期后领取会失败（退出码 4）。脚本写本地 `.log`，定时任务应据退出码发告警。
- **ToS 风险**：低频个人使用风险低；不要做成高频 / 多账号刷分，遵守平台规则。
- **别硬编码领取 URL**：优先点 UI 按钮；仅当你从 Network 面板抓到真实写接口时，才用 `--claim-api`。

## 🧪 本地测试（无需真实账号 / 联网）

`tests/` 内置一套自测：用本地 mock 服务器模拟 WorkBuddy 的签到接口和成长计划页，实跑 `auto_growth.py` 并断言退出码。需要 Playwright + Chromium：

```bash
cd tests
python -m venv .venv && .venv/Scripts/pip install playwright
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python run_growth_tests.py
```

最后验证结果（真实 headless Chromium，15 个场景全部通过）：

| 场景 | 设置 | 期望 | 实际 |
|---|---|---|---|
| combined_full | 礼物未领 + 签到有按钮 | 0 | 0 |
| combined_loggedout | 两页都显示登录 | 4 | 4 |
| ck_inactive | 活动未开启 | 0 | 0 |
| ck_claimed | 今日已领 | 0 | 0 |
| ck_available_button | 有按钮 | 0 | 0 |
| ck_available_nobutton | 无按钮 | 5 | 5 |
| ck_loggedout | 登录页 | 4 | 4 |
| ck_claim_api | 无按钮 + `--claim-api` | 0 | 0 |
| bd_full | 礼物未领 | 0 | 0 |
| bd_gift_claimed | 按钮已是派旅行 | 0 | 0 |
| bd_loggedout | 登录页 | 4 | 4 |
| bd_travel_fail | 派出无指示 | 5 | 5 |
| bd_only_claim / bd_only_travel / bd_only_checkin | 子任务 | 0 | 0 |

## 📤 发布到 GitHub

本目录已可直接作为 Git 仓库根：

```bash
cd workbuddy-auto-checkin
git init
git add .
git commit -m "feat: one-skill WorkBuddy growth-center automation (check-in + buddy)"
gh repo create workbuddy-auto-checkin --public --source=. --push
# 或手动：git remote add origin <你的仓库URL> && git push -u origin main
```

仓库结构：

```
workbuddy-auto-checkin/
├── SKILL.md              # Skill 元数据 + 使用说明（WorkBuddy 加载）
├── scripts/
│   └── auto_growth.py    # 统一核心脚本（零硬编码，签到+礼物+旅行）
├── references/
│   └── api_notes.md      # 实测接口与 UI 流程笔记（调试用）
├── tests/
│   ├── mock_server.py    # 模拟 WorkBuddy 签到接口 + 成长计划页
│   └── run_growth_tests.py  # 编排 15 个场景实跑并断言退出码
├── README.md             # 本文件
├── LICENSE               # MIT
└── .gitignore
```

## License

MIT — see [LICENSE](LICENSE).
