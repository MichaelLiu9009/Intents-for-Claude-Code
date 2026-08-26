"""M20 §1 guard: the consolidate loop -- a single task's token count
over threshold -> alert card (let him consolidate / stop reminding
for this intent / not now); the first-run card yields on the same
beat; muting persists in the DB across sessions. usage comes from
window_usage -- slice precision is M15's live-fire guard, here we
just stub it to verify the alert wiring.

Run: PYTHONIOENCODING=utf-8 python tests/test_m20.py
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
from commander.kernel import prune_report               # noqa: E402
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
        "http://127.0.0.1:9890/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# usage stub: 96k output (above the 50k threshold) -- wiring guard,
# slice precision belongs to M15
prune_report.window_usage = lambda win, home: {
    "calls": 3, "out": 96_000, "cache_read": 5, "msgs": 2}

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # Windows handle race, cleanup failure isn't fatal
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("烧钱", title="很烧的活", steps="去把贵的事办了",
                     fires=1)                    # born=None = born in prehistory
    st.intent_revise("烧钱", status="provisioned")
    st.compile_delivery("烧钱")
    st.close()

    eng = Engine(ws_root, http_port=9890, ws_port=9891, spawn_host=False)
    fake = FakeHost()
    eng.host = fake

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

    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9891", open_timeout=5)
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

    def run_once():
        c.send(json.dumps({"type": "intent", "name": "烧钱", "input": ""}))
        ring = wait_for(lambda: next(
            (t for t in eng.store.tasks_recent(20)
             if t.get("intent") == "烧钱" and t["status"] == "running"),
            None))
        assert ring, "没投出来"
        post({"verb": "task_done", "task": ring["id"], "outcome": "ok",
              "summary": "办完了", "token": "xst"})
        wait_for(lambda: (eng.store.task(ring["id"]) or {})
                 .get("status") == "done")
        time.sleep(1.1)
        return ring["id"]

    # ---- first-run card retired (consolidate re-targeted
    # 2026-08-24): the first run no longer has a first-run card
    # taking the beat, the token alert takes over directly -------------------------------------
    run_once()
    rc = frame_where(lambda f: f.get("type") == "feed"
                     and f.get("kind") == "receipt")
    check("1b completion receipt carries stubbed usage "
          "(out 96,000)",
          rc is not None and "96,000" in rc["text"])
    alert0 = frame_where(lambda f: f.get("type") == "card"
                         and "Token alert" in str(f.get("title")))
    sug = next((f for f in list(frames.queue)
                if f.get("type") == "card"
                and "first run" in str(f.get("title"))), None)
    check("1 first-run card retired: the first run has no "
          "first-run card, token alert takes over directly",
          sug is None and alert0 is not None)
    c.send(json.dumps({"type": "card_answer", "id": alert0["id"],
                       "action": "dismiss"}))

    # ---- second run: over threshold, alerts again (evidence never
    # sleeps)------------------------------
    run_once()
    alert = frame_where(lambda f: f.get("type") == "card"
                        and "Token alert" in str(f.get("title")))
    check("2 §1 over-threshold alert card: out 96k >= threshold "
          "50k, two options (don't remind again/not now), copy "
          "points to retry (consolidate re-targeted 2026-08-24)",
          alert is not None and "96,000" in alert["body"]
          and "retry" in alert["body"]
          and [o["action"] for o in alert["options"]]
          == ["mute-alert", "dismiss"])
    ev = eng.store.events_between("2000-01-01 00:00:00",
                                  "2999-01-01 00:00:00",
                                  kinds=["alert"], names=["token-alert"])
    check("2b alert recorded (journal->events, "
          "kind=alert/token-alert)",
          any(e.get("intent") == "烧钱" for e in ev))
    # (original check 3 "one-click consolidation injection" removed
    # along with the consolidate re-target -- the consolidation-book
    # pipeline's guard lives in test_seed; the alert card itself is
    # closed via dismiss)
    c.send(json.dumps({"type": "card_answer", "id": alert["id"],
                       "action": "dismiss", "data": ""}))

    # ---- third run: alerts again -> mute; fourth run: silent ------------------------
    run_once()
    alert2 = frame_where(lambda f: f.get("type") == "card"
                         and "Token alert" in str(f.get("title")))
    check("4 §1 unmuted alerts fire every time "
          "(evidence never sleeps)", alert2 is not None)
    c.send(json.dumps({"type": "card_answer", "id": alert2["id"],
                       "action": "mute-alert", "data": "烧钱"}))
    muted = wait_for(lambda: (eng.store.intent("烧钱") or {})
                     .get("mute_alert") == 1)
    check("5 §1 mute persisted to DB (mute_alert=1, "
          "survives across sessions)",
          bool(muted))
    run_once()
    quiet = next((f for f in list(frames.queue)
                  if f.get("type") == "card"
                  and "Token alert" in str(f.get("title"))), None)
    check("6 §1 don't remind me again: silent after muting",
          quiet is None)

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)

print()
print("M20 PASS" if not FAILS else f"M20 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
