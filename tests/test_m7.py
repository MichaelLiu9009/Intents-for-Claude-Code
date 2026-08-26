"""M7 guard: retry law (established 2026-08-10; reshaped 2026-08-25:
no acceptance bracket) --
retry = engine-built-in order delivered to sidecar: autopsy the
previous run + redo directly (never re-injected into the executor
slot); task_done settles for real; the lesson rides the
**consolidate offer** raised after settlement — approving it
suspends the intent and opens a consolidate order on sidecar, and
the registration gate revives the asset. Retry/consolidate stay
timeout-exempt (conversational orders). Surgery keeps only the
failed-proposal-card entry point (replay coverage in test_surgery).

Run with: PYTHONIOENCODING=utf-8 python tests/test_m7.py
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
        "http://127.0.0.1:9798/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", steps="Get-Date 报给用户",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9798, ws_port=9799, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9799", open_timeout=5)
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

    def pkg_of(tid):
        return (ws_root / "runtime" / "tasks" / str(tid)
                / "package.md").read_text(encoding="utf-8")

    # ---- First run: done is the final state (v9: a plain intent
    # runs on x·solo) -----------
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
    c.send(json.dumps({"type": "intent", "name": "报时",
                       "input": "顺便报星期"}))
    t1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "报时" and t["status"] == "running"), None))
    post({"verb": "task_done", "task": t1["id"], "outcome": "ok",
          "summary": "报了时间,但忘了星期", "token": "xst"})

    # ---- retry gatekeeping ----------------------------------------------
    c.send(json.dumps({"type": "retry", "task": 99999}))
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "cannot retry" in f.get("text", ""))
    check("1 §19 retry rejects an illegal target with a reason",
          said is not None)

    # ---- retry = opening a bracket (R5 two-round ruling: sidecar
    # settles it directly, doesn't enter the executor slot) --
    c.send(json.dumps({"type": "retry", "task": t1["id"],
                       "reason": "太啰嗦,只要时分"}))
    rb = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("spec") == "retry" and t["status"] == "running"), None))
    check("2 R5 retry opens a bracket: single ring feeds "
          "sidecar, issuer=user, origin points to last time, "
          "error tier, real record",
          rb is not None and rb["issuer"] == "user"
          and rb["origin"] == t1["id"]
          and rb["priority"] == defaults.PRIORITY_ERROR
          and rb["executor"] == "sidecar")
    p0 = pkg_of(rb["id"])
    check("3 retry script: autopsy duty + complaint text verbatim "
          "+ last receipt + real settlement (no acceptance round) "
          "+ no re-injection to the executor slot",
          "Autopsy" in p0 and "太啰嗦,只要时分" in p0
          and "忘了星期" in p0 and "real settlement" in p0
          and "Do NOT re-trigger" in p0)

    # ---- in-flight de-dup also covers retry: repeated keypress
    # rejected -----------------------------
    c.send(json.dumps({"type": "retry", "task": t1["id"]}))
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "in flight" in f.get("text", ""))
    check("4 R5 bracket in flight, repeat retry rejected",
          said is not None)

    # ---- bracket law is exempt from the timeout: the running
    # segment ignores the clock --------------------------------
    defaults.TASK_TIMEOUT_S = -1          # every running item now expired
    eng._reap_overdue()
    check("5 R5 bracket law is timeout-exempt: reap sweeps "
          "through, retry bracket still running",
          eng.store.task(rb["id"])["status"] == "running")
    defaults.TASK_TIMEOUT_S = 900

    # ---- settlement is real: task_done closes the ring and raises
    # the consolidate offer (kind offer — survives terminal typing) --
    r = post({"verb": "task_done", "task": rb["id"], "outcome": "ok",
              "summary": "只报时分版已给出;根因:E 没吃 input 的格式要求"})
    card1 = frame_where(lambda f: f.get("type") == "card"
                        and "Consolidate" in str(f.get("title")))
    check("6 retry settles for real (done, no gated claim); a "
          "consolidate offer card follows (kind=offer, not swept "
          "by cli engagement)",
          r.get("status") == "done"
          and eng.store.task(rb["id"])["status"] == "done"
          and card1 is not None and card1.get("kind") == "offer"
          and any(o.get("action") == "consolidate"
                  for o in card1.get("options", [])))
    eng._close_wait_cards("cli-engaged")
    with eng._card_lock:
        still = any(cd.get("kind") == "offer"
                    for cd in eng._cards.values())
    check("6b offer card survives the cli-engaged sweep (live-fire "
          "2026-08-25: the old ask acceptance card lost its "
          "buttons the moment the user typed)", still)

    # ---- settled ring refuses a second settlement --------------------
    r = post({"verb": "task_done", "task": rb["id"], "outcome": "ok",
              "summary": "再报一次"})
    check("7 a settled retry refuses task_done (only running rings "
          "settle)", "error" in r)

    # ---- consolidate approve: intent suspended + order lands on
    # sidecar; registration gate revives ------------------
    c.send(json.dumps({"type": "card_answer", "id": card1["id"],
                       "action": "consolidate",
                       "data": json.dumps({"kind": "intent",
                                           "name": "报时",
                                           "task": rb["id"]},
                                          ensure_ascii=False)}))
    ct = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("spec") == "consolidate"
         and t["status"] == "running"), None))
    check("8 consolidate approve: intent suspended (draft, trigger "
          "refused) + consolidate order delivered to sidecar with "
          "origin pointing at the retry task",
          ct is not None and ct["executor"] == "sidecar"
          and ct["origin"] == rb["id"]
          and eng.store.intent("报时")["status"] == "draft")
    p1 = pkg_of(ct["id"])
    check("8b consolidate script: suspension named + fold-and-"
          "re-register order + the registration gate stays human",
          "Consolidate: intent '报时'" in p1 and "suspended" in p1
          and "workspace_submit" in p1
          and "registration card" in p1)
    r = post({"verb": "task_done", "task": ct["id"], "outcome": "ok",
              "summary": "已折回 steps 并重新注册"})
    check("8c consolidate order settles normally (no offer chain "
          "re-fires on a consolidate settle)",
          r.get("status") == "done")
    _ws.edit(eng, "报时", steps="1. report 时分,按 input 时区")
    r = _ws.register(post, "报时")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    ok9 = wait_for(lambda: (eng.store.intent("报时") or {})
                   .get("status") == "provisioned")
    check("8d revival = the ordinary registration approve (draft → "
          "provisioned, no special machinery)", bool(ok9))

    # ---- unified cancel (2026-08-25): cancelling a running retry
    # interrupts it NOW and frees the seat (live-fire deadlock:
    # under the old soft law it sat running forever, pinning the
    # error-tier ceiling and refusing every new order) ---------
    c.send(json.dumps({"type": "intent", "name": "报时",
                       "input": "再来一单"}))
    t2 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "报时" and t["status"] == "running"
         and t["id"] != t1["id"]), None))
    post({"verb": "task_done", "task": t2["id"], "outcome": "ok",
          "summary": "ok", "token": "xst"})
    c.send(json.dumps({"type": "retry", "task": t2["id"],
                       "reason": "又不行"}))
    rb2 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("spec") == "retry" and t["status"] == "running"
         and t["id"] != rb["id"]), None))
    c.send(json.dumps({"type": "cancel", "chain": rb2["chain_id"]}))
    ok10 = wait_for(lambda: eng.store.task(rb2["id"])["status"]
                    == "cancelled")
    check("10 unified cancel: a running retry is interrupted now — "
          "ring cancelled, sidecar seat released",
          bool(ok10)
          and eng.store.seat_running("sidecar") is None)
    c.send(json.dumps({"type": "intent", "name": "报时",
                       "input": "第三单"}))
    t3 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "报时" and t["status"] == "running"
         and t["id"] not in (t1["id"], t2["id"])), None))
    check("11 after the cancel, new orders are admitted again (no "
          "intake freeze)", t3 is not None)
    post({"verb": "task_done", "task": t3["id"], "outcome": "ok",
          "summary": "ok", "token": "xst"})

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("9 journal: retry claim / consolidate-open land in the "
          "record",
          any(r["kind"] == "chain" and r["name"] == "claim"
              and r.get("task") == rb["id"] for r in rows)
          and any(r["kind"] == "chain"
                  and r["name"] == "consolidate-open"
                  and r.get("intent") == "报时" for r in rows))

print()
print("M7 PASS" if not FAILS else f"M7 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
