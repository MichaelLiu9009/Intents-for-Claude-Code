"""§2g guard (v14 solo edition): the executor failure loop -- a
failure proposal card (human-triggered), surgery opening plus
in-flight rejection, a residue map, seat suspension, task_done as
the sole ignition + automatic replay, one-surgery-one-replay then
back to the human on a second failure, the retry lane's second
table, surgery timeout going aborted back to the human.

Run: PYTHONIOENCODING=utf-8 python tests/test_surgery.py
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
        f"http://127.0.0.1:9744{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    for nm in ("报时", "写卡"):
        st.intent_create(nm, title=nm, steps=nm + "步骤", fires=1)
        st.intent_revise(nm, status="provisioned")
        st.compile_delivery(nm)
    st.close()

    eng = Engine(ws_root, http_port=9744, ws_port=9745, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    fx = FakeXHost()
    eng._xhosts["solo"] = fx
    eng._tokens["xst"] = "x·solo"
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9745", open_timeout=5)
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

    def chat_where(sub, timeout=8.0):
        return frame_where(lambda f: f.get("type") == "chat"
                           and f.get("name") == "engine"
                           and sub in f.get("text", ""), timeout)

    def solo_task(status=None, not_id=None, intent=None):
        return next(
            (t for t in eng.store.tasks_recent(40)
             if t.get("executor") == "x·solo"
             and (status is None or t["status"] == status)
             and (intent is None or t.get("intent") == intent)
             and (not_id is None or t["id"] != not_id)), None)

    # ---- executor runs a task + bus residue ---------------------------
    c.send(json.dumps({"type": "intent", "name": "报时",
                       "input": "顺便记天气"}))
    t1 = wait_for(lambda: solo_task("running", intent="报时"))
    check("1 precondition: t1 sits at the general execution seat, "
          "running", t1 is not None)
    post("/api/hook", {"hook_event_name": "PreToolUse",
                       "cwd": str(ws_root / "instances" / "x·solo"),
                       "tool_name": "Write",
                       "tool_input": {"file_path": "D:/手账/半成品.md"}})

    # ---- failed lane: failure -> proposal card (still needs human
    # approve) -----------------------------------------------------------
    post("/api/mcp", {"verb": "task_done", "task": t1["id"],
                      "outcome": "failed",
                      "summary": "写不进:缺 Edit 权限", "token": "xst"})
    card = frame_where(lambda f: f.get("type") == "card"
                       and "Executor failed" in str(f.get("title")))
    check("2 §2g failed lane: auto debug proposal card (with a "
          "surgery option), no auto-opening the table",
          card is not None
          and any(o.get("action") == "surgery"
                  for o in card.get("options", []))
          and not any(t.get("spec") == "surgery"
                      for t in eng.store.tasks_recent(40)))
    # audit 2026-08-25: this card carries the ONLY entry into
    # surgery, so it must be sweep-immune — as an `ask` card the
    # first keystroke into the sidecar terminal deleted the repair
    # loop's front door (same defect the consolidate card was
    # already fixed for)
    eng._close_wait_cards("cli-engaged")
    check("2b the failed-order card is kind `offer`, so terminal "
          "typing can't sweep away the only way into surgery",
          card.get("kind") == "offer"
          and card["id"] in eng._cards)

    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "surgery", "data": str(t1["id"])}))
    s1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(40)
         if t.get("spec") == "surgery" and t["status"] == "running"), None))
    check("3 §2g approve opens the table: surgery ticket delivered "
          "to sidecar (error queue)",
          s1 is not None
          and s1["priority"] == defaults.PRIORITY_ERROR
          and s1["origin"] == t1["id"])
    pkg = (ws_root / "runtime" / "tasks" / str(s1["id"])
           / "package.md").read_text(encoding="utf-8")
    check("4 §2g surgery table carries a residue map (bus transcript) "
          "+ failure receipt + ignition note "
          "(v14: fix-intent script, English)",
          "半成品.md" in pkg and "缺 Edit 权限" in pkg
          and "ONE ignition signal" in pkg and "intent.json" in pkg)

    # ---- suspension lock (single intent) + other intents unaffected ---
    c.send(json.dumps({"type": "intent", "name": "报时", "input": ""}))
    said = chat_where("under surgery")
    check("5 §2g suspension lock: intent under surgery refuses to "
          "fire, plus a signpost",
          said is not None
          and solo_task("queued", intent="报时") is None)
    c.send(json.dumps({"type": "intent", "name": "写卡", "input": ""}))
    other = wait_for(lambda: solo_task("running", intent="写卡"))
    check("5b v14 suspension granularity = single: other intents "
          "keep running (parallel seats)",
          other is not None)
    post("/api/mcp", {"verb": "task_done", "task": other["id"],
                      "outcome": "ok", "summary": "写完", "token": "xst"})

    # ---- task_done is the sole ignition: booking triggers replay ------
    post("/api/mcp", {"verb": "task_done", "task": s1["id"],
                      "outcome": "ok", "summary": "清了半成品,改了 steps"})
    t2 = wait_for(lambda: solo_task("running", intent="报时",
                                    not_id=t1["id"]))
    check("6 §2g booking triggers replay: original intent + "
          "original input resubmitted to the execution seat, "
          "origin points to the original failed ticket "
          "(v14 has no parked ring, direct release)",
          t2 is not None and t2["payload"] == "顺便记天气"
          and t2["origin"] == t1["id"])

    # ---- fails again -> back to the human (one surgery, one replay,
    # no machine self-loop) -----------------------------------------
    post("/api/mcp", {"verb": "task_done", "task": t2["id"],
                      "outcome": "failed", "summary": "还是不行",
                      "token": "xst"})
    card2 = frame_where(lambda f: f.get("type") == "card"
                        and "Executor failed" in str(f.get("title")))
    check("7 §2g fails again, back to the human: another "
          "proposal card waits on a person, no automatic re-surgery",
          card2 is not None
          and not any(t.get("spec") == "surgery"
                      and t["status"] in ("queued", "running")
                      for t in eng.store.tasks_recent(40)))

    # ---- retry lane (reshaped 2026-08-25): a retry on a failed task
    # opens a retry order on sidecar -----------------------------------
    # (sidecar autopsies + redoes directly, no surgery auto-open;
    # surgery is now reachable only through the failed card)
    c.send(json.dumps({"type": "retry", "task": t2["id"],
                       "reason": "先清残留,再把权限写进 steps"}))
    rb = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(40)
         if t.get("spec") == "retry" and t["status"] == "running"), None))
    pkg2 = (ws_root / "runtime" / "tasks" / str(rb["id"])
            / "package.md").read_text(encoding="utf-8")
    check("8 retry lane: opens a retry order delivered to "
          "sidecar (no surgery opened), note travels with the script",
          rb is not None and rb["executor"] == "sidecar"
          and "Autopsy" in pkg2 and "先清残留" in pkg2
          and not any(t.get("spec") == "surgery"
                      and t["status"] in ("queued", "running")
                      for t in eng.store.tasks_recent(40)))

    # ---- order in flight: a duplicate retry is refused ---------------
    c.send(json.dumps({"type": "retry", "task": t2["id"],
                       "reason": "再来一轮"}))
    said = chat_where("ring in flight")
    check("9 retry in flight, another retry is refused "
          "(in-flight dedupe)", said is not None)
    # settle the retry (real settlement now — the consolidate offer
    # that follows just sits unanswered), freeing the seat for the
    # surgery-timeout test below
    post("/api/mcp", {"verb": "task_done", "task": rb["id"],
                      "outcome": "ok", "summary": "清了残留并兑现"})
    wait_for(lambda: eng.store.task(rb["id"])["status"] == "done")

    # ---- surgery timeout -> aborted back to the human (no replay) -----
    # entry point: check 7's second failed proposal card (surgery's
    # sole gate)
    c.send(json.dumps({"type": "card_answer", "id": card2["id"],
                       "action": "surgery", "data": str(t2["id"])}))
    s2 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(40)
         if t.get("spec") == "surgery" and t["status"] == "running"
         and t["id"] != s1["id"]), None))
    defaults.TASK_TIMEOUT_S = 3
    aborted = wait_for(lambda: eng.store.task(s2["id"])["status"]
                       == "failed", timeout=20)
    noti = frame_where(lambda f: f.get("type") == "card"
                       and "Surgery incomplete" in str(f.get("title")), 10)
    check("10 §2g surgery timeout: aborted card goes back to the "
          "human, no replay",
          bool(aborted) and noti is not None
          and solo_task("running", intent="报时",
                        not_id=t1["id"]) is None
          or solo_task("running", intent="报时", not_id=t1["id"])
          == t2)
    defaults.TASK_TIMEOUT_S = 900

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8")
            .splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    check("11 journal: surgery open/replay/aborted all filed",
          {("surgery", "open"), ("surgery", "replay"),
           ("surgery", "aborted")} <= names)

print()
print("SURGERY PASS" if not FAILS else f"SURGERY FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
