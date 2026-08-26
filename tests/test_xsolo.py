"""§2m v9 guard: executor rerouting -- a standalone intent always
runs through the general-purpose executor seat x·solo (sonnet
pinned + fixed thinking budget), and sidecar sheds the executor
role; xsolo package (steps travels with the task); the solo seat's
queue rule; the failure loop (proposal card -> surgery table fixes
the intent -> booking delivers directly, no parked stage); the
reserved-name gate.

Run: PYTHONIOENCODING=utf-8 python tests/test_xsolo.py
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
        f"http://127.0.0.1:9758{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    for nm in ("报时", "写卡"):
        st.intent_create(nm, title=nm, steps=f"先跑 {nm} 脚本,再回账",
                         fires=1, scenario="看点")
        st.intent_revise(nm, status="provisioned")
        st.compile_delivery(nm)
    sp = st.spec("deliver:报时")
    st.close()
    check("1 §2m v9 single-source reroute: deliver tail loop "
          "assignee=x·solo, template=xsolo",
          sp["steps"][-1]["assignee"] == "x·solo"
          and sp["steps"][-1]["template"] == "xsolo")

    eng = Engine(ws_root, http_port=9758, ws_port=9759, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    # ---- general-purpose executor's home (spin up for real to
    #      verify shape first, then swap in a stand-in) ---------------
    h = eng._xhost(defaults.XSOLO_NAME)
    home = ws_root / "instances" / "x·solo"
    smd = json.loads((home / ".claude" / "settings.json")
                     .read_text(encoding="utf-8"))
    check("2 §2m v9 seat home: CLAUDE.md general-purpose executor "
          "role (English identity) + sonnet pinned + "
          "thinking budget pinned (settings env)",
          "command interpreter"
          in (home / "CLAUDE.md").read_text(encoding="utf-8")
          and h.model == "sonnet"
          and smd["env"]["MAX_THINKING_TOKENS"]
          == str(defaults.XSOLO_THINKING))
    fx = FakeXHost()
    eng._xhosts[defaults.XSOLO_NAME] = fx
    xtok = next(k for k, v in eng._tokens.items()
                if v == defaults.XSOLO_SEAT)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9759", open_timeout=5)
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

    def solo_task(status=None, intent=None, not_id=None):
        return next(
            (t for t in eng.store.tasks_recent(40)
             if t.get("executor") == "x·solo"
             and (status is None or t["status"] == status)
             and (intent is None or t.get("intent") == intent)
             and (not_id is None or t["id"] != not_id)), None)

    # ---- trigger: standalone intent reroutes to headless, sidecar
    #      no longer accepts the task -----------------------------------
    c.send(json.dumps({"type": "intent", "name": "报时",
                       "input": "顺带写手账"}))
    t1 = wait_for(lambda: solo_task("running", "报时"))
    check("3 §2m v9 trigger reroute: standalone intent delivers "
          "to x·solo (running), sidecar seat gets zero delivery",
          t1 is not None and fx.delivered
          and not fake.sent)
    pkg = (ws_root / "runtime" / "tasks" / str(t1["id"])
           / "package.md").read_text(encoding="utf-8")
    check("4 §2m v9 xsolo package: full steps travels with task "
          "(no skill single-source) + user input",
          "E · execution" in pkg and "先跑 报时 脚本" in pkg
          and "顺带写手账" in pkg)

    # ---- §2m v14 parallelism rule: x·solo spins up on demand,
    #      multiple tasks run at once ------------------------------------
    c.send(json.dumps({"type": "intent", "name": "写卡", "input": ""}))
    t2 = wait_for(lambda: solo_task("running", "写卡"))
    check("5 §2m v14 parallelism rule: t1 still running, 「写卡」"
          "still delivers (spins up on demand, no queueing)",
          t2 is not None
          and solo_task("running", "报时") is not None
          and len(fx.delivered) >= 2)
    post("/api/mcp", {"verb": "task_done", "task": t1["id"],
                      "outcome": "ok", "summary": "报完了",
                      "token": xtok})
    check("6 parallel tasks independent: t1 booking doesn't "
          "affect t2 in flight",
          wait_for(lambda: eng.store.task(t1["id"])["status"] == "done")
          and eng.store.task(t2["id"])["status"] == "running")

    # ---- failure loop: proposal card -> surgery (the fix-the-intent
    #      version of the script) -------------------------------------
    post("/api/hook", {"hook_event_name": "PreToolUse",
                       "cwd": str(home),
                       "tool_name": "Write",
                       "tool_input": {"file_path": "D:/卡/半成品.md"}})
    post("/api/mcp", {"verb": "task_done", "task": t2["id"],
                      "outcome": "failed",
                      "summary": "写不动:缺权限", "token": xtok})
    card = frame_where(lambda f: f.get("type") == "card"
                       and "Executor failed" in str(f.get("title")))
    check("7 §2g×v9 failure proposal card still sent (solo task, "
          "same loop)",
          card is not None
          and any(o.get("action") == "surgery"
                  for o in card.get("options", [])))
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "surgery", "data": str(t2["id"])}))
    s1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(40)
         if t.get("spec") == "手术" and t["status"] == "running"), None))
    spkg = (ws_root / "runtime" / "tasks" / str(s1["id"])
            / "package.md").read_text(encoding="utf-8")
    check("8 §2m v9 surgery script swap: standalone intent "
          "version (§2u fix intent = edit intent.json and "
          "re-register, not edit skill; script in English)"
          " + residue map still carried",
          s1 is not None and "standalone intent" in spkg
          and "intent.json" in spkg and "半成品.md" in spkg
          and "skill" not in spkg)

    # ---- solo suspension lock (single-intent granularity) -------------
    c.send(json.dumps({"type": "intent", "name": "写卡", "input": ""}))
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "under surgery" in f.get("text", ""))
    check("9 §2m v9 suspension lock scoped to single intent: "
          "intent under surgery rejects trigger",
          said is not None
          and solo_task("queued", "写卡") is None)

    # ---- booking delivers directly (no skill-pending-approval stage) --
    post("/api/mcp", {"verb": "task_done", "task": s1["id"],
                      "outcome": "ok", "summary": "清了,改了 steps"})
    t3 = wait_for(lambda: solo_task("running", "写卡",
                                    not_id=t2["id"]))
    check("10 §2m v9 booking delivers directly: standalone "
          "intent has no parked stage, replay delivers "
          "immediately (origin points to the original failed "
          "task)",
          t3 is not None and t3["origin"] == t2["id"])
    post("/api/mcp", {"verb": "task_done", "task": t3["id"],
                      "outcome": "ok", "summary": "这回成了",
                      "token": xtok})

    # ---- reserved-name gate -----------------------------------------------
    scratch = ws_root / "instances" / "sidecar" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "sk.md").write_text("# solo\n占名测试。\n",
                                   encoding="utf-8")
    r = post("/api/mcp", {"verb": "intent_submit", "name": "solo",
                          "kind": "protocol"})
    check("11 reserved-name gate: protocol can't be named solo "
          "(protects the general-purpose executor seat name)",
          "reserved name" in r.get("error", ""))

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)

print()
print("XSOLO PASS" if not FAILS else f"XSOLO FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
