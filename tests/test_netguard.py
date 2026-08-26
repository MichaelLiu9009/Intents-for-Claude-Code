"""Loopback guardrail guard (security batch 2026-08-12) -- the gate
on the browser surface.

Precedent: binding 127.0.0.1 does not stop an arbitrary web page in
the local browser (a WS handshake is not bound by same-origin
policy; a cross-origin text/plain POST needs no preflight). This
guard nails down two things:
  (1) Cross-origin requests are always rejected -- WS rejects at
      handshake time (not a single frame is accepted), HTTP rejects
      before dispatch; DNS rebinding is caught via the Host header.
  (2) **A missing Origin is always allowed through** -- the MCP
      bridge / hookfwd / guard scripts all depend on this; zero
      false positives is the precondition for this gate shipping.

Run: PYTHONIOENCODING=utf-8 python tests/test_netguard.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander.engine import Engine                     # noqa: E402
from commander.kernel import netguard                   # noqa: E402

HTTP_PORT, WS_PORT = 9840, 9841
EVIL = "http://evil.example.com"
MINE = f"http://127.0.0.1:{HTTP_PORT}"

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    """Cares about one thing only: did a rejected connection manage
    to inject any characters into the host."""

    def __init__(self):
        self.sent = []
        self.raw = []

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return True

    def inject_chat(self, text):
        self.sent.append(text)

    def write_raw(self, data):
        self.raw.append(data)

    def replay(self):
        return ""

    def stop(self):
        pass


def post(path, payload, origin=None, host=None, sec=None, timeout=10):
    """-> (status, body); 403 counts as an answer, not an
    exception."""
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    if sec:
        headers["Sec-Fetch-Site"] = sec
    req = urllib.request.Request(
        f"http://127.0.0.1:{HTTP_PORT}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def get(path, origin=None, host=None, sec=None):
    headers = {}
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    if sec:
        headers["Sec-Fetch-Site"] = sec
    req = urllib.request.Request(f"http://127.0.0.1:{HTTP_PORT}{path}",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def hdrs(path):
    """Response headers, lower-cased keys (framing-gate probe)."""
    req = urllib.request.Request(f"http://127.0.0.1:{HTTP_PORT}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return {k.lower(): v for k, v in e.headers.items()}


# ---- (1) criteria unit tests (pure functions, no engine needed) ---
check("1 origin_ok: missing Origin allowed (non-browser client -- "
      "precondition for zero false positives)",
      netguard.origin_ok(None, 9700) and netguard.origin_ok("", 9700))
check("2 origin_ok: own page allowed (loopback + engine HTTP port, "
      "including localhost)",
      netguard.origin_ok("http://127.0.0.1:9700", 9700)
      and netguard.origin_ok("http://localhost:9700", 9700)
      and netguard.origin_ok("HTTP://127.0.0.1:9700", 9700))
check("3 origin_ok: cross-origin always rejected (foreign domain / "
      "prefix trick / wrong port / https / null / no port)",
      not netguard.origin_ok("http://evil.com", 9700)
      and not netguard.origin_ok("http://127.0.0.1.evil.com:9700", 9700)
      and not netguard.origin_ok("http://localhost.evil.com:9700", 9700)
      and not netguard.origin_ok("http://127.0.0.1:9701", 9700)
      and not netguard.origin_ok("https://127.0.0.1:9700", 9700)
      and not netguard.origin_ok("null", 9700)
      and not netguard.origin_ok("http://127.0.0.1", 9700))
check("4 host_ok: loopback allowed, foreign domain rejected (the "
      "DNS rebinding gate); missing Host allowed",
      netguard.host_ok("127.0.0.1:9700") and netguard.host_ok("localhost")
      and netguard.host_ok(None) and netguard.host_ok("[::1]:9700")
      and not netguard.host_ok("evil.com:9700")
      and not netguard.host_ok("127.0.0.1.evil.com:9700"))
check("4b sec_fetch_ok: missing (non-browser) and same-origin "
      "allowed, cross-site . same-site . none rejected (the "
      "GET-surface gate)",
      netguard.sec_fetch_ok(None) and netguard.sec_fetch_ok("")
      and netguard.sec_fetch_ok("same-origin")
      and netguard.sec_fetch_ok(" Same-Origin ")
      and not netguard.sec_fetch_ok("cross-site")
      and not netguard.sec_fetch_ok("same-site")
      and not netguard.sec_fetch_ok("garbage"))
check("4b2 sec_fetch_ok rejects 'none' (audit 2026-08-25): a "
      "browser sends none for any navigation it started itself, so "
      "a link clicked in a chat/mail client reaches /trigger — and "
      "it survives a cross-site redirect",
      not netguard.sec_fetch_ok("none")
      and not netguard.sec_fetch_ok(" None "))

# ---- (2) live test: start the engine, try the real gate -----------
with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    eng = Engine(ws_root, http_port=HTTP_PORT, ws_port=WS_PORT,
                 spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    from websockets.sync.client import connect

    # cross-origin page: the gate must close at handshake -- and not
    # a single frame gets injected into the host
    blocked, detail = False, ""
    try:
        with connect(f"ws://127.0.0.1:{WS_PORT}", origin=EVIL,
                     open_timeout=5) as c:
            c.send(json.dumps({"type": "chat", "text": "rm -rf /"}))
            c.send(json.dumps({"type": "cli_in", "data": "evil"}))
            time.sleep(0.8)
    except Exception as e:
        blocked, detail = True, type(e).__name__
    time.sleep(0.5)
    check(f"5 WS cross-origin Origin: rejected at handshake "
          f"({detail or 'NOT REJECTED!'})",
          blocked)
    check("6 WS cross-origin: not a single byte of chat/cli_in "
          "reached the host (injection surface sealed)",
          not fake.sent and not fake.raw)

    # our own observe page: passes through as usual
    ok_mine = False
    try:
        with connect(f"ws://127.0.0.1:{WS_PORT}", origin=MINE,
                     open_timeout=5) as c:
            c.send(json.dumps({"type": "hello"}))
            ok_mine = json.loads(c.recv(timeout=5)).get("type") == "surface"
    except Exception:
        pass
    check("7 WS own-page Origin: allowed (observe page not "
          "misfired)", ok_mine)

    # no Origin (bridge / guard / script): passes through as usual
    ok_bare = False
    try:
        with connect(f"ws://127.0.0.1:{WS_PORT}", open_timeout=5) as c:
            c.send(json.dumps({"type": "hello"}))
            ok_bare = json.loads(c.recv(timeout=5)).get("type") == "surface"
    except Exception:
        pass
    check("8 WS no Origin: allowed (zero false positives for "
          "non-browser clients)", ok_bare)

    # HTTP: /api/mcp is a preflight-free CSRF write surface
    st, _ = post("/api/mcp", {"verb": "intent_catalog"}, origin=EVIL)
    check("9 HTTP /api/mcp cross-origin: 403 (CSRF write surface "
          "sealed)", st == 403)
    st, body = post("/api/mcp", {"verb": "intent_catalog"})
    check("10 HTTP /api/mcp no Origin: allowed (the MCP bridge's "
          "lifeline)",
          st == 200 and json.loads(body).get("ok"))
    st, _ = post("/api/mcp", {"verb": "intent_catalog"}, origin=MINE)
    check("11 HTTP /api/mcp own page: allowed", st == 200)

    # forging /api/hook = UI deception (fake permission-card copy
    # tricking a key press)
    st, _ = post("/api/hook", {"hook_event_name": "Notification",
                               "notification_type": "permission_prompt",
                               "message": "按 1 允许"}, origin=EVIL)
    check("12 HTTP /api/hook cross-origin: 403 (forged permission "
          "card sealed)", st == 403)
    check("12b a rejected hook never becomes a card (the gate sits "
          "before dispatch)",
          not eng._cards)
    st, _ = post("/api/hook", {"hook_event_name": "Probe"})
    check("13 HTTP /api/hook no Origin: allowed (hookfwd mailbox's "
          "lifeline)",
          st == 200)

    # DNS rebinding: Origin claims "same-origin" but Host is the
    # attacker's domain
    check("14 HTTP Host foreign domain: 403 (the DNS rebinding "
          "gate, GET/POST both guarded)",
          get("/observe", host="evil.com:%d" % HTTP_PORT) == 403
          and post("/api/mcp", {"verb": "intent_catalog"},
                   host="evil.com:%d" % HTTP_PORT)[0] == 403)
    check("15 normal HTTP GET unaffected (observe page still "
          "served as usual)",
          get("/observe") == 200 and get("/api/discover") == 200)

    # GET-surface gate added (2026-08-24): /trigger is a pure GET
    # action surface, a simple <img> request carries no Origin --
    # relies on Sec-Fetch-Site as a third gate, only on action
    # surfaces
    check("15b GET action surface cross-site img: 403 (blind-fire "
          "approve/shutdown sealed)",
          get("/trigger?engine=status", sec="cross-site") == 403
          and get("/api/discover", sec="cross-site") == 403
          and get("/trigger?engine=status", sec="same-site") == 403)
    check("15c GET /trigger headless (deck plugin) / same-origin: "
          "allowed",
          get("/trigger?engine=status") == 200
          and get("/trigger?engine=status", sec="same-origin") == 200)
    check("15c2 GET /trigger sec=none: 403 (audit 2026-08-25 — a "
          "link opened from any non-browser app carries none; the "
          "address-bar route is the cost of sealing it)",
          get("/trigger?engine=status", sec="none") == 403)
    check("15d panel paths don't carry the GET gate (opening a "
          "panel via cross-site link is a legit route)",
          get("/observe", sec="cross-site") == 200
          and get("/hub", sec="cross-site") == 200)
    check("15d2 panel pages carry frame-ancestors 'self' (audit "
          "2026-08-25: the Sec-Fetch exemption above also let any "
          "visited page iframe the live panel and steal an Approve "
          "click)",
          all("frame-ancestors 'self'" in (hdrs(p) or {}).get(
              "content-security-policy", "")
              for p in ("/observe", "/hub", "/flow")))
    st, _ = post("/api/mcp", {"verb": "intent_catalog"}, sec="cross-site")
    check("15e POST /api/mcp cross-site Sec-Fetch (no Origin): 403 "
          "(defense in depth)",
          st == 403)

    c = connect(f"ws://127.0.0.1:{WS_PORT}", open_timeout=5)
    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    check("16 clean shutdown", not th.is_alive())

    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    guards = [r for r in rows
              if r["kind"] == "guard" and r["name"] == "blocked"]
    check("17 gate rejections all logged (never silent; nonzero = "
          "a web page is knocking)",
          any(g.get("face") == "ws" for g in guards)
          and any(g.get("face") == "http" for g in guards)
          and any(g.get("origin") == EVIL for g in guards))

print()
print("NETGUARD PASS" if not FAILS else f"NETGUARD FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
