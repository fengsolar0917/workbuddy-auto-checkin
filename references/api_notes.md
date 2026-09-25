# API & Endpoint Notes (observed, for debugging only)

These were captured live from the WorkBuddy web app via the browser network
panel and in-page `fetch`. They are **not** a stable public contract — front-end
routes can change. Prefer clicking the UI button over hardcoding URLs; this
document exists only to help debug.

## Endpoints seen during workbench load

| Method | Path | Purpose |
|---|---|---|
| POST | `/billing/meter/checkin-status` | Daily check-in **status** (core read) |
| GET  | `/activity/growth/buddy/info` | Buddy / growth center |
| GET  | `/portal/msg-center/message/summary` | Message center |

All requests go to the same origin `https://www.workbuddy.cn` and rely on the
same-site session cookie. No separate Bearer token was observed.

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

## Why read via in-page fetch, not urllib/requests

An independent HTTP client (urllib, requests, curl) does **not** carry the
browser's logged-in session cookie. The status/claim calls must run **inside
the page** (Playwright `page.evaluate` with `fetch(..., {credentials:'include'})`)
so the cookie is attached automatically. The bundled `auto_checkin.py` does this
correctly.

## Capturing the real claim endpoint yourself

When the campaign is `active:true` and you click "领取今日礼包":

1. Open the workbench in a normal browser, signed in.
2. Open DevTools → Network, filter XHR.
3. Click the claim button.
4. Note the POST URL that fires (likely a sibling of `checkin-status`).
5. Re-run the script with `--claim-api "/that/path"` to claim deterministically.

Until then, the default UI-click strategy is the safe, version-tolerant choice.
