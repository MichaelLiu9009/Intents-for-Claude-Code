"""M4 guard: intent creation chain (INTENT_SPEC §3, ruling 2026-08-10:
initiation rights sit with the human / sim exercises the full chain /
in-flight refuses new triggers). FakeHost stands in for the host,
HTTP is the bridge.

Run: PYTHONIOENCODING=utf-8 python tests/test_m4.py
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
        "http://127.0.0.1:9780/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


defaults.MAX_HOME_INTENTS = 2       # quota rule; engine reads it at call time

with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", steps="Get-Date 报给用户",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9780, ws_port=9781, spawn_host=False)
    fake = FakeHost()
    eng.host = fake

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
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9781", open_timeout=5)
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

    menu = frame_where(lambda f: f.get("type") == "intents")
    check("1 §3 at start: IME dict has only the seed intent",
          menu and [r["name"] for r in menu["rows"]] == ["报时"])

    # ---- submit draft: reject empty steps; draft + human approval-2 gate
    post({"verb": "intent_submit", "name": "开谱", "scenario": "开谱",
          "steps": ""})
    r = _ws.register(post, "开谱")
    check("2 §2u empty steps + no chain -> refused at registration "
          "(form decided by final declaration)",
          "steps" in r.get("error", ""))
    r = post({"verb": "intent_submit", "name": "野建", "steps": "x",
              "title": "野建"})
    check("2b class retired: submit takes no class at all, the "
          "row keeps the fossil column default",
          r.get("ok") and (eng.store.intent("野建") or {})
          .get("class") == "未分类")
    post({"verb": "intent_submit", "name": "开谱", "title": "开谱铺屏",
          "scenario": "练琴",
          "steps": "1. open D:/scores 里最新的 PDF(默认阅读器)"})
    r = _ws.register(post, "开谱")
    gate_tid = r.get("task")
    it = eng.store.intent("开谱")
    gt = eng.store.task(gate_tid)
    check('3 §2u registration = draft row + one human gate '
          '(approve "taking effect", final step)',
          r.get("ok") and it["status"] == "draft"
          and gt["status"] == "gated"
          and gt["gate"] == "approve registration"
          and gt["executor"] == "user")
    tpl = (ws_root / "runtime" / "tasks" / str(gate_tid)
           / "template.md").read_text(encoding="utf-8")
    check("4 §2u registration card renders into task dir: manifest "
          "+ directory listing, **no full-text dump**",
          "开谱" in tpl and "taking effect" in tpl
          and "workspace" in tpl)
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "pending registration" in f.get("text", ""))
    check("5 §2u raising the gate announces it loudly "
          "(chat pane: pending registration)", said is not None)

    # ---- draft cannot be bound: IME trigger has no effect
    c.send(json.dumps({"type": "intent", "name": "开谱", "input": "x"}))
    time.sleep(1.0)
    check("6 §3 draft isn't shown, can't be bound (trigger ignored)",
          len([t for t in eng.store.tasks_recent(50)
               if t.get("intent") == "开谱"]) == 1)

    # ---- resubmit while gated = revise draft (rev++)
    _ws.edit(eng, "开谱",
             steps="1. open D:/scores 最新 PDF -> if 目录空, (L2, ok)\n"
                   "2. report 目录空,一句")
    r = _ws.register(post, "开谱")
    check("7 §2u edit + resubmit = revise (rev++), same gate cycle "
          "(in-flight dedup: resubmit re-renders the same card)",
          r.get("ok") and eng.store.intent("开谱")["rev"] >= 2
          and r.get("task") == gate_tid)

    # ---- human approval-2 = final gate: approve ships it (2026-08-11)
    c.send(json.dumps({"type": "approve", "task": gate_tid}))
    up = wait_for(lambda: eng.store.intent("开谱")["status"]
                  == "provisioned")
    check("8 §3 approve -> provisioned + delivery spec compiles "
          "(sim is no longer a gate)",
          bool(up) and eng.store.spec("deliver:开谱") is not None)
    fresh = frame_where(lambda f: f.get("type") == "intents"
                        and any(x["name"] == "开谱" for x in f["rows"]))
    check("9 §3 IME dict refreshes immediately (no restart needed)",
          fresh is not None)

    # ---- validate: sim optional, human-triggered (3-wall, test ledger)
    c.send(json.dumps({"type": "validate", "name": "开谱"}))
    sim = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("spec") == "validate" and t["status"] == "running"),
        None))
    check("10 §3 validate chain: human-triggered, sim task "
          "delivered back to sidecar",
          sim is not None and sim["issuer"] == "user" and fake.sent)
    pkg = (ws_root / "runtime" / "tasks" / str(sim["id"])
           / "package.md").read_text(encoding="utf-8")
    check("11 §3b three-wall discipline ships with the "
          "validate package",
          "sim self-test" in pkg and "fixtures" in pkg)
    r = post({"verb": "task_done", "task": sim["id"], "outcome": "ok",
              "summary": "照 steps 走通,产物已自清"})
    check("12 §3 validate history books to the test ledger, "
          "status untouched",
          r.get("ok") and eng.store.intent("开谱")["status"]
          == "provisioned"
          and len(eng.store.track("开谱")) == 0
          and len(eng.store.track("开谱", include_test=True)) == 1)
    sug = frame_where(lambda f: f.get("type") == "card"
                      and f.get("kind") == "ask"
                      and "收尾" in str(f.get("title")), 3)
    check("12b custom wrap-up fix v2 (user self-correction "
          "2026-08-13): inside the creation session, sim passing "
          "**doesn't** trigger the suggestion -- same as the "
          "birth-quarantine ruling, only prompts on first flight",
          sug is None)
    rc = eng.store.events_between("2000-01-01 00:00:00",
                                  "2999-01-01 00:00:00",
                                  kinds=["task"], names=["receipt"])
    check("12d completion receipt: books on close-out (FakeHost "
          "has no transcript -> duration only, "
          "soft dependency doesn't break it)",
          any(e.get("task_id") == sim["id"] for e in rc))

    # ---- §2u revision channel: edit intent.json + re-register
    _ws.edit(eng, "开谱",
             steps="1. open D:/scores 最新 PDF -> if 目录空, (L2, ok)\n"
                   "2. report 目录空,report 用户")
    r = _ws.register(post, "开谱")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: "目录空" in (eng.store.intent("开谱") or {})
             .get("steps", ""))
    check("13 §2u revision = edit + re-register (rev++, "
          "approval compiles into the store)",
          r.get("ok")
          and "目录空" in eng.store.intent("开谱")["steps"])
    r = post({"verb": "caveat_add", "intent": "开谱",
              "text": "副屏是 utility 屏,禁止铺", "origin": "task-9"})
    check("13b caveat_add removed: refusal reason is the "
          "signpost (folds into intent.json's steps)",
          "removed" in r.get("error", "")
          and "intent.json" in r["error"])
    r = post({"verb": "intent_update", "name": "开谱", "steps": "x"})
    check("13c §2u intent_update channel removed: refusal "
          "points to intent.json",
          "removed" in r.get("error", "")
          and "intent.json" in r["error"])
    r = post({"verb": "intent_submit", "name": "开谱", "steps": "x",
              "scenario": "练琴"})
    check("13d §19 resubmitting an already-live name: refusal "
          "is the signpost (points to edit + re-register)",
          "intent.json" in r.get("error", ""))

    # ---- in-flight dedup: refuse new trigger (ruling)
    c.send(json.dumps({"type": "intent", "name": "开谱", "input": "肖邦"}))
    wait_for(lambda: any(t.get("spec") == "deliver:开谱"
                         and t["status"] == "running"
                         for t in eng.store.tasks_recent(20)))
    c.send(json.dumps({"type": "intent", "name": "开谱", "input": "再来"}))
    refused = frame_where(lambda f: f.get("type") == "chat"
                          and f.get("name") == "engine"
                          and "still running" in f.get("text", ""))
    live = [t for t in eng.store.tasks_recent(50)
            if t.get("spec") == "deliver:开谱"]
    check("14 §3 in-flight refuses new trigger, refusal lands "
          "in chat pane",
          refused is not None and len(live) == 1)

    # ---- dual quota: full catalog refuses creation (graduation pressure)
    r = post({"verb": "intent_submit", "name": "录音", "steps": "点名录音"})
    check("15 §3 full catalog refuses creation, refusal carries "
          "graduation pressure",
          "full" in r.get("error", ""))

    # ---- journal reconciliation
    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    check("16 journal: draft / revised / provisioned / refused "
          "all recorded",
          {("intent", "draft"), ("intent", "revised"),
           ("intent", "provisioned"), ("chain", "refused")} <= names)

print()
print("M4 PASS" if not FAILS else f"M4 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
