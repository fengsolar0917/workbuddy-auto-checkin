#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local mock of the bits of WorkBuddy that auto_growth.py touches — for SAFE
offline self-testing, with NO real account / network.

One server simulates BOTH features on the same origin:

  Check-in (daily 100 credits):
    GET  /workbench?ck=<scenario>&btn=<0|1>&logged=<0|1>
         -> renders a page (login / with claim button / plain)
         -> also records the check-in STATE for subsequent status calls
    POST /billing/meter/checkin-status
         -> returns the recorded STATE (or a login HTML page if logged=1)
    POST /billing/meter/checkin
         -> flips today_checked_in=true (the "claim" write)

  Buddy (领取礼物 + 派龙焰喵去旅行) on the growth-center page:
    GET  /growth?gift=<done>&logged=<0|1>&failtravel=<0|1>
         -> renders the page; clicking buttons drives a local state machine
            (领取礼物 -> modal -> 关闭 -> 派猫猫旅行 -> 确定派出 -> 采风中)

Run standalone:  python mock_server.py <port>
"""

import functools
import http.server
import json
import socketserver
import threading
from urllib.parse import urlparse, parse_qs

# ---- check-in state machine (module-global, like a tiny backend) ----
CHECKIN_SCENARIOS = {
    "inactive": {"active": False, "today_checked_in": False},
    "claimed": {"active": True, "today_checked_in": True},
    "available_button": {"active": True, "today_checked_in": False},
    "available_nobutton": {"active": True, "today_checked_in": False},
}
CHECKIN_STATE = dict(CHECKIN_SCENARIOS["inactive"])
LOGGED_OUT = False

WORKBENCH_LOGIN = (
    '<!doctype html><html><head><meta charset="utf-8"></head>'
    '<body><h1>Sign in to WorkBuddy</h1><button>立即登录</button></body></html>'
)
WORKBENCH_CLAIM = (
    '<!doctype html><html><head><meta charset="utf-8"><title>WB Mock</title></head>'
    '<body><h1>WorkBuddy Mock</h1>'
    '<button onclick="fetch(\'/billing/meter/checkin\',{method:\'POST\','
    'credentials:\'include\',headers:{\'Content-Type\':\'application/json\'},body:\'{}\'})'
    '.then(()=>{}).catch(()=>{})">领取今日礼包</button></body></html>'
)
WORKBENCH_PLAIN = (
    '<!doctype html><html><head><meta charset="utf-8"></head>'
    '<body><h1>WorkBuddy Mock (no claim button)</h1></body></html>'
)

GROWTH_TMPL = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>成长计划</title></head><body>
<h1>成长计划</h1>
<div id="page"></div>
<script>
const GIFT_DONE = {gift_done};
const LOGGED = {logged};
const FAILTRAVEL = {failtravel};
const page = document.getElementById('page');
function renderInitial() {{
  if (LOGGED) {{ page.innerHTML = '<button>立即登录</button>'; return; }}
  if (GIFT_DONE) {{ page.innerHTML = '<button id="travel">派猫猫旅行</button>'; }}
  else {{ page.innerHTML = '<button id="gift">领取礼物</button>'; }}
}}
document.body.addEventListener('click', function(e) {{
  const t = e.target; if (!t) return;
  const txt = t.textContent || '';
  if (txt.includes('领取礼物')) {{
    page.innerHTML = '<div id="giftModal">Buddy 满载而归啦～'
      + '<button id="pts">领取 9 积分</button>'
      + '<button id="close">关闭</button></div>';
  }} else if (txt.includes('领取 9 积分')) {{
    const b = document.getElementById('pts'); if (b) b.textContent = '已领取积分';
  }} else if (t.id === 'close') {{
    page.innerHTML = '<button id="travel">派猫猫旅行</button>';
  }} else if (txt.includes('派猫猫旅行') || txt.includes('派去旅行')) {{
    page.innerHTML = '<div id="travelModal">想让 Buddy 今天去哪里逛逛？'
      + '<button class="dest">咖啡馆</button>'
      + '<button class="dest">商场店铺</button>'
      + '<button class="dest">健身房</button>'
      + '<button class="dest">古镇客栈</button>'
      + '<button id="go">确定派出</button></div>';
  }} else if (t.className === 'dest') {{
    document.querySelectorAll('.dest').forEach(function(x){{x.style.color='';}});
    t.style.color = 'red';
  }} else if (txt.includes('确定派出') || txt.includes('确认派出') || txt === '派出') {{
    if (!FAILTRAVEL) {{
      page.innerHTML = '<div id="result">Buddy 正在 咖啡馆 采风中… 距离回家 02:00:00</div>';
    }}
  }}
}});
renderInitial();
</script></body></html>"""


def _send(handler, code, body, ctype):
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path.startswith("/workbench"):
            global CHECKIN_STATE, LOGGED_OUT
            ck = qs.get("ck", ["inactive"])[0]
            LOGGED_OUT = qs.get("logged", ["0"])[0] == "1"
            if LOGGED_OUT:
                _send(self, 200, WORKBENCH_LOGIN, "text/html; charset=utf-8")
                return
            CHECKIN_STATE = dict(CHECKIN_SCENARIOS.get(ck, CHECKIN_SCENARIOS["inactive"]))
            btn = qs.get("btn", ["0"])[0] == "1"
            body = WORKBENCH_CLAIM if btn else WORKBENCH_PLAIN
            _send(self, 200, body, "text/html; charset=utf-8")
        elif parsed.path.startswith("/growth"):
            gift_done = qs.get("gift", [""])[0] == "done"
            logged = qs.get("logged", [""])[0] == "1"
            failtravel = qs.get("failtravel", [""])[0] == "1"
            body = GROWTH_TMPL.format(
                gift_done=str(gift_done).lower(),
                logged=str(logged).lower(),
                failtravel=str(failtravel).lower(),
            )
            _send(self, 200, body, "text/html; charset=utf-8")
        else:
            _send(self, 404, "{}")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/billing/meter/checkin-status":
            global LOGGED_OUT
            if LOGGED_OUT:
                _send(self, 200, WORKBENCH_LOGIN, "text/html; charset=utf-8")
            else:
                _send(self, 200, json.dumps(CHECKIN_STATE), "application/json")
        elif parsed.path == "/billing/meter/checkin":
            CHECKIN_STATE["today_checked_in"] = True
            _send(self, 200, json.dumps({"ok": True}), "application/json")
        else:
            _send(self, 404, "{}")

    def log_message(self, *a):
        pass


def start_free_server():
    """Start a mock server on a free port; return (httpd, port)."""
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8731
    httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
    print("mock WorkBuddy server on http://127.0.0.1:%d" % port)
    print("  /workbench?ck=inactive|claimed|available_button|available_nobutton&btn=0|1&logged=0|1")
    print("  /growth?gift=done&logged=1&failtravel=1")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
