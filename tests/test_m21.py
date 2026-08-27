"""M21 guard: the protocol plane (M20 §2/§2d) -- skill snapshot,
human-approved full text; interactive bracket semantics (marker
function words / in-member delivery / non-member rejection / deadline
exemption / human-closed settlement); executor's graduated shape
(graduation threshold / pointer stamp / reroute-compile / executor
seat minting / completion notify); toolkit's neutral ground.

Run: PYTHONIOENCODING=utf-8 python tests/test_m21.py
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
from commander.engine import Engine, ProtoInstance      # noqa: E402
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

    def write_raw(self, data):
        pass

    def replay(self):
        return ""

    def stop(self):
        pass


class FakeXHost:
    """Executor-seat stand-in: records deliveries, doesn't spawn a
    real CLI."""
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


def post(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:9895/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


SKILL_I = """# 陪练台本

- 开场:问今天练哪首。
- 每轮:用户触发成员词,按成员段落做;做完等下一轮。
- 收场:引擎收账,你只收个尾。
"""
SKILL_X = """# 观察聚合

## intent 报时
Get-Date 报给用户(summary 一句)。
"""

with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    scratch = ws_root / "draft"
    scratch.mkdir(parents=True)
    st = Store(ws_root / "state.db")
    for nm, steps in (("挥拍", "挥一下拍"),
                      ("记录", "记一笔")):
        st.intent_create(nm, title=nm, steps=steps, fires=1, cls="工具")
        st.intent_revise(nm, status="provisioned")
        st.compile_delivery(nm)
    st.close()

    eng = Engine(ws_root, http_port=9895, ws_port=9896, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9896", open_timeout=5)
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

    # ---- §2u two-phase flow: open ticket + provision workspace ->
    #      write files -> register ---------------------------
    r = _ws.open_proto(post, "陪练", subtype="executor", scenario="练琴")
    check("1 §2m v14 subtype gate: only the multi-round-bracket "
          "type remains (executor split out; rejection carries a "
          "signpost)",
          "multi-round bracket" in r.get("error", ""))
    r = _ws.proto_ready(post, eng, "陪练", SKILL_I,
                        [_ws.member_decl("报时", scenario="看点")],
                        scenario="练琴")
    gate = r.get("task")
    tpl = (ws_root / "runtime" / "tasks" / str(gate)
           / "template.md").read_text(encoding="utf-8")
    check("2 §2u register = snapshot + one gate; the card doesn't "
          "pour in full text (skill.md sits in the folder -- open "
          "it yourself)",
          r.get("ok") and "skill.md" in tpl and "陪练" in tpl
          and "陪练台本" not in tpl
          and (eng.store.proto_get("陪练") or {})["status"] == "draft")
    _ws.set_members(eng, "陪练", ["报时", "没这个"])
    r2 = _ws.register(post, "陪练")
    check("3 v17 member declarations ride with the book: a name "
          "missing from members/ = whole-book rejection (the "
          "offender is named)",
          "没这个" in r2.get("error", ""))
    _ws.set_members(eng, "陪练", ["报时"])
    r = _ws.register(post, "陪练")
    gate = r.get("task")

    # ---- human approval promotes to provisioned + marker function
    #      words ---------------------------------------
    c.send(json.dumps({"type": "approve", "task": gate}))
    up = wait_for(lambda: (eng.store.proto_get("陪练") or {})
                  .get("status") == "provisioned")
    skill_home = (ws_root / "instances" / "sidecar" / ".claude"
                  / "skills" / "protocol-陪练" / "SKILL.md")
    check("4 §2 approval means going live: skill render lands in "
          "utility + the home script",
          bool(up)
          and (ws_root / "utility" / "protocols" / "陪练"
               / "skill.md").is_file()
          and skill_home.is_file())
    menu = {x["name"] for x in eng._intent_menu()}
    check("6 sidecar IME lists only non-protocol intents (user "
          "ruling 2026-08-23: marker function words and member "
          "words stay hidden; trigger grammar is still "
          "recognized, marker doesn't occupy the intents table)",
          not any(n.endswith("·open") or n.endswith("·wrap") for n in menu)
          and eng.store.intent("陪练·open") is None)

    # ---- bracket semantics (M26: the bracket lives in the x·陪练
    #      seat)------------------------
    pinst = ProtoInstance("陪练", ws_root / "instances" / "x·陪练",
                          "sonnet", spawn=False)
    pinst.host = FakeHost()
    pinst._spawned = True
    eng._xhosts["陪练"] = pinst
    fx = FakeXHost()
    eng._xhosts["solo"] = fx
    eng._tokens["xst21"] = "x·solo"
    c.send(json.dumps({"type": "intent", "name": "陪练·open",
                       "input": "肖邦"}))
    br = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if str(t.get("spec")) == "protocol:陪练"
         and t["status"] == "running"), None))
    pkg = (ws_root / "runtime" / "tasks" / str(br["id"])
           / "package.md").read_text(encoding="utf-8")
    check("7 §2 start opens the loop: package renders skill + "
          "members + bracket discipline, envelope delivered to "
          "the instance seat (pointer form)",
          br is not None and "陪练台本" in pkg and "报时" in pkg
          and br.get("executor") == "x·陪练"
          and any(f"[task {br['id']}]" in s and "package:" in s
                  for s in pinst.host.sent))
    n_tasks = len(eng.store.tasks_recent(50))
    c.send(json.dumps({"type": "intent", "name": "报时", "input": "9点"}))
    stepped = wait_for(lambda: any(
        "protocol 陪练 step" in s and "intent 报时" in s
        for s in pinst.host.sent))
    check("8 §2 in-bracket member: delivery only, no loop-open "
          "(envelope into instance, zero new task)",
          bool(stepped) and len(eng.store.tasks_recent(50)) == n_tasks)
    # M26 parallel law: with the bracket open, a non-member loner
    # still goes through the regular chain (global exclusivity
    # retired)
    c.send(json.dumps({"type": "intent", "name": "挥拍", "input": ""}))
    dt9 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "挥拍" and t["status"] == "running"
         and t.get("executor") == "x·solo"), None))
    # status=running lands in the DB before host.deliver is visible --
    # the delivery ledger waits one more beat
    d9 = wait_for(lambda: fx.delivered)
    check("9 M26 parallel law: bracket open, a loner still runs "
          "through x·solo (no more global rejection)",
          dt9 is not None and bool(d9)
          and not any("挥拍" in s for s in pinst.host.sent))
    post({"verb": "task_done", "task": dt9["id"], "outcome": "ok",
          "summary": "挥好了(并行)", "token": "xst21"})
    c.send(json.dumps({"type": "intent", "name": "陪练·open", "input": ""}))
    nested = frame_where(lambda f: f.get("type") == "chat"
                         and f.get("name") == "engine"
                         and "already open" in f.get("text", ""), 4)
    check("10 M26 idempotent Start: re-starting with the bracket "
          "already open = points back to the same instance",
          nested is not None
          and (eng._bracket_of("陪练") or {}).get("id") == br["id"])
    # deadline exemption: age the bracket by hand and manually shake
    # reap -- it should not be judged failed
    eng.store.task_update(br["id"], delivered_at="2020-01-01 00:00:00")
    eng._reap_overdue()
    check("11 §2 deadline-law exemption: protocol task is immune "
          "to TASK_TIMEOUT",
          eng.store.task(br["id"])["status"] == "running")
    c.send(json.dumps({"type": "intent", "name": "陪练·wrap", "input": ""}))
    closed = wait_for(lambda: (eng.store.task(br["id"]) or {})
                      .get("status") == "done")
    rec = eng.store.record_for(br["id"]) or {}
    endnote = wait_for(lambda: any(
        "protocol 陪练 end" in s for s in pinst.host.sent))
    check("12 §2 end = human closes the bracket: engine settles "
          "directly (no task_done needed), closing receipt goes "
          "to the instance",
          bool(closed) and "closed" in (rec.get("outcome") or "")
          and bool(endnote))

    # ---- §2m v14: entering-the-book pointer stamp + member-count
    #      out-of-bounds + trigger still goes through x·solo -------------
    _ws.open_proto(post, "观察", scenario="观察")
    _ws.put_skill(eng, "观察", SKILL_X)
    _ws.set_members(eng, "观察", [])
    r = _ws.register(post, "观察")
    check("13a §2i seat-count law: empty roster = two function "
          "words, 2 < min3 -> reject (an empty book can't stand)",
          "seat count" in r.get("error", ""))
    # v17 name-collision whole-book rejection: 挥拍 is already an
    # independent intent -- names are globally unique
    _ws.put_member(eng, "观察", _ws.member_decl("挥拍", scenario="观察"))
    _ws.set_members(eng, "观察", ["挥拍"])
    r = _ws.register(post, "观察")
    check("13b v17 name-collision whole-book rejection: member "
          "name conflicting with an independent intent = whole "
          "book not accepted",
          "name collision" in r.get("error", ""))
    # v17 atomicity: one member's E is sick -> whole book rejected,
    # good members don't get shelved either
    _ws.put_member(eng, "观察", _ws.member_decl("观云", scenario="看云"))
    _ws.put_member(eng, "观察", _ws.member_decl(
        "观鸟", scenario="看鸟", steps="1. 飞 坏动词不在词表"))
    _ws.set_members(eng, "观察", ["观云", "观鸟"])
    r = _ws.register(post, "观察")
    check("13c v17 atomicity: one member's E is sick -> "
          "whole-book rejection (error carries the member "
          "prefix), good members don't land in the DB either",
          "观鸟" in r.get("error", "")
          and eng.store.intent("观云") is None)
    _ws.put_member(eng, "观察", _ws.member_decl("观鸟", scenario="看鸟"))
    r = _ws.register(post, "观察")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    up14 = wait_for(lambda: (eng.store.proto_get("观察") or {})
                    .get("status") == "provisioned")
    it14 = eng.store.intent("观鸟") or {}
    check("14 v17 whole-book atomic compile: members go live "
          "with the batch, proto+pointer double-stamped, no "
          "delivery chain minted (solo-fire already locked out)",
          bool(up14) and it14.get("status") == "provisioned"
          and it14.get("proto") == "观察"
          and it14.get("migrated_to") == "protocol:观察"
          and eng.store.spec("deliver:观鸟") is None)
    check("15b toolkit neutral ground: engine stands up the "
          "directory on power-on",
          (ws_root / "toolkit").is_dir())

    # ---- roster swap + revision-while-open (live-fire 2026-08-27) ----
    _ws.put_member(eng, "观察", _ws.member_decl("观星", scenario="看星"))
    _ws.set_members(eng, "观察", ["观云", "观星"])       # 观鸟 dropped
    r = _ws.register(post, "观察")
    # 14b: approving a revision while the bracket is OPEN is refused
    o14 = ProtoInstance("观察", ws_root / "instances" / "x·观察",
                        "sonnet", spawn=False)
    o14.host = FakeHost(); o14._spawned = True
    eng._xhosts["观察"] = o14
    eng._on_intent("观察·open", "")
    br14 = wait_for(lambda: eng._bracket_of("观察"))
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    time.sleep(1.2)
    still = (eng.store.proto_get("观察") or {}).get("members")
    check("14b revision gate re-checks the bracket at approval "
          "(TOCTOU family, retire's sibling): open bracket -> "
          "recompile refused, roster unchanged",
          bool(br14) and "观星" not in (still or []))
    eng._proto_close("观察", by="test")
    r = _ws.register(post, "观察")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    ok14c = wait_for(lambda: "观星" in ((eng.store.proto_get("观察")
                                        or {}).get("members") or []))
    check("14c roster swap retires the dropped member: 观鸟 flips "
          "to retired (proto stamp kept, soft law), no zombie "
          "provisioned row survives the recompile",
          bool(ok14c)
          and (eng.store.intent("观鸟") or {}).get("status") == "retired"
          and (eng.store.intent("观鸟") or {}).get("proto") == "观察"
          and (eng.store.intent("观星") or {}).get("status") == "provisioned")

    # ---- M26 seat idempotent routing (the new shape of
    #      lock-on-entry) + loners still go through the executor
    #      seat -----------
    oinst = ProtoInstance("观察", ws_root / "instances" / "x·观察",
                          "sonnet", spawn=False)
    oinst.host = FakeHost()
    oinst._spawned = True
    eng._xhosts["观察"] = oinst
    n_fx16 = len(fx.delivered)
    c.send(json.dumps({"type": "intent", "name": "观鸟", "input": ""}))
    time.sleep(1.2)                 # give a mistakenly-opened bracket a chance to surface
    check("16 lazy-spawn retired (user ruling 2026-08-23): a "
          "closed-book member's solo fire is rejected -- no "
          "book opened, no step, still zero delivery to a seat",
          eng._bracket_of("观察") is None
          and not any("intent 观鸟" in s and "step" in s
                      for s in oinst.host.sent)
          and len(fx.delivered) == n_fx16)
    c.send(json.dumps({"type": "intent", "name": "挥拍", "input": ""}))
    dt = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "挥拍" and t["status"] == "running"
         and t.get("executor") == "x·solo"), None))
    d17 = wait_for(lambda: dt is not None and fx.delivered
                   and fx.delivered[-1][0] == dt["id"])
    check("17 a loner still runs through x·solo (v17 layering: "
          "a stateless single item isn't bound by book law)",
          dt is not None and bool(d17)
          and not any(f"[task {dt['id']}]" in s for s in fake.sent))
    r = post({"verb": "task_done", "task": dt["id"], "outcome": "ok",
              "summary": "挥好了", "token": "xst21"})
    noti = frame_where(lambda f: f.get("type") == "card"
                       and "Executor done" in str(f.get("title")))
    check("18 §2g completion notify: the card carries summary + "
          "retry-with-note guidance",
          r.get("ok") and noti is not None
          and "挥好了" in noti["body"] and "Retry" in noti["body"])

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)

print()
print("M21 PASS" if not FAILS else f"M21 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
