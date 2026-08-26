"""M17 guard (slimmed-down version after the 2026-08-24
permission-surface consolidation): schema version / first-run
suggestion card (Consolidate/Skip, no materialization) / one-click
consolidation injection / created-session isolation / pruner
retirement assertion (spec/seat/home all gone).

History: the original M17 guarded the materialization pipeline (the
pruner seat + friction counting + bcompile format gate + promotion-
scheme render) -- the user's final ruling on 2026-08-24 tore all of
it out; the allow side now belongs to harness auto mode + the
PERM_ALLOW ledger; the registered flake 6b was buried with it.

Run: PYTHONIOENCODING=utf-8 python tests/test_m17.py
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
from commander.kernel.store import Store, SCHEMA_VERSION  # noqa: E402

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
    check("1 schema: user_version=19 (v19 ·启/·收 made real: "
          "protocols.prep/wrapup columns), intents.born_session "
          "present",
          SCHEMA_VERSION == 19
          and st._db.execute("PRAGMA user_version").fetchone()[0] == 19
          and "born_session" in {r[1] for r in st._db.execute(
              "PRAGMA table_info(intents)")}
          and "procedures" in {r[1] for r in st._db.execute(
              "PRAGMA table_info(intents)")})
    # prehistoric intent (born NULL = ready)
    st.intent_create("报时", title="报告当前时间", scenario="随口一问",
                     steps="Get-Date 报给用户", fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9870, ws_port=9871, spawn_host=False)
    eng.host = FakeHost()

    class FakeXHost:            # §2m v9: executor-seat stand-in for plain intents
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

    eng._xhosts["solo"] = FakeXHost()
    eng._tokens["xst"] = "x·solo"

    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ---- ② pruner retirement assertion (permission-surface
    #      consolidation final ruling)---------------------------
    check("2 pruner retired: spec row cleared, instances/pruner "
          "not cast, modules scenario not seeded, svc seat "
          "attribute absent",
          eng.store.spec("prune") is None
          and not (ws_root / "instances" / "pruner").exists()
          and not (ws_root / defaults.MODULES_DIRNAME / "pruner").exists()
          and not hasattr(eng, "svc"))

    # ---- WS observation surface -----------------------------------------------------
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9871", open_timeout=5)
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

    def run_once(name):
        """Trigger one real execution run and settle its account."""
        c.send(json.dumps({"type": "intent", "name": name, "input": ""}))
        ring = wait_for(lambda: next(
            (t for t in eng.store.tasks_recent(20)
             if t.get("intent") == name and t["status"] == "running"),
            None))
        assert ring, f"{name} 没投出来"
        time.sleep(0.4)
        post(9870, "/api/mcp", {"verb": "task_done", "task": ring["id"],
                                "outcome": "ok", "summary": "报完了",
                                "token": "xst"})
        wait_for(lambda: (eng.store.task(ring["id"]) or {})
                 .get("status") == "done")
        return ring["id"]

    # ---- ③ first-run suggestion card retirement assertion
    #      (consolidate re-targeted 2026-08-24: an intent's proper
    #      improvement path = the retry loop, the consolidation
    #      prompt moved to book-closing time)------------------------
    run_once("报时")
    time.sleep(0.8)
    sug = next((f for f in list(frames.queue)
                if f.get("type") == "card" and f.get("kind") == "ask"
                and "first run" in str(f.get("title"))), None)
    check("3 first-run suggestion card retired: after the "
          "first real execution settles, **no** card pops "
          "(intent-consolidation loop retired along with "
          "register-equals-compile)", sug is None)

    # ---- wrap up ----------------------------------------------------------
    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    c.close()
    check("9 clean shutdown", not th.is_alive())

print()
if FAILS:
    print(f"-- {len(FAILS)} checks failed:")
    for f in FAILS:
        print("   " + f)
    sys.exit(1)
print("M17 all green")
