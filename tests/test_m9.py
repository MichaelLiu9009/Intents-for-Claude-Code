"""M9 guard (v16 rewrite, user ruling on the night of 2026-08-16):
workspace plane --
two-stage process (open ticket -> build workspace, then register-
is-compile), human-approved tools snapshot, M section rides along
with the order, steps is mandatory, chain has left along with the
physical layer's retirement (procedure = a hook slot built into
the engine; this file's original "procedure plane" topic is
entirely voided -- the vocabulary-surface guard now lives in
test_ier 1p-1t / 2g-2l).

Run with: PYTHONIOENCODING=utf-8 python tests/test_m9.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import _ws  # noqa: E402  (§2u two-stage-process helper)

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander.engine import Engine                     # noqa: E402

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
        "http://127.0.0.1:9806/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)

    eng = Engine(ws_root, http_port=9806, ws_port=9807, spawn_host=False)
    fake = FakeHost()
    eng.host = fake

    class FakeXHost:            # §2m v9: executor-slot stand-in
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
    c = connect("ws://127.0.0.1:9807", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))

    # ---- §2u open ticket, build workspace: no human approval,
    # the directory is the artifact ------------------------------------
    r = post({"verb": "intent_submit", "name": "打招呼",
              "scenario": "寒暄", "title": "打个招呼",
              "steps": "1. call 问候 带触发输入\n2. report 把结果说给用户,"
                       "一句",
              "acceptance": "ok: 说出去了\nfailed: 其余"})
    wsd = Path(r["workspace"])
    check("1 §2u ticket-open builds workspace: dir + "
          "declaration + three empty dirs (v16: no procedure/), "
          "**no human gate**",
          r.get("ok") and r.get("status") == "draft" and wsd.is_dir()
          and (wsd / "intent.json").is_file()
          and (wsd / "CLAUDE.md").is_file()
          and all((wsd / x).is_dir() for x in
                  ("tools", "inputs", "records"))
          and not (wsd / "procedure").is_dir()
          and not any(t.get("intent") == "打招呼"
                      and t["status"] == "gated"
                      for t in eng.store.tasks_recent(20)))

    # ---- registration-time validation: declared name -> conventional
    # path ----------------------------
    _ws.edit(eng, "打招呼", tools=["问候"])
    r = _ws.register(post, "打招呼")
    check("2 §2u declared tool missing on disk -> named item by "
          "item (name -> conventional path)",
          "tools/问候.* is not there" in r.get("error", ""))
    _ws.put_tool(eng, "打招呼", "问候", "Write-Output ('hi ' + $args[0])")
    r = _ws.register(post, "打招呼")
    gate_tid = r.get("task")
    check("3 §2u register = snapshot stamped with hash + one "
          "card awaiting human approval to 「take effect」",
          r.get("ok")
          and eng.store.task(gate_tid)["status"] == "gated")
    tpl = (ws_root / "runtime" / "tasks" / str(gate_tid)
           / "template.md").read_text(encoding="utf-8")
    check("4 §2u registration card **doesn't dump full text**: "
          "only lists items taking effect + hash + directory",
          "tools/问候.ps1" in tpl and "taking effect" in tpl
          and str(wsd) in tpl and "Write-Output" not in tpl)

    # ---- human approval promotes it -> trigger: M section rides
    # along with the order -------------------------------------
    c.send(json.dumps({"type": "approve", "task": gate_tid}))
    up = wait_for(lambda: (eng.store.intent("打招呼") or {})
                  .get("status") == "provisioned")
    check("5 §2u one card approves the complete item: approval "
          "provisions it immediately", bool(up))
    r = post({"verb": "intent_catalog"})
    check("5b v16 catalog no longer lists procedures "
          "separately (nothing on the agent side to bind a "
          "prelude to -- physical-layer vocabulary stays "
          "hidden from the agent)",
          r.get("ok") and "procedures" not in r)
    c.send(json.dumps({"type": "intent", "name": "打招呼",
                       "input": "世界"}))
    t1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("intent") == "打招呼" and t["status"] == "running"
         and t.get("executor") == "x·solo"
         and str(t.get("spec")).startswith("deliver:")), None))
    check("6 v16 trigger delivers straight to the executor "
          "seat: single-node delivery chain, first ring is "
          "deliver (no engine prelude ring)",
          t1 is not None
          and len(eng.store.chain(t1["chain_id"])) == 1)
    pkg = (ws_root / "runtime" / "tasks" / str(t1["id"])
           / "package.md").read_text(encoding="utf-8")
    check("7 §2u trigger input goes into the I section; M "
          "section carries the registered tool's real path "
          "(name maps to path)",
          "世界" in pkg and "M · methods" in pkg
          and "问候.ps1" in pkg)
    post({"verb": "task_done", "task": t1["id"], "outcome": "ok",
          "summary": "招呼打了", "token": "xst"})
    ok_done = wait_for(lambda: (eng.store.task(t1["id"]) or {})
                       .get("status") == "done")
    check("8 booking ok -> task done, intent stays listed",
          bool(ok_done)
          and (eng.store.intent("打招呼") or {})
          .get("status") == "provisioned")

    # ---- v16 single form: steps mandatory, chain rejected ----------------
    post({"verb": "intent_submit", "name": "空壳", "scenario": "空壳"})
    r = _ws.register(post, "空壳")
    check("9 v16 steps is mandatory: empty-shell registration "
          "rejected (the chain half of the old 「both-empty "
          "reject」 retired along with the physical layer)",
          "steps" in r.get("error", "")
          and "required" in r.get("error", ""))
    _ws.edit(eng, "空壳", chain=["ime"])
    r = _ws.register(post, "空壳")
    check("10 v16 chain stuffed into the declaration -> "
          "CASELAW 25 names the unknown key (hard reject, not "
          "silent)",
          "unknown fields" in r.get("error", "")
          and "chain" in r.get("error", ""))

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)

print()
print("M9 PASS" if not FAILS else f"M9 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
