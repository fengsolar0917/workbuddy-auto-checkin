---
name: workbuddy-auto-checkin
description: One-skill automation for the whole WorkBuddy 成长计划 (growth center) routine — daily check-in credits (每日签到 / 领取今日礼包), 领取礼物 (claim the Buddy gift incl. points), and 派「当前展示的 Buddy」去旅行 (dispatch the Buddy currently shown on the page — WorkBuddy Buddies have NO level field, only rarity like SSR; so this skill dispatches the displayed Buddy, no hardcoded name) — all in the background without launching the WorkBuddy desktop client. Use when the user wants to build, run, package, or distribute a reusable cross-platform background automation for any of these WorkBuddy growth-center tasks, or asks how to auto "每日签到" / "领取礼物" / "派龙焰喵去旅行" / "派猫猫旅行" unattended. Covers browser-profile reuse, environment auto-detection (OS + Edge/Chrome/Chromium), current-Buddy detection, scheduled tasks (Windows Task Scheduler / macOS launchd / Linux cron), and safe degradation when inactive / already done / not logged in / travel button absent.
agent_created: true
---

# WorkBuddy 成长计划一站式自动化

## Overview

This **single** skill delivers one cross-platform, zero-hardcoding automation that
performs the **entire** growth-center routine the user originally asked for — in the
background, without starting the WorkBuddy desktop client:

1. **打开成长计划页面** — `https://www.workbuddy.cn/profile/growth-center`
2. **领取礼物** — claim the gift the Buddy brought back from its trip
   (the "Buddy 报告" preview + redeemable points, e.g. 9 积分)
3. **派「当前展示的 Buddy」去旅行** — the script reads the Buddy name shown
   on the page hero (e.g. 龙焰喵 SSR) and dispatches **that** one. Important:
   WorkBuddy Buddies have **no "等级/level" field** — only rarity (SSR etc.) —
   so "dispatch the highest-level pet" is not implementable; instead the skill
   dispatches whatever Buddy is currently displayed. It picks a destination
   (咖啡馆 / 商场店铺 / 健身房 / 古镇客栈; default 咖啡馆) and confirms 派出.
   If the page has **no** travel button at that moment (the dispatch button
   only appears when the Buddy is **at home** and ready to go — while it is
   traveling, or while a returned gift awaits claim, the button is hidden),
   the script logs and **skips safely (exit 0)** instead of erroring.
4. **每日签到领积分** — the daily check-in credits (第 1–6 天 100 积分/天,
   第 7 天 1000), a sibling action on the same account, driven by the same
   logged-in session

All four are handled by **one script** (`scripts/auto_growth.py`). You can run the
whole routine, or limit to a subset with flags (see below). It reuses an
already-logged-in browser profile so the same-site session cookie authenticates
every request — no token extraction required.

Key property for portability: **the bundled script assumes nothing about the
target machine.** It auto-detects the operating system, the installed browser
(Edge / Chrome / Chromium), the user-data directory, and the profile name. It
fails with a clear instruction (not a crash) when a dependency is missing.

### Three run modes (no browser-closing required)

You do **not** have to close your daily browser. Pick the mode that fits:

1. **CDP attach (recommended, zero conflict).** Start Edge/Chrome once with
   `--remote-debugging-port=9222` (a one-time tweak to the shortcut). Then run
   `python auto_growth.py --cdp-url 127.0.0.1:9222`. The script *attaches* to
   your already-running browser — no second process, no profile lock, same
   login session. **Your browser stays open the whole time.**
2. **Dedicated bot profile (default, never conflicts).** With no flags the
   script uses an isolated dir `~/.workbuddy-growth-bot` that is NOT your daily
   browser, so it can launch anytime without locking anything. Log in once with
   `python auto_growth.py --open`, then schedule headless runs.
3. **Reuse an existing profile (opt-in).** Pass `--user-data-dir` + `--profile`
   to reuse e.g. your real Default profile — only when that browser is closed
   or that profile isn't running.

## When to use

- The user says "打开成长计划", "领取礼物", "派龙焰喵去旅行", "派猫猫旅行",
  "每日签到", "领积分", "成长计划自动", "龙焰喵自动", or any "WorkBuddy 自动".
- The user wants to schedule these actions on a headless machine / server.
- The user wants to package this workflow as a reusable WorkBuddy skill or
  publish it to GitHub.

## Mechanism (how it works)

1. The daily check-in is a manual click, not auto-credited on login. The
   backend status endpoint observed in the live web app is
   `POST /billing/meter/checkin-status`. The claim action is triggered by
   clicking the "领取今日礼包 / 签到" button on the workbench.
2. The Buddy actions are triggered by clicking UI buttons on the growth-center
   page (no public claim API was observed): click **领取礼物** → modal
   "Buddy 满载而归啦～" → click **领取 9 积分** → click **关闭** → the Buddy
   returns home and a **派<宠物名>去旅行** button appears → click it → pick
   destination → click **确定派出** → "Buddy 正在 XX 采风中… 距离回家
   HH:MM:SS". (This is a **manual** daily loop the user performs; the Buddy
   does **not** auto-travel. The travel button is hidden while the Buddy is
   traveling or while a returned gift is awaiting claim.)

   **Current-Buddy dispatch (no level concept):** WorkBuddy Buddies have no
   numeric level. The script reads the Buddy name from the page hero
   (`detect_current_buddy()` in `scripts/auto_growth.py`, regex
   `/专属 Buddy ([一-龥]+喵)/`), then clicks the travel trigger for that name
   (`派龙焰喵去旅行` etc.), falling back to generic `派猫猫旅行 / 派去旅行 …` text.
   This keeps working on any account (the displayed name is never hardcoded).
   If no travel button is present at that moment (Buddy not at home), the
   script reports it and **skips safely (exit 0)** — it does NOT fail or
   invent a pet.
3. Authentication for both is the same-site session cookie a logged-in browser
   carries automatically. Reusing a persistent browser profile inherits it.
4. The script therefore launches the target browser headless with a persistent
   user-data directory (or attaches via CDP), opens the relevant pages, and
   drives the buttons — the status/claim calls run **inside the page**
   (`page.evaluate` `fetch` with `credentials:'include'`) so the cookie is
   attached. An independent HTTP client (urllib/requests) would NOT carry the
   cookie, which is why in-page fetch is used.

> Reference detail (observed endpoints, response shape, DOM flow, caveats) lives
> in `references/api_notes.md`. Load it only when debugging.

## Workflow

### Step 1 — Install dependencies (on any machine)

The only runtime dependency is Playwright's Python package.

```bash
python -m pip install playwright
```

A browser binary is needed only when the target machine has **no** Edge/Chrome
installed; in that case fetch Playwright's bundled Chromium:

```bash
python -m playwright install chromium
```

If Edge or Chrome is already installed and logged into workbuddy.cn, skip the
browser download — the script reuses that profile directly.

### Step 2 — Ensure a logged-in session exists

The automation can only work if the chosen profile / browser session is already
signed in to `https://www.workbuddy.cn/`.

- **CDP mode (recommended):** your running browser is already signed in — nothing to do.
- **Bot-profile / reuse mode:** if not yet signed in, run once with `--open`
  to open that profile in a visible window, sign in manually, then close it.
  The session is cached in the profile and reused on every headless run.

```bash
python scripts/auto_growth.py --open          # first-time login (bot profile)
python scripts/auto_growth.py --user-data-dir "C:/.../User Data" --profile "Profile 1" --open
```

### Step 3 — Run (auto-detect everything)

```bash
python scripts/auto_growth.py                       # everything: 礼物 + 旅行 + 签到
```

The script auto-detects OS, browser, user-data dir, and profile. Useful flags:

| Flag | Purpose |
|---|---|
| `--cdp-url URL` | **Attach to a running browser** (e.g. `127.0.0.1:9222`). No lock, no close needed. |
| `--open` | Open the resolved profile in a visible window to log in manually, then exit |
| `--skip-checkin` | Skip daily check-in (do Buddy gift + travel only) |
| `--skip-buddy` | Skip Buddy gift + travel (do check-in only) |
| `--only-checkin` | Only do daily check-in |
| `--only-claim` | Only claim the Buddy gift |
| `--only-travel` | Only dispatch Buddy travel |
| `--destination {咖啡馆,商场店铺,健身房,古镇客栈}` | Travel destination (default 咖啡馆) |
| `--claim-api PATH` | Explicit check-in claim endpoint to POST instead of clicking UI |
| `--browser edge\|chrome\|chromium` | Force a browser (else auto-detect) |
| `--user-data-dir PATH` | Explicit User Data dir (folder containing `Default`) |
| `--profile NAME` | Profile name (default `Default`) |
| `--bot-profile-dir PATH` | Override the default isolated bot profile dir |
| `--growth-url URL` / `--checkin-url URL` / `--url URL` | URL overrides |
| `--dry-run` | Detect env + open page + report login state, no action |

Exit codes (for scheduler alerting):
`0` = done or safely skipped (already claimed / already traveling / inactive);
`2` = Playwright missing; `3` = browser launch failed (profile locked) / CDP attach failed;
`4` = not logged in (sign in once with `--open`); `5` = a required step failed (button missing).

> Note `5` is **not** a safe skip — schedule it to alert you, distinct from `0`.

### Enabling CDP attach (one-time, so you never close the browser)

Shut your browser, then edit its shortcut / launch command to add a debug port,
and restart it:

- **Windows (Edge):** duplicate the Edge shortcut, set target to
  `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222`
- **macOS:** `open -a "Microsoft Edge" --args --remote-debugging-port=9222`
- **Linux:** `microsoft-edge --remote-debugging-port=9222 &`

Then run:

```bash
python scripts/auto_growth.py --cdp-url 127.0.0.1:9222
```

The script attaches to that exact running browser, runs in a new tab, and closes
only that tab — your browser and other tabs keep running.

### Step 4 — Schedule it (no client needed)

Pick the scheduler for the target OS. The script runs headless and writes a
`.log` next to itself.

**Windows — Task Scheduler (runs even when logged off):**

```bat
schtasks /create /tn "WorkBuddy成长计划" ^
  /tr "pythonw \"C:\path\to\scripts\auto_growth.py\"" ^
  /sc daily /st 09:00 /rl limited
```

**macOS — launchd** (drop a plist calling the script; `StartCalendarInterval` Hour=9).

**Linux — cron:**

```cron
0 9 * * * /usr/bin/python3 /path/to/scripts/auto_growth.py >> /path/to/growth.log 2>&1
```

## Packaging & distribution

To ship this skill to another machine or publish it:

1. Copy the whole `workbuddy-auto-checkin/` folder into the other machine's
   `~/.workbuddy/skills/` (user scope) or `<repo>/.workbuddy/skills/` (project scope).
   WorkBuddy picks it up on next start — no install step.
2. To produce a distributable zip, run the skill-creator packager:

   ```bash
   python ~/.workbuddy/skills/skill-creator/scripts/package_skill.py \
     ~/.workbuddy/skills/workbuddy-auto-checkin
   ```

3. For GitHub: the folder is already repo-ready (see `README.md` and `LICENSE`).
   Initialize a repo at the skill root and push — no extra build step.

## Local testing (no real account / network)

The skill ships a self-test in `tests/` that runs `auto_growth.py` against a
local mock of WorkBuddy (check-in endpoints + growth-center page). It uses
Playwright's bundled Chromium, so install that first (Step 1). Then:

```bash
cd tests
python -m venv .venv && .venv/Scripts/pip install playwright
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python run_growth_tests.py
```

It starts a mock server, runs the script in each scenario, and asserts the **exit
code** (and, for travel cases, that the right outcome appears in stdout).
Last verified result (real headless Chromium, 18 scenarios):

| Scenario | Setup | Expect | Got | Behavior |
|---|---|---|---|---|
| `combined_full` | gift unclaimed + checkin available w/ button | 0 | 0 | claims gift, dispatches travel, claims check-in |
| `combined_loggedout` | both pages show login | 4 | 4 | ERROR "not logged in" |
| `ck_inactive` | checkin `active:false` | 0 | 0 | safe skip |
| `ck_claimed` | checkin `today_checked_in:true` | 0 | 0 | already-claimed skip |
| `ck_available_button` | button present | 0 | 0 | clicks, verifies `today_checked_in=true` |
| `ck_available_nobutton` | no button | 5 | 5 | WARN "no claim button" (alert) |
| `ck_loggedout` | login page | 4 | 4 | ERROR "not logged in" |
| `ck_claim_api` | no button + `--claim-api` | 0 | 0 | deterministic claim path |
| `bd_full` | gift unclaimed | 0 | 0 | gift + travel |
| `bd_gift_claimed` | button already gone | 0 | 0 | skips gift, travels |
| `bd_loggedout` | login page | 4 | 4 | ERROR "not logged in" |
| `bd_travel_dispatch` | `dispatch=1` (button present) | 0 | 0 | dispatches current Buddy 龙焰喵 |
| `bd_travel_skipped` | no travel button (live-like) | 0 | 0 | INFO "安全跳过" (not an error) |
| `bd_travel_already` | `traveling=1` (采风中) | 0 | 0 | already-traveling skip |
| `bd_travel_fail` | button + `failtravel=1` | 5 | 5 | WARN (alert) |
| `bd_only_claim` | `--only-claim` | 0 | 0 | gift only |
| `bd_only_travel` | `--only-travel` | 0 | 0 | travel only |
| `bd_only_checkin` | `--only-checkin` | 0 | 0 | check-in only |

## Safety & caveats

- **No hardcoded paths/users.** Everything is auto-detected; override only via flags.
- **You do NOT need to close your browser.** The recommended CDP mode attaches to
  a running browser with `--remote-debugging-port=9222`; the default bot-profile
  mode uses an isolated dir that never locks your daily browser. Only mode 3
  (`--user-data-dir`) reuses your live profile and then needs that browser closed.
- **Already-done is a safe skip (exit 0).** Already-claimed check-in / already
  gifted / already-traveling are detected and skipped — never double-claims.
- **Profile lock (mode 3 only):** reusing your real Default profile while that
  browser is running fails. Prefer CDP mode or the default bot profile instead.
- **Activity must be live.** When `checkin-status` returns `active:false` the
  button is absent — the script logs and skips (exit 0), it never errors out.
- **Terms of service:** low-frequency personal use is low-risk; do not build
  high-frequency or multi-account farming. Respect platform rules.
- **Don't hardcode claim URLs.** Prefer clicking the UI button; only set
  `--claim-api` if the user has captured the real endpoint from the Network panel.
