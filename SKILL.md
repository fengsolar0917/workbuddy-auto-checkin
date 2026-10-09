---
name: workbuddy-auto-checkin
description: One-skill automation for the whole WorkBuddy 成长计划 (growth center) routine — daily check-in credits (每日签到 / 领取今日礼包), 领取礼物 (claim the Buddy gift incl. points), 派「当前展示的 Buddy」去旅行 (dispatch the Buddy currently shown on the page — WorkBuddy Buddies have NO level field, only rarity like SSR; so this skill dispatches the displayed Buddy, no hardcoded name), and the monthly login-streak rewards (redeem / lottery / makeup) — all in the background without launching the WorkBuddy desktop client, via official REST APIs only (no browser, no Playwright). Use when the user wants to build, run, package, or distribute a reusable cross-platform background automation for any of these WorkBuddy growth-center tasks, or asks how to auto "每日签到" / "领取礼物" / "派 Buddy 去旅行" / "派萌宠旅行" unattended. Covers current-Buddy detection, scheduled tasks (Windows Task Scheduler / macOS launchd / Linux cron), and safe degradation when inactive / already done / not logged in / API unavailable.
agent_created: true
---

# WorkBuddy 成长计划一站式自动化

## Overview

This **single** skill delivers one cross-platform, zero-hardcoding automation that
performs the **entire** growth-center routine the user originally asked for — in the
background, without starting the WorkBuddy desktop client, **via official REST APIs only**:

1. **每日签到领积分** — the daily check-in credits (an amount that varies per campaign; the script simply claims whatever the active campaign grants).
2. **领取礼物** — claim the gift the Buddy brought back from its trip (the "Buddy 报告" preview + some redeemable credits — the amount varies per campaign).
3. **派「当前展示的 Buddy」去旅行** — the script reads the current Buddy from the API (no hardcoded name) and dispatches it. It picks a destination (咖啡馆 / 商场店铺 / 健身房 / 古镇客栈; default 咖啡馆). If the Buddy is not at home / the travel API is not open, the script logs and **skips safely (exit 0)** instead of erroring. This is a **manual** daily loop the user performs; the Buddy does **not** auto-travel.
4. **月历连登奖励（API-only）** — monthly login-streak milestones: auto-**redeem** every claimable 7/14/28-day tier, auto-**draw** available lottery chances, and optionally auto-**makeup** broken login days with makeup cards (capped: only as many cards as needed to reach the next locked tier). REST-only (`GET /activity/growth/streak`, `POST /activity/growth/redeem`, ...).

All four are handled by **one script** (`scripts/auto_growth.py`). You can run the
whole routine, or limit to a subset with flags (see below). It reuses an
already-logged-in WorkBuddy desktop session by **decrypting the local login token**
(AES-256-GCM envelope) — no browser, no token extraction needed.

### API-only implementation (no browser)

`scripts/api_backend.py` calls the official WorkBuddy REST endpoints directly:
- `POST /v2/billing/meter/checkin-activity-status` (read sign-in status)
- `POST /v2/billing/meter/daily-checkin` (idempotent daily check-in; `code 10001` = already done)
- `GET /v2/activity/growth/buddy/info` (current Buddy name/rarity)
- Travel on `www.workbuddy.cn` **without `/v2`**:
  `GET /activity/growth/buddy/travel/status`,
  `POST /activity/growth/buddy/travel/depart` (`{"location_id":N}`),
  `POST /activity/growth/buddy/travel/claim` (`{}`).
- Streak rewards: `GET /activity/growth/streak`, `POST /activity/growth/redeem`, `GET /activity/growth/lottery/summary`, `POST /activity/growth/lottery/draw`, `POST /activity/growth/makeup-cards/use`, ...

The heavy lifting — decrypting the WorkBuddy `accessToken` (since WorkBuddy 3.1.0 an **AES-256-GCM envelope** in `workbuddy-desktop.info`, not plaintext JWT) and discovering the decryption key via Windows DPAPI or scanning the running `WorkBuddy.exe` process memory — is **vendored as-is** from the community skill **totorosir-workbuddy-score v3.1.2 (MIT-0)** into `scripts/_vendor_buddy_station.py`. We do not hand-edit that file; to upgrade, re-vendor the whole module. The API path needs **only Python stdlib** — no Playwright, no browser.

> An earlier Playwright/UI fallback layer (button-text matching) was removed on 2026-10-09 because it broke whenever WorkBuddy changed page copy and it hardcoded account-specific pet names. The skill is now API-only.

> Reference detail (observed endpoints, response shape, caveats) lives in `references/api_notes.md`. Load it only when debugging.

## When to use

- The user says "打开成长计划", "领取礼物", "派 Buddy 去旅行", "派萌宠旅行",
  "每日签到", "领积分", "成长计划自动", "Buddy 自动", "萌宠自动", or any "WorkBuddy 自动".
- The user wants to schedule these actions on a headless machine / server.
- The user wants to package this workflow as a reusable WorkBuddy skill or publish it to GitHub.

## Workflow

### Step 1 — Ensure Python 3.8+ (standard library only)

No `pip install`, no browser, no Playwright. The script decrypts your local
WorkBuddy login token and calls the REST endpoints directly.

### Step 2 — Ensure a logged-in session exists

The automation can only work if WorkBuddy desktop is installed and you have
signed in at least once (the `workbuddy-desktop.info` login file must exist).

### Step 3 — Run (auto-detect everything)

```bash
python scripts/auto_growth.py                       # everything: 礼物 + 旅行 + 签到 + 连登
```

Useful flags:

| Flag | Purpose |
|---|---|
| `--skip-checkin` | Skip daily check-in (do Buddy gift + travel + streak only) |
| `--skip-buddy` | Skip Buddy gift + travel (do check-in only) |
| `--only-checkin` | Only do daily check-in |
| `--only-claim` | Only claim the Buddy gift |
| `--only-travel` | Only dispatch Buddy travel |
| `--destination {咖啡馆,商场店铺,健身房,古镇客栈}` | Travel destination (default 咖啡馆) |
| `--no-redeem` | Skip streak milestone auto-redeem (7/14/28-day login rewards; on by default) |
| `--no-lottery` | Skip auto lottery draw from streak rewards (on by default) |
| `--no-makeup` | Skip auto makeup-card use for broken login days (on by default, capped) |
| `--backend {api}` | Execution backend (API-only; the only mode) |

Exit codes (for scheduler alerting):
`0` = done or safely skipped (already claimed / already traveling / inactive / API not open);
`4` = not logged in / cannot obtain local login token (sign in to WorkBuddy desktop once);
`5` = a required step failed (API error).

> Note `5` is **not** a safe skip — schedule it to alert you, distinct from `0`.

### Step 4 — Schedule it (no client needed)

Pick the scheduler for the target OS. The script runs headless and writes a `.log` next to itself.

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

`tests/test_api_backend.py` is the API backend unit test (no browser, no network — fast). It mocks the vendored REST call and verifies check-in / gift / travel / streak behavior across combinations. Pure stdlib + the vendored module:

```bash
python tests/test_api_backend.py
# => 15 passed, 0 failed
```

## Safety & caveats

- **No hardcoded paths/users/pet-names.** Everything is read from the API; override only via flags.
- **Already-done is a safe skip (exit 0).** Already-claimed check-in / already gifted / already-traveling are detected and skipped — never double-claims.
- **API token is read-only & scoped.** The API backend only *decrypts* the on-disk WorkBuddy login token (never writes/exfiltrates it) and calls the authorized check-in + Buddy-travel + streak endpoints — no exchange / lottery / other write calls. The decryption logic is vendored from totorosir-workbuddy-score v3.1.2 (MIT-0) and kept untouched in `_vendor_buddy_station.py`.
- **Activity must be live.** When `checkin-status` returns `active:false` the script logs and skips (exit 0), it never errors out.
- **Terms of service:** low-frequency personal use is low-risk; do not build high-frequency or multi-account farming. Respect platform rules.
