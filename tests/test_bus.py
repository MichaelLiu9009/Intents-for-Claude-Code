"""§2f guard: the unified telemetry bus -- PreToolUse mailbox planted
in all three homes, attributed to the current active task by seat,
only sampling the tool name + coarse target, journal flood exemption,
the completion receipt eats the census, the bypass carries no load.

Run: PYTHONIOENCODING=utf-8 python tests/test_bus.py
"""
import json
import queue
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
from commander.kernel.provision import (                # noqa: E402
    provision_solo_home)
from commander.kernel.store import Store                # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    def __init__(self):
        self.sent = []

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return True

    def inject_chat(self, text):
        self.sent.append(text)

    def replay(self):
        return ""

    def stop(self):
        pass


def wait_for(fn, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def post(path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:9742{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # Windows handle race, cleanup failure isn't fatal
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报时", steps="Get-Date 报给用户",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9742, ws_port=9743, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    home = ws_root / "instances" / "sidecar"

    # ---- planting surface: all three home mints carry a PreToolUse
    #      mailbox -------------------------
    cfg = json.loads((home / ".claude" / "settings.json")
                     .read_text(encoding="utf-8"))
    check("1 §2f sidecar home has PreToolUse mailbox planted "
          "(hookfwd shares the pipe)",
          "PreToolUse" in cfg.get("hooks", {})
          and "hookfwd.py" in
          cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
    xh = provision_solo_home(ws_root)
    xcfg = json.loads((xh / ".claude" / "settings.json")
                      .read_text(encoding="utf-8"))
    check("2 §2f x·solo seat planted under the same rule (-p "
          "seat has a hook face too; pruner seat retired along "
          "with the permission-face consolidation)",
          "PreToolUse" in xcfg.get("hooks", {}))

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9743", open_timeout=5)
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

    # ---- attribution: by seat (cwd's tail name) attribute to the
    #      current active task (§2m v9: plain intents rerouted to
    #      x·solo, bus attribution follows the seat)-------------------------------------

    class FakeXHost:
        def __init__(self):
            self.delivered = []

        def alive(self):
            return True

        def deliver(self, tid, line):
            self.delivered.append((tid, line))
            return True

        def reap(self, tid):
            pass

        def stop(self):
            pass

    fx = FakeXHost()
    eng._xhosts["solo"] = fx
    eng._tokens["xst"] = "x·solo"
    xhome = ws_root / "instances" / "x·solo"
    c.send(json.dumps({"type": "intent", "name": "报时", "input": ""}))
    t1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "报时" and t["status"] == "running"),
        None))
    check("3 precondition: t1 running", t1 is not None)

    def bus(cwd, tool, ti):
        post("/api/hook", {"hook_event_name": "PreToolUse",
                           "session_id": "s-x", "cwd": str(cwd),
                           "tool_name": tool, "tool_input": ti})

    bus(xhome, "Edit", {"file_path": "D:/notes/x.md", "old_string": "长料"
                        * 500})
    bus(xhome, "Bash", {"command": "git status -s"})
    bus(ws_root / "别处", "Read", {"file_path": "D:/orphan.md"})   # outside a seat
    ev = eng._task_dir(t1["id"]) / "events.jsonl"
    rows = wait_for(lambda: ev.is_file() and [
        json.loads(x) for x in
        ev.read_text(encoding="utf-8").splitlines()])
    check("4 §2f attribution: both hands post to t1's event "
          "ledger; off-seat noise dropped, not filed",
          rows is not None and len(rows) == 2
          and rows[0]["tool"] == "Edit"
          and rows[0]["target"] == "D:/notes/x.md")
    check("5 §2f coarse target only: command takes the first "
          "word, payload not recorded",
          rows is not None and rows[1]["target"] == "git"
          and all("old_string" not in json.dumps(r) for r in rows))

    # ---- journal flood exemption + sid not polluted by the x· seat ------------------------
    check("6 §2f sid learning doesn't route through the bus "
          "(host session not overwritten by the execution slot)",
          eng._host_session != "s-x")

    # ---- the completion receipt eats the census ---------------------------------------------------
    r = post("/api/mcp", {"verb": "task_done", "task": t1["id"],
                          "outcome": "ok", "summary": "报完",
                          "token": "xst"})
    check("7 t1 settlement accepted", bool(r.get("ok")))
    fr = frame_where(lambda f: "settled" in str(f)
                     and "tools 2 calls" in str(f))
    check("8 §2f completion receipt carries the tool census "
          "(2 calls, top list)", fr is not None)

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    jrows = [json.loads(x) for x in
             (jdir / "events.jsonl").read_text(
                 encoding="utf-8").splitlines()]
    check("9 §2f journal flood exemption: PreToolUse doesn't "
          "enter the full hook log",
          not any(r["kind"] == "hook" and r["name"] == "PreToolUse"
                  for r in jrows))

print()
print("BUS PASS" if not FAILS else f"BUS FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
