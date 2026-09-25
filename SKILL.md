---
name: workbuddy-auto-checkin
description: Automate claiming WorkBuddy's daily check-in credits (每日签到 / 领取今日礼包) on a schedule without launching the WorkBuddy desktop client. Use this skill when the user wants to build, run, package, or distribute a reusable cross-platform background automation for WorkBuddy daily sign-in, or asks how to claim the 100-credit/day check-in unattended. It covers browser-profile reuse, environment auto-detection (OS + Edge/Chrome/Chromium), scheduled tasks (Windows Task Scheduler / macOS launchd / Linux cron), and safe degradation when the activity is inactive or not logged in.
agent_created: true
---

# WorkBuddy 每日签到自动领取

## Overview

This skill delivers a cross-platform, zero-hardcoding automation that claims
WorkBuddy's daily check-in credits (每日签到 / 领取今日礼包) in the background,
without starting the WorkBuddy desktop client. It reuses an already-logged-in
browser profile so the same-site session cookie authenticates the request —
no token extraction required.

Key property for portability: **the bundled script assumes nothing about the
target machine.** It auto-detects the operating system, the installed browser
(Edge / Chrome / Chromium), the user-data directory, and the profile name. It
fails with a clear instruction (not a crash) when a dependency is missing.

## When to use

- The user asks to "自动领取每日签到积分", "每天签到 WorkBuddy", or "不打开客户端领积分".
- The user wants to schedule this on a headless machine, a server, or another PC.
- The user wants to package this workflow as a reusable WorkBuddy skill or publish it to GitHub.
- The user asks "WorkBuddy 签到能不能后台自动做" — answer yes, then apply this skill.

## Mechanism (how it works)

1. The daily check-in is a manual click, not auto-credited on login. The
   backend endpoint observed in the live WorkBuddy web app is
   `POST /billing/meter/checkin-status` (status read). The actual claim action
   is triggered by clicking the "领取今日礼包 / 签到" button on the web workbench.
2. Authentication is the same-site session cookie that a logged-in browser
   carries automatically. Reusing a persistent browser profile inherits it.
3. The script therefore launches the target browser in headless mode with a
   persistent user-data directory, opens the workbench, reads `checkin-status`
   **inside the page (page.evaluate fetch)** — never via an independent HTTP
   client, which would not carry the cookie — then clicks the claim button or
   calls an optional explicit claim API.

> Reference detail (observed endpoints, response shape, caveats) lives in
> `references/api_notes.md`. Load it only when debugging the API.

## Workflow

### Step 1 — Install dependencies (on any machine)

The only runtime dependency is Playwright's Python package.

```bash
python -m pip install --upgrade pip
python -m pip install playwright
```

A browser binary is needed only when the target machine has **no** Edge/Chrome
installed; in that case fetch Playwright's bundled Chromium:

```bash
python -m playwright install chromium
```

If Edge or Chrome is already installed and logged into workbuddy.cn, skip the
browser download — the script reuses that profile directly.

### Step 2 — Ensure a logged-in profile exists

The automation can only claim credits if some browser profile on the machine
is already signed in to `https://www.workbuddy.cn/`. If not:

1. Open Edge/Chrome normally.
2. Sign in to WorkBuddy in that profile.
3. Close the browser. The script will reuse this exact profile.

### Step 3 — Run (auto-detect everything)

```bash
python scripts/auto_checkin.py
```

The script auto-detects OS, browser, user-data dir, and profile. Useful flags:

| Flag | Purpose |
|---|---|
| `--browser edge\|chrome\|chromium` | Force a specific browser (else auto-detect) |
| `--user-data-dir PATH` | Explicit User Data dir (the folder containing `Default`) |
| `--profile NAME` | Profile name inside it (default `Default`) |
| `--claim-api PATH` | Explicit claim endpoint to POST instead of clicking UI |
| `--dry-run` | Only detect environment + report login/activity state, no claim |
| `--url URL` | Override the WorkBuddy base URL |

Exit codes (for scheduler alerting):
`0` = claimed or safely skipped (already claimed / inactive);
`2` = Playwright missing; `3` = browser launch failed (profile locked);
`4` = not logged in (sign in once); `5` = claim attempt failed.

### Step 4 — Schedule it (no client needed)

Pick the scheduler for the target OS. The script runs headless and writes a
`.log` next to itself.

**Windows — Task Scheduler (runs even when logged off):**

```bat
schtasks /create /tn "WorkBuddy每日签到" ^
  /tr "pythonw \"C:\path\to\scripts\auto_checkin.py\"" ^
  /sc daily /st 09:00 /rl limited
```

**macOS — launchd** (drop a plist calling the script; `StartCalendarInterval` Hour=9).

**Linux — cron:**

```cron
0 9 * * * /usr/bin/python3 /path/to/scripts/auto_checkin.py >> /path/to/checkin.log 2>&1
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

## Safety & caveats

- **No hardcoded paths/users.** Everything is auto-detected; override only via flags.
- **Profile lock:** if the target browser is running, a second process using the
  same profile fails. Use a dedicated profile for the bot, or run when the browser
  is closed.
- **Activity must be live.** When `checkin-status` returns `active:false` the
  button is absent — the script logs and skips (exit 0), it never errors out.
- **Terms of service:** low-frequency personal use is low-risk; do not build
  high-frequency or multi-account farming. Respect platform rules.
- **Don't hardcode the claim URL.** Prefer clicking the UI button; only set
  `--claim-api` if the user has captured the real endpoint from the Network panel.
