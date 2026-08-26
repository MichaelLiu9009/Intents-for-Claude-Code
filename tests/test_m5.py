"""M5 guard: cancel rule + priority 3 tiers + issuer rule (ruling
2026-08-10).

Run: PYTHONIOENCODING=utf-8 python tests/test_m5.py
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
        self.trust = True
        self.sent = []

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return self.trust

    def inject_chat(self, text):
        self.sent.append(text)

    def replay(self):
        return ""

    def stop(self):
        pass


def wait_for(fn, timeout=6.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def post(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:9790/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", steps="Get-Date 报给用户",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9790, ws_port=9791, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9791", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    frames: queue.Queue = queue.Queue()

    def pump_ws():
        while True:
            try:
                frames.put(json.loads(c.recv()))
            except Exception:
                return

    threading.Thread(target=pump_ws, daemon=True).start()

    def frame_where(pred, timeout=6.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                f = frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(f):
                return f
        return None

    # ---- issuer rule: IME chain books to human ledger; frame has two faces
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
    check("1 §6 issuer rule: IME-triggered chain has issuer=user",
          t1 is not None and t1["issuer"] == "user"
          and t1["priority"] == defaults.PRIORITY_INTENT)
    fr = frame_where(lambda f: f.get("type") == "chains"
                     and "ledger" in f and "queue" in f)
    check("2 §6 display has two faces: chains frame carries ledger "
          "+ queue",
          fr is not None and any(x["issuer"] == "user"
                                 for x in fr["ledger"]))

    # ---- unified cancel (2026-08-25): the running ring is
    # interrupted NOW — cancelled, reaped, no auto-replay ----
    c.send(json.dumps({"type": "cancel", "chain": t1["chain_id"]}))
    note = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "interrupted" in f.get("text", ""))
    check("3 §6 unified cancel on a running chain: ring "
          "interrupted now (cancelled), stated loudly",
          note is not None
          and eng.store.task(t1["id"])["status"] == "cancelled")
    check("3b §6 receipt: user-initiated chain, receipt is the chat "
          "face (no PTY injection; v9 reroute: sidecar seat has "
          "zero injection)",
          len(fake.sent) == 0)
    r = post({"verb": "task_done", "task": t1["id"], "outcome": "ok",
              "summary": "已报", "token": "xst"})
    led = {x["chain"]: x["status"] for x in eng.store.chains_recent(20)}
    check("4 §6 a late settlement is refused (the ring is already "
          "final); ledger final state = cancelled",
          "error" in r and len(eng.store.track("报时")) == 0
          and led[t1["chain_id"]] == "cancelled")

    # ---- cancel creation chain: sim pass still won't ship (flag at close)
    post({"verb": "intent_submit", "name": "开谱", "title": "开谱",
          "scenario": "开谱", "steps": "1. open D:/scores 最新 PDF"})
    r = _ws.register(post, "开谱")        # §2u: gate only in phase two
    gate_tid = r["task"]
    gt = eng.store.task(gate_tid)
    check("5 §6 creation chain runs the self-build/self-test track "
          "(priority 1)",
          gt["priority"] == defaults.PRIORITY_SELF)
    c.send(json.dumps({"type": "cancel", "chain": gt["chain_id"]}))
    wait_for(lambda: eng.store.chain_cancelled(gt["chain_id"]))
    check("5b §6 receipt: agent-initiated chain cancelled, notice "
          "envelope injected to issuer",
          any("[chain" in s and "cancelled" in s for s in fake.sent))
    c.send(json.dumps({"type": "approve", "task": gate_tid}))
    time.sleep(1.0)
    check("6 §6 cancelled creation chain: gate voided, approve can't "
          "move it, intent stays in draft",
          eng.store.task(gate_tid)["status"] == "cancelled"
          and eng.store.intent("开谱")["status"] == "draft")

    # ---- internal cannot be cancelled
    eng.store.spec_put("maint", head="engine",
                       priority=defaults.PRIORITY_INTERNAL,
                       consequence="引擎维护", steps=[
                           {"assignee": "engine", "kind": "procedure",
                            "ref": "prune@1"}])
    mt = eng.store.chain_start("maint", issuer="engine")
    c.send(json.dumps({"type": "cancel", "chain": mt["chain_id"]}))
    refuse = frame_where(lambda f: f.get("type") == "chat"
                         and f.get("name") == "engine"
                         and "cannot cancel" in f.get("text", ""))
    check("7 §6 internal (priority 2) refuses cancel, reason goes "
          "to chat face",
          refuse is not None
          and not eng.store.chain_cancelled(mt["chain_id"]))

    # ---- journal
    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("8 journal: chain/cancelled recorded",
          any(r["kind"] == "chain" and r["name"] == "cancelled"
              for r in rows))

print()
print("M5 PASS" if not FAILS else f"M5 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
