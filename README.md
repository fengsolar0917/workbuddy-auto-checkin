# WorkBuddy 成长计划一站式自动化（workbuddy-auto-checkin）

> 一个零硬编码的 WorkBuddy Skill：**不启动 WorkBuddy 桌面客户端**，
> 把成长计划页的一整套操作做成一个 Skill —— **每日签到**、
> **领取 Buddy 礼物**、**月历连登奖励（7/14/28 天里程碑自动兑换·抽奖·可选补签）**，
> 以及**派当前展示的 Buddy 去旅行**（可选/尽力）。
> 全程走 **API-only** 方案（直连官方 REST 接口，只需要 Python 标准库，**无需浏览器、无需 Playwright**）。
> 复用你本机 `www.workbuddy.cn` 的登录态（AES-256-GCM 信封解密），无需手动提取 token。
>
> ⚠️ **平台**：登录态解密依赖 Windows DPAPI / 进程内存读取，**当前仅支持 Windows**（macOS / Linux 暂不支持，见「原理」）。
>
> ⚠️ **关于「派 Buddy 去旅行」**：Buddy 旅行是你**每天在客户端手动派出**的（并非自动出行），
> 脚本只会在「Buddy 在家、待派出」的短暂窗口**尽力尝试**派发；找不到出发状态或接口未开放时**安全跳过（exit 0）**，
> 绝不伪造派发成功。因此旅行只是**可选附属能力**，不是核心自动化项。

**English summary:** A single zero-hardcoded WorkBuddy skill that auto-runs the
whole growth-center routine in the background — daily check-in credits, claim
the Buddy gift, dispatch the Buddy on a trip, and the monthly login-streak
rewards (redeem / lottery / optional makeup) — without launching the WorkBuddy
desktop client. It runs **API-only** (official REST endpoints, stdlib-only, no
browser, no Playwright). It reuses your local login state (AES-256-GCM envelope
decryption), no manual token extraction. **Windows only** (token decryption
relies on DPAPI / process-memory key discovery).

---

## ✨ 特性

- **一个 Skill 完成全部**：签到 + 领礼物 + 连登奖励（兑换/抽奖/可选补签）+ 派旅行（可选/尽力），统一入口 `auto_growth.py`，可整体跑也可按需 `--skip-*` / `--only-*` / `--no-*` 拆分。
- **纯 API，零浏览器依赖（默认且唯一）**：直连官方 REST 接口（仅 Python 标准库），不走 Playwright/UI 文案匹配，官方改页面文案也不受影响。
- **零硬编码**：不写死用户名、路径、浏览器、宠物名。从接口读取当前 Buddy，换号也能用。
- **依赖缺失不崩**：缺 `api_backend.py` 等模块会给出明确提示后退出（退出码 4），而不是静默失败。
- **优雅降级**：活动未开启 / 今日已领 / 已领过礼物 / 已在旅行 / 未登录 / API 不可用，全部安全跳过并写日志（退出码 0/4）。
- **可定时后台运行**：附 Windows 任务计划示例（`pythonw` 无窗口后台）；当前仅支持 Windows。
- **可复用**：直接作为 WorkBuddy Skill 复制到 `~/.workbuddy/skills/` 使用，或独立 Python 脚本运行。

- **连登奖励全自动（2026-09 新增，API-only）**：月历连登 7/14/28 天里程碑——
  自动兑换所有已达成的档位（`POST /activity/growth/redeem`，幂等 client_token）、
  自动抽掉可用抽奖次数；**补签默认关闭**（补签卡是稀缺资源），加 `--makeup` 才会
  按需限量补当月断登日（只补到「刚好够到下一个未达成档」，绝不超花，且在兑换之前执行，
  保证当月解锁的档位当月兑掉）。`--no-redeem` / `--no-lottery` 可分别关闭兑换与抽奖。
  端点契约详见 `references/api_notes.md`。

## ⚙️ 原理

**纯 API（无需浏览器）**：`scripts/api_backend.py` 直接调用 WorkBuddy 官方 REST 接口完成所有任务——每日签到、领旅行奖励、派 Buddy 旅行、连登兑换/抽奖/补签。它从本机 `workbuddy-desktop.info` 读取 `accessToken` 并解密（WorkBuddy 3.1.0 起该字段是 **AES-256-GCM 信封**而非明文 JWT，解密密钥经 Windows DPAPI 或扫描运行中的 `WorkBuddy.exe` 进程内存获得——**因此当前仅支持 Windows**）；这部分"重活"逐字节搬运自社区技能 **totorosir-workbuddy-score v3.1.2（MIT-0）**，放在 `scripts/_vendor_buddy_station.py`，不手改。该路径**仅依赖 Python 标准库**。

> 登录态实测（2026-10-09，Windows）：文件位于 `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`（CodeBuddy 与 WorkBuddy 同源共用该扩展宿主目录，属正常），登录态内 `domain` 字段官方签发为 `www.codebuddy.cn`，签到接口在该域可用——`www.codebuddy.cn` 与 `www.workbuddy.cn` 后端同源，token 通用。脚本按登录态内的 `domain` 字段直连，不写死域名。

1. 每日签到有**专门的领取动作**，不会登录即自动到账。API 路径核心接口：`POST /v2/billing/meter/checkin-activity-status`（读状态）、`POST /v2/billing/meter/daily-checkin`（幂等领取，`code 10001` = 今日已签）。
2. Buddy「领取礼物 / 派去旅行」：API 旅行走 `www.workbuddy.cn`（无 `/v2`）：`travel/status`、`travel/depart`、`travel/claim`。脚本按 `DEST_ID` 映射选目的地（与官方 4 个地点一致），不写死宠物名。
3. 鉴权：API 用本地解密出的 `accessToken` 直连 REST，无需浏览器 Cookie。

> 注：早期版本曾内置 Playwright/UI 回退层（靠页面中文按钮文案匹配），因官方改文案即失效、且写死账号专属宠物名，已于 2026-10-09 移除。当前为纯 API 实现。

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
# 全部跑：礼物 + 旅行 + 签到 + 连登（纯 API，无需浏览器）
python scripts/auto_growth.py

# 指定旅行目的地
python scripts/auto_growth.py --destination 古镇客栈

# 只做 Buddy 旅行（跳过签到）
python scripts/auto_growth.py --skip-checkin

# 只签到
python scripts/auto_growth.py --only-checkin

# 关闭连登兑换与抽奖
python scripts/auto_growth.py --no-redeem --no-lottery

# 额外开启限量补签（消耗补签卡，默认关闭）
python scripts/auto_growth.py --makeup
```

**前提**：本机为 **Windows**，装有 WorkBuddy 桌面端（含登录态文件 `workbuddy-desktop.info`）且已登录，
脚本自动读 token，**无需浏览器、无需 pip install 任何包**。

### 退出码（供定时任务判断是否需要告警）

| 码 | 含义 |
|---|---|
| 0 | 全部完成 / 安全跳过（已领、已旅行、活动未开启、接口未开放） |
| 4 | 未登录 / 无法获取本机登录态（需先登录 WorkBuddy 桌面端） |
| 5 | 某步 API 失败（需关注） |

> ⚠️ 退出码 **5 不是安全跳过**，定时任务应对它单独告警，别和「已领(0)」混为一谈。

## ⏰ 定时后台调度（不启动客户端）

**Windows — 任务计划（可脱机运行，无窗口）：**

```bat
schtasks /create /tn "WorkBuddy成长计划" ^
  /tr "pythonw \"C:\path\to\scripts\auto_growth.py\"" ^
  /sc daily /st 09:00 /rl limited
```

> macOS / Linux 暂不支持：登录态信封解密依赖 Windows DPAPI 与 `WorkBuddy.exe` 进程内存读取。
> 若官方未来提供 macOS/Linux 的密钥派生途径，欢迎 PR。

## 🛡️ 排坑清单

- **活动未开启**：`checkin-status` 返回 `active:false`、Buddy 旅行接口未开放；脚本都会跳过（退出码 0），不会崩。
- **会话失效**：API 路径 token 解密失败或请求 401 时返回 4。脚本写本地 `.log`，定时任务应据退出码发告警。
- **ToS 风险**：低频个人使用风险低；不要做成高频 / 多账号刷分，遵守平台规则。

## 🧪 本地测试（无需真实账号 / 联网）

`tests/test_api_backend.py` 是 API 后端单元测试（无浏览器、无网络，最快）——mock 掉社区 REST 调用，验证签到/礼物/旅行/连登各组合下的 API 行为。纯标准库：

```bash
python tests/test_api_backend.py
# => 20 passed, 0 failed
```

## 📤 发布到 GitHub

本目录已可直接作为 Git 仓库根：

```bash
cd workbuddy-auto-checkin
git init
git add .
git commit -m "feat: one-skill WorkBuddy growth-center automation (API-only)"
gh repo create workbuddy-auto-checkin --public --source=. --push
# 或手动：git remote add origin <你的仓库URL> && git push -u origin main
```

仓库结构：

```
workbuddy-auto-checkin/
├── SKILL.md              # Skill 元数据 + 使用说明（WorkBuddy 加载）
├── scripts/
│   ├── auto_growth.py        # 统一核心脚本（零硬编码，API-only 编排：签到+礼物+旅行+连登）
│   ├── api_backend.py        # API 后端薄封装（签到 / 领旅行奖励 / 派旅行 / 连登兑换·抽奖·补签）
│   └── _vendor_buddy_station.py  # 社区技能 totorosir-workbuddy-score v3.1.2 (MIT-0) 逐字节搬运：AtRest token 解密 + REST 调用（不手改）
├── references/
│   └── api_notes.md      # 实测接口笔记（含连登 streak/redeem/lottery/makeup 端点契约，调试用）
├── tests/
│   └── test_api_backend.py   # API 后端单元测试（无浏览器、无网络）
├── README.md             # 本文件
├── LICENSE               # MIT
└── .gitignore
```

## License

MIT — see [LICENSE](LICENSE).
