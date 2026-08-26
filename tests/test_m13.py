"""M13 guard: cockpit card surface (docs/M13-COCKPIT.md) -- provision
hook surface / /api/hook dispatch (dedicated cards + unknown types not
swallowed) / card_answer passthrough / PTY stall detection and
auto-dismiss law / hookfwd mailbox end-to-end.

Run: PYTHONIOENCODING=utf-8 python tests/test_m13.py
"""
import json
import os
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

defaults.IDLE_STALL_S = 1.2     # shorten stall threshold (engine reads
                                 # this module attr at run time)

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    """Engine knows the host through six surfaces:
    alive/ready/trusted/inject_chat/write_raw/replay -- the stand-in
    fulfills the contract; replay carries ANSI, to verify tail-record
    stripping."""

    def __init__(self):
        self.trust = True
        self.sent = []                      # inject_chat(whole line, two-beat)
        self.raw = []                       # write_raw(raw keystrokes as-is)

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return self.trust

    def inject_chat(self, text):
        self.sent.append(text)

    def write_raw(self, data):
        self.raw.append(data)

    def replay(self):
        return ("\x1b[2mAllow Read of package.md?\x1b[0m\r\n"
                "\x1b[36m❯ 1. Yes\x1b[0m\r\n  2. No\r\n")

    def stop(self):
        pass


def post(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def wait_for(fn, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)

    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", scenario="随口一问",
                     steps="Get-Date 报给用户", fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9830, ws_port=9831, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ---- ① provision hook surface (M13: M3's 1b permission floor
    #      extended)-----------
    home = ws_root / "instances" / "sidecar"
    st_json = json.loads((home / ".claude" / "settings.json")
                         .read_text(encoding="utf-8"))
    hooks = st_json.get("hooks", {})
    cmd = (hooks.get("Stop") or [{}])[0].get("hooks", [{}])[0].get(
        "command", "")
    check("1 provision mints hooks: Notification + Stop +"
          " PreToolUse (§2f bus) -> hookfwd mailbox +"
          " PermissionRequest -> permfwd (M18) (command carries"
          " workspace, matcher left empty, catch-all)",
          set(hooks) == {"Notification", "Stop", "PreToolUse",
                         "PermissionRequest"}
          and "hookfwd.py" in cmd and str(ws_root) in cmd
          and "matcher" not in (hooks.get("Stop") or [{}])[0])
    dirs = st_json["permissions"]["additionalDirectories"]
    check("1b permission floor still holds (hooks and"
          " additionalDirectories composed in the same file,"
          " neither crowds out the other)",
          any(d.endswith("runtime") for d in dirs)
          and any(d.endswith("utility") for d in dirs))

    # ---- WS observation surface ----------------------------------------------------
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9831", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    frames: queue.Queue = queue.Queue()

    def pump_ws(cli, q):
        while True:
            try:
                q.put(json.loads(cli.recv()))
            except Exception:
                return

    threading.Thread(target=pump_ws, args=(c, frames), daemon=True).start()

    def frame_where(q, pred, timeout=8.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                f = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(f):
                return f
        return None

    # ---- ② /api/hook: permission_prompt -> dedicated perm card (with
    #      tail record)--------
    r = post(9830, "/api/hook",
             {"hook_event_name": "Notification",
              "notification_type": "permission_prompt",
              "message": "Claude needs your permission to use Read"})
    perm = frame_where(frames, lambda f: f.get("type") == "card"
                       and f.get("kind") == "perm")
    check("2 permission_prompt -> perm card: message + screen"
          " tail-record (ANSI stripped) + keypress options",
          r.get("ok") and perm is not None
          and "permission to use Read" in perm["body"]
          and "1. Yes" in perm["body"] and "\x1b[36m" not in perm["body"]
          and len(perm.get("options") or []) >= 3)

    # ---- ③ card_answer key -> raw keystroke passthrough to host + card
    #      closes on answer ----------
    c.send(json.dumps({"type": "card_answer", "id": perm["id"],
                       "action": "key", "data": "1"}))
    closed = frame_where(frames, lambda f: f.get("type") == "card_close"
                         and f.get("id") == perm["id"])
    check("3 card_answer key: keypress '1' passes through"
          " verbatim to host (write_raw, zero padding) +"
          " card_close sent",
          closed is not None and fake.raw == ["1"] and not fake.sent)

    # ---- ④ unknown subtype not swallowed: info card surfaces
    #      (completeness law layer 2)------------
    post(9830, "/api/hook",
         {"hook_event_name": "Notification",
          "notification_type": "weird_probe", "message": "神秘事件"})
    info = frame_where(frames, lambda f: f.get("type") == "card"
                       and f.get("kind") == "info")
    check("4 unknown subtype -> info card not swallowed"
          " (subtype becomes title, message becomes body)",
          info is not None and "weird_probe" in info["title"]
          and "神秘事件" in info["body"])

    # ---- ⑤ late subscriber: hello replay shows cards still on the
    #      rack ------------------------------
    c2 = connect("ws://127.0.0.1:9831", open_timeout=5)
    frames2: queue.Queue = queue.Queue()
    threading.Thread(target=pump_ws, args=(c2, frames2),
                     daemon=True).start()
    c2.send(json.dumps({"type": "hello"}))
    cards0 = frame_where(frames2, lambda f: f.get("type") == "cards")
    check("5 hello replay cards frame: late subscriber can see"
          " the info card still on the rack",
          cards0 is not None
          and any(r["id"] == info["id"] for r in cards0["rows"]))
    c2.send(json.dumps({"type": "card_answer", "id": info["id"],
                        "action": "dismiss"}))
    closed = frame_where(frames, lambda f: f.get("type") == "card_close"
                         and f.get("id") == info["id"])
    check("6 dismiss answer collects the card (card_close"
          " broadcast seen by both sides)",
          closed is not None)

    # ---- ⑥ Stop hook -> feed event row (new reply available)------------------------
    post(9830, "/api/hook", {"hook_event_name": "Stop"})
    feed = frame_where(frames, lambda f: f.get("type") == "feed"
                       and f.get("kind") == "reply")
    check("7 Stop -> feed event row (material for the"
          " collapsed-state badge)",
          feed is not None and "new reply" in feed["text"])

    # ---- ⑦ PTY stall detection: running ring + stall over threshold
    #      -> stall card (§2m v9: plain intents rerouted to headless,
    #      stall detection now only covers the sidecar seat -- use
    #      validate's sim task as the sidecar-seat sample)---------------------
    c.send(json.dumps({"type": "validate", "name": "报时"}))
    tid = wait_for(lambda: next(
        (t["id"] for t in eng.store.tasks_recent(10)
         if t.get("spec") == "validate" and t["status"] == "running"),
        None))
    check("8 delivery in place (running, envelope injected"
          " into sidecar seat)",
          tid is not None and fake.sent and "[task" in fake.sent[-1])
    stall = frame_where(frames, lambda f: f.get("type") == "card"
                        and f.get("kind") == "stall")
    check("9 stillness exceeds threshold -> stall card surfaces"
          " (names the task, carries tail-record)",
          stall is not None and stall.get("task") == tid
          and "1. Yes" in stall["body"])

    # ---- ⑧ stall line-typing: line answer = whole-line inject
    #        (two-beat) + card closes; typing re-arms -> stalls and
    #        surfaces again -----------------------------------
    n_sent = len(fake.sent)
    c.send(json.dumps({"type": "card_answer", "id": stall["id"],
                       "action": "line", "data": "继续"}))
    closed = frame_where(frames, lambda f: f.get("type") == "card_close"
                         and f.get("id") == stall["id"])
    check("10 line answer: whole line goes through inject_chat"
          " to host + card collected",
          closed is not None and len(fake.sent) == n_sent + 1
          and fake.sent[-1] == "继续")
    stall2 = frame_where(frames, lambda f: f.get("type") == "card"
                         and f.get("kind") == "stall"
                         and f.get("id") != stall["id"])
    check("11 typing re-arms: still no output -> second stall"
          " card (one per episode)",
          stall2 is not None)

    # ---- ⑨ auto-dismiss law: PTY output resumes -> card auto-closes ---------------------------
    eng._on_pty_output("宿主醒了,继续跑…")
    closed = frame_where(frames, lambda f: f.get("type") == "card_close"
                         and f.get("id") == stall2["id"])
    check("12 output resumes -> stall card auto-dismisses"
          " (no stale card left behind)", closed is not None)
    post(9830, "/api/mcp", {"verb": "task_done", "task": tid,
                            "outcome": "ok", "summary": "报完了"})

    # ---- ⑩ hookfwd mailbox end-to-end (real subprocess, argv points
    #      at workspace)----------
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    fwd = SRC / "commander" / "hookfwd.py"
    p = subprocess.run(
        [sys.executable, str(fwd), str(ws_root)],
        input=json.dumps({"hook_event_name": "ProbeHook"}).encode("utf-8"),
        capture_output=True, timeout=15, env=env)
    probe = frame_where(frames, lambda f: f.get("type") == "card"
                        and f.get("kind") == "info"
                        and "ProbeHook" in f.get("title", ""))
    check("13 hookfwd: stdin -> engine.json discovered -> POST"
          " /api/hook -> card surfaces; exit 0",
          p.returncode == 0 and probe is not None)
    with tempfile.TemporaryDirectory() as t2:
        p2 = subprocess.run(
            [sys.executable, str(fwd), t2],
            input=b'{"hook_event_name":"X"}',
            capture_output=True, timeout=15, env=env)
    check("14 mailbox never backfires: exits 0 even without"
          " the engine (silent)",
          p2.returncode == 0)

    # ---- wrap up + journal reconciliation ------------------------------------------
    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    check("15 clean shutdown", not th.is_alive())
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    check("16 journal: hooks fully recorded + card open/answer"
          "/close all leave a trace",
          {("hook", "Notification"), ("hook", "Stop"),
           ("hook", "ProbeHook"), ("card", "open"),
           ("card", "answer"), ("card", "close")} <= names)

print()
print("M13 PASS" if not FAILS else f"M13 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
