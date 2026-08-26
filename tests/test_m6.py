"""M6 guard: timeout rule v1 (running times out -> failed, verdict
rides the receipt; gated sets no clock).

Run: PYTHONIOENCODING=utf-8 python tests/test_m6.py
"""
import json
import queue
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import _ws  # noqa: E402

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


def wait_for(fn, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def post(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:9795/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


defaults.TASK_TIMEOUT_S = 2         # engine polls per pump; hot-resizable

with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", steps="Get-Date 报给用户",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9795, ws_port=9796, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9796", open_timeout=5)
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

    # set up the gated round first (clock must not touch it)
    post({"verb": "intent_submit", "name": "开谱", "title": "开谱",
          "scenario": "开谱", "steps": "1. open D:/scores 最新 PDF"})
    r = _ws.register(post, "开谱")        # §2u: gate only in phase two
    gate_tid = r["task"]

    # ---- running times out -> failed, verdict rides the receipt
    class FakeXHost:            # §2m v9: execution-slot stand-in, bare intent
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
    c.send(json.dumps({"type": "intent", "name": "报时", "input": ""}))
    t1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "报时" and t["status"] == "running"), None))
    check("1 precondition: running after delivery", t1 is not None)
    reaped = wait_for(lambda: eng.store.task(t1["id"])["status"]
                      == "failed")
    check("2 §6 timeout rule: verdict is failed on timeout",
          bool(reaped))
    time.sleep(1.0)
    check("3 §6xv9 verdict rides the receipt to the resident "
          "pane only: x·solo has no listening pane, TIMEOUT_LINE "
          "isn't injected into sidecar "
          "(verdict goes through chat pane + ledger)",
          not any("timeout" in s for s in fake.sent))
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "timed out" in f.get("text", ""))
    check("4 §6 issuer receipt: user chain gets chat pane "
          "+ status update", said is not None)
    check("5 §6 history logs timeout",
          [x["outcome"] for x in eng.store.track("报时")] == ["timeout"])
    r = post({"verb": "task_done", "task": t1["id"], "outcome": "ok",
              "summary": "迟到的回账", "token": "xst"})
    check("6 §19 post-verdict receipt refused, refusal carries "
          "status (English side)",
          "only running" in r.get("error", ""))

    # ---- in-flight cleared: intent can re-trigger
    c.send(json.dumps({"type": "intent", "name": "报时", "input": "再来"}))
    t2 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "报时" and t["status"] == "running"
         and t["id"] != t1["id"]), None))
    check("7 §6 after timeout in-flight clears, re-trigger "
          "goes through", t2 is not None)

    # ---- gated sets no clock
    check("8 §6 gated sets no clock (a gate may never be "
          "approved, clock untouched)",
          eng.store.task(gate_tid)["status"] == "gated")

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("9 journal: chain/timeout recorded",
          any(r["kind"] == "chain" and r["name"] == "timeout"
              for r in rows))

print()
print("M6 PASS" if not FAILS else f"M6 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
