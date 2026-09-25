# WorkBuddy 每日签到自动领取（workbuddy-auto-checkin）

> 一个跨平台、零硬编码的 WorkBuddy Skill：在**不启动 WorkBuddy 桌面客户端**的前提下，
> 定时后台自动领取「每日签到 / 领取今日礼包」积分（第 1–6 天 100 积分/天，第 7 天 1000 积分）。
> 复用你已登录 `workbuddy.cn` 的浏览器 Profile，靠同域会话 Cookie 鉴权，无需手动提取 token。

**English summary:** A cross-platform, zero-hardcoded WorkBuddy skill that
auto-claims daily check-in credits in the background without launching the
WorkBuddy desktop client. It reuses an already-logged-in browser profile and
authenticates via the same-site session cookie.

---

## ✨ 特性

- **零硬编码**：不写死用户名、路径、浏览器。自动探测操作系统（Windows / macOS / Linux）+ 已安装浏览器（Edge / Chrome / Chromium）+ 用户目录 + Profile。
- **依赖缺失不崩**：没装 Playwright 会给出明确安装命令后退出，而不是静默失败。
- **优雅降级**：活动未开启 / 今日已领 / 未登录 / Profile 被占用，全部安全跳过并写日志。
- **跨平台调度**：内置 Windows 任务计划、macOS launchd、Linux cron 三种挂法。
- **可复用**：直接作为 WorkBuddy Skill 复制到 `~/.workbuddy/skills/` 使用，或独立 Python 脚本运行。

## ⚙️ 原理

1. WorkBuddy 的每日签到有**专门的领取动作**，不会登录即自动到账（需手动点「领取今日礼包」）。
2. 核心接口：`POST /billing/meter/checkin-status`（读状态）。领取动作在 Web 工作台点按钮触发。
3. 鉴权 = 同域会话 Cookie。脚本用 Playwright 以**持久化用户目录**启动已登录的浏览器（headless），在页面内 `fetch` 读状态并点领取按钮——Cookie 自动带上。
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

# 全自动探测运行
python scripts/auto_checkin.py

# 指定浏览器 / Profile / 显式领取接口
python scripts/auto_checkin.py --browser edge
python scripts/auto_checkin.py --user-data-dir "C:/Users/你/AppData/Local/Microsoft/Edge/User Data" --profile "Profile 1"
python scripts/auto_checkin.py --claim-api "/billing/meter/checkin"
python scripts/auto_checkin.py --dry-run     # 只检测环境与登录态，不领取
```

**前提**：目标机器上至少有一个浏览器 Profile 已登录 `https://www.workbuddy.cn/`。没登录就先手动登录一次（关闭浏览器后脚本会复用该 Profile）。

### 退出码（供定时任务判断是否需要告警）

| 码 | 含义 |
|---|---|
| 0 | 领取成功 / 安全跳过（已领或活动未开启） |
| 2 | Playwright 未安装 |
| 3 | 浏览器启动失败（Profile 被占用或未装浏览器） |
| 4 | 未登录（需先在该 Profile 登录一次） |
| 5 | 领取未确认（检查登录态 / 活动 / UI） |

## ⏰ 定时后台调度（不启动客户端）

**Windows — 任务计划（可脱机运行，无窗口）：**

```bat
schtasks /create /tn "WorkBuddy每日签到" ^
  /tr "pythonw \"C:\path\to\scripts\auto_checkin.py\"" ^
  /sc daily /st 09:00 /rl limited
```

**macOS — launchd**（建一个 plist，`StartCalendarInterval` 设 Hour=9，ProgramArguments 指向脚本）。

**Linux — cron：**

```cron
0 9 * * * /usr/bin/python3 /path/to/scripts/auto_checkin.py >> /path/to/checkin.log 2>&1
```

## 🛡️ 排坑清单

- **Profile 锁**：目标浏览器正在运行时，另一进程用同 Profile 启动会报「资料夹被占用」。给机器人用独立 Profile，或在浏览器关闭时段运行。
- **活动未开启**：`checkin-status` 返回 `active:false` 时按钮不出现，脚本会跳过（退出码 0），不会崩。
- **Cookie 失效**：Profile 退出登录 / 会话过期后领取会失败（退出码 4）。脚本写本地 `.log`，定时任务应据退出码发告警。
- **ToS 风险**：低频个人使用风险低；不要做成高频 / 多账号刷分，遵守平台规则。
- **别硬编码领取 URL**：优先点 UI 按钮；仅当你从 Network 面板抓到真实写接口时，才用 `--claim-api`。

## 📤 发布到 GitHub

本目录已可直接作为 Git 仓库根：

```bash
cd workbuddy-auto-checkin
git init
git add .
git commit -m "feat: cross-platform WorkBuddy auto check-in skill"
gh repo create workbuddy-auto-checkin --public --source=. --push
# 或手动：git remote add origin <你的仓库URL> && git push -u origin main
```

仓库结构：

```
workbuddy-auto-checkin/
├── SKILL.md              # Skill 元数据 + 使用说明（WorkBuddy 加载）
├── scripts/
│   └── auto_checkin.py   # 跨平台核心脚本（零硬编码）
├── references/
│   └── api_notes.md      # 实测接口与响应结构（调试用）
├── README.md             # 本文件
└── LICENSE               # MIT
```

## License

MIT — see [LICENSE](LICENSE).
