"""§2h guard: seat queue rules -- one task per seat, the accept-rule
(three priority tiers), cutting-in doesn't interrupt, grandfathering,
queueing doesn't burn the clock, gated tasks don't occupy the queue.

Run: PYTHONIOENCODING=utf-8 python tests/test_queue.py
"""
import json
import queue
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import _ws  # noqa: E402  (§2u two-stage helper)
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


def wait_for(fn, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def post(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:9740/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


defaults.TASK_TIMEOUT_S = 6

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # Windows handle race, cleanup failure is not fatal
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    for n, steps in (("报时", "Get-Date 报给用户"),
                     ("写卡", "写一张卡片"),
                     ("查天", "查天气报给用户")):
        st.intent_create(n, title=n, steps=steps, fires=1)
        st.intent_revise(n, status="provisioned")
        st.compile_delivery(n)
    # error-tier placeholder chain (§2h: the surgery/breach family's
    # tier), head=user so it can be started directly
    st.spec_put("breach·占", head="user",
                priority=defaults.PRIORITY_ERROR,
                consequence="测试用 error 档占位",
                steps=[{"assignee": "sidecar", "kind": "deliver",
                        "ref": "报时", "template": "package",
                        "accounting": "real",
                        "on_ok": "end", "on_fail": "end"}])
    st.close()

    eng = Engine(ws_root, http_port=9740, ws_port=9741, spawn_host=False)
    fake = FakeHost()
    eng.host = fake

    class FakeXHost:            # §2m v9: stand-in executor for a
                                 # standalone intent
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
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9741", open_timeout=5)
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

    def task_of(intent, status=None, not_id=None):
        return next(
            (t for t in eng.store.tasks_recent(30)
             if t.get("intent") == intent
             and (status is None or t["status"] == status)
             and (not_id is None or t["id"] != not_id)), None)

    # ---- 0 tier naming, and rework/retry belong to the error tier -----
    check("0 §2h retry / qual·rework belongs to error tier (surgery"
          " family)",
          eng.store.spec("retry")["priority"] == defaults.PRIORITY_ERROR
          and eng.store.spec("qual·rework")["priority"]
          == defaults.PRIORITY_ERROR
          and defaults.PRIORITY_EXEC == 0
          and defaults.PRIORITY_INTERNAL == 3)

    # ---- one task per seat + equal-tier joins the queue (v14: queue
    #      rules only govern the resident sidecar surface; x·solo
    #      spins up on demand and never queues, parallelism is
    #      checked separately in test_xsolo) -------------------------
    c.send(json.dumps({"type": "validate", "name": "报时"}))
    t1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(30)
         if t.get("spec") == "validate" and t.get("intent") == "报时"
         and t["status"] == "running"), None))
    check("1 precondition: t1 (sim, sidecar seat) delivers running",
          t1 is not None)

    c.send(json.dumps({"type": "validate", "name": "写卡"}))
    t2 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(30)
         if t.get("spec") == "validate" and t.get("intent") == "写卡"),
        None))
    time.sleep(1.2)          # give the pump two ticks, to prove it
                              # doesn't deliver t2
    t2 = eng.store.task(t2["id"])
    check("2 §2h one task per seat: same-tier task joins queue,"
          " not delivered (t2 queued, envelope not sent)",
          t2["status"] == "queued"
          and not any(f"[task {t2['id']}]" in x for x in fake.sent))

    # ---- error-tier cutting in joins the queue -------------------------
    t3 = eng.store.chain_start("breach·占", issuer="user")
    check("3 §2h higher-tier task joins queue (error tier"
          " t3 queued)",
          eng.store.task(t3["id"])["status"] == "queued")

    # ---- lower tier is refused (the accept-rule) -----------------------
    c.send(json.dumps({"type": "validate", "name": "查天"}))
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "refused" in f.get("text", ""))
    check("4 §2h accept-rule: lower-tier new task refused"
          " outright, refusal reason carries a pointer",
          said is not None
          and not any(t.get("spec") == "validate"
                      and t.get("intent") == "查天"
                      for t in eng.store.tasks_recent(30)))

    # ---- booking rotation: error cut-in delivers first, exec
    # grandfathering is not evicted ------------------------------------
    r = post({"verb": "task_done", "task": t1["id"], "outcome": "ok",
              "summary": "验完"})
    check("5 t1 accounting accepted", bool(r.get("ok")))
    t3r = wait_for(lambda: eng.store.task(t3["id"])["status"] == "running")
    check("6 §2h cut-in: after t1 settles, error tier t3"
          " delivers first (jumps ahead of earlier t2)",
          bool(t3r))
    check("7 §2h grandfather: already-queued lower-tier t2"
          " not evicted, still queued",
          eng.store.task(t2["id"])["status"] == "queued")

    # ---- queueing doesn't burn the clock: t3 times out and gets
    # reaped, t2's queue age already exceeds the clock but it lives --
    reaped = wait_for(lambda: eng.store.task(t3["id"])["status"]
                      == "failed", timeout=15)
    check("8 §6 timeout rule as usual: running t3 times out"
          " to failed", bool(reaped))
    t2r = wait_for(lambda: eng.store.task(t2["id"])["status"] == "running")
    check("9 §2h queueing doesn't burn the clock: t2's queue age"
          " exceeds TASK_TIMEOUT but isn't reaped, delivers once"
          " its turn comes", bool(t2r))
    r = post({"verb": "task_done", "task": t2["id"], "outcome": "ok",
              "summary": "验完"})
    check("10 t2 accounting accepted", bool(r.get("ok")))

    # ---- gated tasks don't occupy the queue -----------------------------
    post({"verb": "intent_submit", "name": "开谱", "title": "开谱",
          "scenario": "要看谱时", "steps": "1. open D:/scores 最新 PDF"})
    r = _ws.register(post, "开谱")        # §2u the gate only exists
                                          # in the second stage
    gate_tid = r["task"]
    c.send(json.dumps({"type": "intent", "name": "查天", "input": ""}))
    t4 = wait_for(lambda: task_of("查天"))
    check("11 §2h gated doesn't occupy queue: pending-approval"
          " task hangs on the gate, exec new task accepted as"
          " usual (v14: plain intent runs via x·solo in"
          " parallel, never refused by queue)",
          t4 is not None
          and eng.store.task(gate_tid)["status"] == "gated")

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("12 journal: queue-priority refusal is recorded",
          any(r["kind"] == "chain" and r["name"] == "refused"
              and r.get("reason") == "queue-priority" for r in rows))

print()
print("QUEUE PASS" if not FAILS else f"QUEUE FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
