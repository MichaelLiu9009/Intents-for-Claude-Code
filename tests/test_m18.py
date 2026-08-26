"""M18 guard: dedicated approval window (docs/M18-APPROVAL.md) --
blocking round-trip / deny / timeout defer / always + session grants
(answer-first, then stop) / supersede / suggest recorded / provision
mints PermissionRequest / friction not double-counted / permfwd
end-to-end.

Run: PYTHONIOENCODING=utf-8 python tests/test_m18.py
"""
import http.server
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel.store import Store                # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return True

    def inject_chat(self, text):
        pass

    def write_raw(self, data):
        pass

    def replay(self):
        return ""

    def stop(self):
        pass


def wait_for(fn, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def ask_async(payload, out):
    """POST /api/perm (blocking); the answer lands in the out list."""
    req = urllib.request.Request(
        "http://127.0.0.1:9880/api/perm",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out.append(json.loads(r.read().decode("utf-8", "replace")))
    except Exception as e:
        out.append({"error": repr(e)})


PAYLOAD = {
    "tool_name": "PowerShell",
    "tool_input": {"command": '& "D:\\x\\run.ps1" -Song 鸽子'},
    "cwd": "D:/somewhere",
    "permission_suggestions": [
        {"type": "addRules", "behavior": "allow",
         "rules": [{"toolName": "PowerShell",
                    "ruleContent": '& "D:\\x\\run.ps1"*'}]}],
}

with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("老将", title="老将", scenario="s", steps="做",
                     fires=1)                       # born NULL = prehistoric
    st.intent_revise("老将", status="provisioned")
    st.compile_delivery("老将")
    st.close()

    eng = Engine(ws_root, http_port=9880, ws_port=9881, spawn_host=False)
    eng.host = FakeHost()
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ---- ⑦ provision minted the PermissionRequest hook ----------------------
    stj = json.loads((ws_root / "instances" / "sidecar" / ".claude"
                      / "settings.json").read_text(encoding="utf-8"))
    ph = (stj.get("hooks", {}).get("PermissionRequest")
          or [{}])[0].get("hooks", [{}])[0]
    check("7 provision: PermissionRequest -> permfwd, timeout=300"
          " (a hook waiting on a human can't be cut off at 5s)",
          "permfwd.py" in ph.get("command", "")
          and ph.get("timeout") == defaults.PERM_HOOK_TIMEOUT_S == 300)

    # ---- WS observation surface -----------------------------------------------------
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9881", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    frames: queue.Queue = queue.Queue()

    def pump_ws():
        while True:
            try:
                frames.put(json.loads(c.recv()))
            except Exception:
                return

    threading.Thread(target=pump_ws, daemon=True).start()

    def frame_where(pred, timeout=8.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                f = frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(f):
                return f
        return None

    def approval_card(timeout=8.0):
        return frame_where(lambda f: f.get("type") == "card"
                           and f.get("kind") == "approval", timeout)

    # ---- ① blocking round-trip: allow --------------------------------------------
    out1 = []
    threading.Thread(target=ask_async, args=(PAYLOAD, out1),
                     daemon=True).start()
    card = approval_card()
    check("1 blocking round-trip: ask hangs, approval card"
          " surfaces (tool + one-line detail + suggest"
          " verbatim; three keys)",
          card is not None and "PowerShell" in card["title"]
          and "run.ps1" in card["body"]
          and [o["data"] for o in card["options"]]
          == ["allow", "always", "deny"]
          and not out1)                       # still hanging -- the block is real
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "perm", "data": "allow"}))
    wait_for(lambda: out1)
    closed = frame_where(lambda f: f.get("type") == "card_close"
                         and f.get("id") == card["id"], 4)
    check("1b answer releases it: hook gets allow, card"
          " collected immediately",
          out1 and out1[0].get("decision") == "allow"
          and closed is not None)

    # ---- ② deny --------------------------------------------------------
    out2 = []
    threading.Thread(target=ask_async, args=(PAYLOAD, out2),
                     daemon=True).start()
    card = approval_card()
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "perm", "data": "deny"}))
    wait_for(lambda: out2)
    check("2 deny same path (recorded by=human)",
          out2 and out2[0].get("decision") == "deny")

    # ---- ③ timeout = defer (fail-safe)-----------------------------------
    defaults.PERM_ASK_WAIT_S = 1.2
    out3 = []
    threading.Thread(target=ask_async, args=(PAYLOAD, out3),
                     daemon=True).start()
    card = approval_card()
    wait_for(lambda: out3, timeout=6)
    closed = frame_where(lambda f: f.get("type") == "card_close"
                         and f.get("id") == card["id"], 4)
    check("3 timeout defer: nobody answers -> decision=ask"
          " (CLI's native popup as fallback) + card"
          " self-collects",
          out3 and out3[0].get("decision") == "ask"
          and closed is not None)
    defaults.PERM_ASK_WAIT_S = 290.0

    # ---- ④ always -> grants -> second ask answered-first-then-stopped ------------------------------
    out4 = []
    threading.Thread(target=ask_async, args=(PAYLOAD, out4),
                     daemon=True).start()
    card = approval_card()
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "perm", "data": "always"}))
    wait_for(lambda: out4)
    while not frames.empty():
        frames.get()                          # drain the queue, verify the second ask produces zero new cards
    out5 = []
    threading.Thread(target=ask_async, args=(PAYLOAD, out5),
                     daemon=True).start()
    wait_for(lambda: out5, timeout=6)
    ghost = approval_card(timeout=1.5)
    grants = eng.store.events_between(
        "2000-01-01 00:00:00", "2999-01-01 00:00:00",
        kinds=["perm"], names=["grant"])
    by_grant = eng.store.events_between(
        "2000-01-01 00:00:00", "2999-01-01 00:00:00",
        kinds=["perm"], names=["allow"])
    check("4 always: gets allow + grant recorded; **second ask"
          " with the same suggest answered-first-then-stopped**"
          " (no new card, by=grant) -- batch two is"
          " session-level always-approve",
          out4 and out4[0].get("decision") == "allow"
          and out5 and out5[0].get("decision") == "allow"
          and ghost is None and len(grants) == 1
          and any("grant" in (e.get("fields") or "")
                  for e in by_grant))
    check("4b always -> the harness's **own** PermissionUpdate objects "
          "ride back as `grant`; permfwd relays them verbatim as "
          "updatedPermissions and the CLI banks its own copy in this "
          "seat's settings (live-fire 2026-08-25). Nothing is minted "
          "engine-side on this face -- the rule the CLI banks is one "
          "the harness itself offered",
          out4 and out4[0].get("grant")
          == PAYLOAD["permission_suggestions"])
    check("4c allow-once carries no grant: one click, one call -- "
          "the deck's Approve key answers options[0] (Allow once), "
          "so the physical shortcut can never bank a permanent rule",
          out1 and "grant" not in out1[0])

    # ---- ⑤ supersede: double ask, the old one defers and lets go -------------------------------
    p2 = dict(PAYLOAD, tool_name="Bash",
              tool_input={"command": "git status"},
              permission_suggestions=[])
    out6, out7 = [], []
    threading.Thread(target=ask_async, args=(p2, out6),
                     daemon=True).start()
    card6 = approval_card()
    p3 = dict(p2, tool_input={"command": "git log"})
    threading.Thread(target=ask_async, args=(p3, out7),
                     daemon=True).start()
    card7 = approval_card()
    wait_for(lambda: out6, timeout=6)
    check("5 supersede: second ask arrives, the old one defers"
          " and lets go (doesn't answer the wrong question),"
          " new card stands",
          out6 and out6[0].get("decision") == "ask"
          and card7 is not None and not out7)
    c.send(json.dumps({"type": "card_answer", "id": card7["id"],
                       "action": "perm", "data": "deny"}))
    wait_for(lambda: out7)

    # ---- ⑥ suggest verbatim recorded --------------------------------------------
    asks = eng.store.events_between(
        "2000-01-01 00:00:00", "2999-01-01 00:00:00",
        kinds=["perm"], names=["ask"])
    check("6 suggest verbatim recorded from the first ask"
          " (can't be reconstructed after the fact -- P5 raw"
          " material)",
          any('run.ps1' in (e.get("fields") or "") for e in asks))

    # (original check ⑧ "friction not double-counted" removed along
    # with the retirement of _prune_watch -- permission-surface
    # consolidation 2026-08-24: the friction-counting loop no longer
    # exists.)

    # ---- ⑧b-d layered exemption stamp (real bug found 2026-08-12:
    #      duplicate cards)-------------------------------
    # check ③'s defer should already have hand-opened a fallback
    # card (once Notification is blocked by the exemption stamp, the
    # observation surface for the native popup is taken over by
    # defer) -- sweep away the leftovers
    with eng._card_lock:
        leftovers = [cd["id"] for cd in eng._cards.values()
                     if cd["kind"] == "perm"]
    check("8b defer opens the fallback card itself (the native"
          " popup needs an observation surface, Notification"
          " doesn't fire a second time)", bool(leftovers))
    for cid in leftovers:
        eng._card_close(cid, "test-sweep")
    PAYLOAD2 = {"tool_name": "WebFetch",
                "tool_input": {"url": "https://example.com/x"}}
    NOTIF = {"hook_event_name": "Notification",
             "message": "Claude needs your permission",
             "session_id": "s9"}
    out8 = []
    threading.Thread(target=ask_async, args=(PAYLOAD2, out8),
                     daemon=True).start()
    card = approval_card()
    eng._on_hook(NOTIF)
    with eng._card_lock:
        dup = any(cd["kind"] == "perm" for cd in eng._cards.values())
    check("8c dedicated window present (ask parked):"
          " Notification's fallback card is skipped -- the"
          " same ask never produces two cards",
          card is not None and not dup)
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "perm", "data": "allow"}))
    wait_for(lambda: out8)
    eng._on_hook(NOTIF)
    with eng._card_lock:
        dup2 = any(cd["kind"] == "perm" for cd in eng._cards.values())
    eng._perm_done_t = 0.0
    eng._perm_seen_t = 0.0      # clear the birth stamp too (night-two fix: the reverberation baseline includes birth)
    eng._on_hook(NOTIF)
    with eng._card_lock:
        dup3 = any(cd["kind"] == "perm" for cd in eng._cards.values())
    check("8d reverberation exemption window (skipped within"
          " 10s of the ask's birth/settle -- answered fast in"
          " 2s, reverberation arrives at 6s); once the"
          " exemption passes, the hook-absent fallback card"
          " still opens",
          out8 and out8[0].get("decision") == "allow"
          and not dup2 and dup3)
    with eng._card_lock:
        sweep = [cd["id"] for cd in eng._cards.values()
                 if cd["kind"] == "perm"]
    for cid in sweep:
        eng._card_close(cid, "test-sweep")

    # ---- ⑨ AskUserQuestion: admission auto-approved, the question
    #      surface becomes an ask card (ruled 2026-08-12)----
    outq = []
    QP = {"tool_name": "AskUserQuestion",
          "tool_input": {"questions": [
              {"question": "选哪个?", "header": "选择",
               "options": [{"label": "甲", "description": "第一"},
                           {"label": "乙"}],
               "multiSelect": False}]}}
    threading.Thread(target=ask_async, args=(QP, outq),
                     daemon=True).start()
    ok9 = wait_for(lambda: outq)
    qcard = frame_where(lambda f: f.get("type") == "card"
                        and f.get("kind") == "ask", 6)
    pol = [e for e in eng.store.events_between(
        "2000-01-01 00:00:00", "2999-01-01 00:00:00",
        kinds=["perm"], names=["allow"])
        if '"by": "policy"' in (e.get("fields") or "")]
    check("9 AskUserQuestion: ask action auto-approved"
          " (by=policy, no ask line so no friction accrues,"
          " no approval popup), question + options render into"
          " an ask card, answered by number key",
          bool(ok9) and outq[0].get("decision") == "allow"
          and qcard is not None and "选哪个" in qcard["body"]
          and "甲 — 第一" in qcard["body"]
          and [o["data"] for o in qcard["options"]] == ["1", "2", "\x1b"]
          and len(pol) == 1)
    check("9b terminal answers directly = card-dismiss rule"
          " sweeps the ask card (cli-engaged collects the ask"
          " along with it)",
          (eng._on_cli_in("1"),
           not any(cd["kind"] == "ask" for cd in eng._cards.values()))[1])

    # ---- ①c permfwd end-to-end (real subprocess, walk-up to find
    #      engine.json)---------
    out9 = []

    def run_fwd():
        p = subprocess.run(
            [sys.executable, str(SRC / "commander" / "permfwd.py"),
             str(ws_root)],
            input=json.dumps(PAYLOAD).encode("utf-8"),
            capture_output=True, timeout=30)
        out9.append(p)
    # the always above already granted the same suggest -> permfwd
    # should immediately get allow (answer-first-then-stop)
    tfwd = threading.Thread(target=run_fwd, daemon=True)
    tfwd.start()
    tfwd.join(timeout=25)
    dec = {}
    if out9:
        try:
            dec = json.loads(out9[0].stdout.decode("utf-8", "replace"))
        except ValueError:
            dec = {}
    check("9 permfwd end-to-end: real subprocess -> engine"
          " grant answers first -> stdout emits decision JSON,"
          " exit 0",
          out9 and out9[0].returncode == 0
          and dec.get("hookSpecificOutput", {})
                 .get("decision", {}).get("behavior") == "allow")
    # ---- (9b) permfwd relays `grant` as updatedPermissions ---------------
    # A stub engine answering allow+grant: the relay is the only thing
    # standing between a human's "Always allow" and the CLI actually
    # banking the rule, and it has no other guard.
    relay_payload = {"decision": "allow",
                     "grant": PAYLOAD["permission_suggestions"]}

    class _Stub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps(relay_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as t2:
        (Path(t2) / "runtime").mkdir()
        (Path(t2) / "runtime" / "engine.json").write_text(
            json.dumps({"http": srv.server_address[1]}), encoding="utf-8")
        pf = subprocess.run(
            [sys.executable, str(SRC / "commander" / "permfwd.py"), t2],
            input=json.dumps(PAYLOAD).encode("utf-8"),
            capture_output=True, timeout=30)
    srv.shutdown()
    try:
        dec2 = (json.loads(pf.stdout.decode("utf-8", "replace"))
                .get("hookSpecificOutput", {}).get("decision", {}))
    except ValueError:
        dec2 = {}
    check("9b permfwd relays `grant` verbatim as updatedPermissions --"
          " the field the CLI reads to bank the rule into this seat's"
          " own settings. Without the relay, Always allow would raise"
          " a card that persists nothing CLI-side",
          pf.returncode == 0 and dec2.get("behavior") == "allow"
          and dec2.get("updatedPermissions")
          == PAYLOAD["permission_suggestions"])


    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    c.close()
    check("10 clean shutdown", not th.is_alive())

print()
if FAILS:
    print(f"-- {len(FAILS)} checks failed:")
    for f in FAILS:
        print("   " + f)
    sys.exit(1)
print("M18 all green")
