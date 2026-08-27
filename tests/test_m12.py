"""M12 guard: flow graph (typed node graph) -- edge-table/DB
reconciliation, entry three-way separation of powers, token travel
(cross-chain continuation), hop guardrail, cancel constitutional duty.

v16 (user's call, night of 2026-08-16): the procedure node retired
along with the physical layer; delivery chain = single deliver node;
qual·rework currently has no production inbound edge (left for future
wiring when E-layer failures are hooked up) -- this file instead uses
head (engine) to open the chain directly and drive the rework loop,
with the mechanism guard kept as-is; the intent-suspended assertion
retired together with the suspend effect (a physical-layer blowup
reports to the human; the intent itself is innocent and stays live).

Run: PYTHONIOENCODING=utf-8 python tests/test_m12.py
"""
import json
import queue
import sqlite3
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
from commander.kernel.store import (                    # noqa: E402
    FLOW_QUAL_NEW, FLOW_QUAL_REWORK, Store)

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
        "http://127.0.0.1:9816/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


defaults.MAX_NODE_VISITS = 2        # guardrail testable: two rounds
                                     # of rework hits the cap

with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)

    # ---- Seed: two ordinary intents (v16 single form) ----------------
    st = Store(ws_root / "state.db")
    for name in ("坏蛋", "坏蛋二"):
        st.intent_create(name, title=name, steps="1. report 一句",
                         cls="utility")
        st.intent_revise(name, status="provisioned")
        st.compile_delivery(name)
    st.close()

    # Simulate a legacy DB: knock the compiled artifact back to its old
    # value (a fossil node with assignee=engine) -- recompiling on boot
    # should self-heal back to the blueprint value (single deliver node
    # delivering to x·solo)
    db = sqlite3.connect(str(ws_root / "state.db"))
    db.execute("UPDATE chain_spec_steps SET assignee='engine', "
               "kind='procedure' WHERE spec='deliver:坏蛋'")
    db.commit()
    db.close()

    eng = Engine(ws_root, http_port=9816, ws_port=9817, spawn_host=False)
    fake = FakeHost()
    eng.host = fake

    class FakeXHost:            # execution-slot double (guard: a
                                 # prelude blowup must not deliver)
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

    xfake = FakeXHost()
    eng._xhosts["solo"] = xfake
    eng._tokens["xst"] = "x·solo"
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    # ---- ① Edge table <-> DB reconciliation (blueprint is truth;
    # docs/M12-FLOW.md §②) ----------
    EDGES = {
        (FLOW_QUAL_NEW, 0): {
            "assignee": "user", "kind": "gate", "accounting": "test",
            "template": "template", "effect": "ok:provision",
            "on_ok": "end", "on_fail": "end"},
        (FLOW_QUAL_REWORK, 0): {
            "assignee": "sidecar", "kind": "deliver",
            "accounting": "test", "template": "debug", "effect": None,
            "on_ok": "next", "on_fail": "end"},
        (FLOW_QUAL_REWORK, 1): {
            "assignee": "sidecar", "kind": "deliver",
            "accounting": "test", "template": "sim",
            "effect": "ok:reprovision", "on_ok": "end",
            "on_fail": f"{FLOW_QUAL_REWORK}:0"},
        ("validate", 0): {
            "assignee": "sidecar", "kind": "deliver",
            "accounting": "test", "template": "sim", "effect": None,
            "on_ok": "end", "on_fail": "end"},
        # R5 two-round ruling (2026-08-23): retry = single-ring
        # bracket (retry-fulfill delivers to sidecar, real ledger;
        # the claim/acceptance ring is intercepted in task_done, not
        # in the edge table)
        ("retry", 0): {
            "assignee": "sidecar", "kind": "deliver",
            "accounting": "real", "template": "retry-fulfill",
            "effect": None, "on_ok": "end", "on_fail": "end"},
        # v16 physical-layer ruling: procedure node retired -- the
        # delivery chain = single deliver node, no effect, no
        # rerouting (a physical-layer blowup reports to the human,
        # it does not suspend the intent)
        ("deliver:坏蛋", 0): {
            "assignee": "x·solo", "kind": "deliver",
            "accounting": "real", "effect": None,
            "on_ok": "end", "on_fail": "end"},
    }
    bad = []
    for (spec, seq), want in EDGES.items():
        node = eng.store.node(spec, seq) or {}
        for k, v in want.items():
            if node.get(k) != v:
                bad.append(f"{spec}:{seq}.{k}={node.get(k)!r}≠{v!r}")
    check("1 M12 edge table <-> DB reconciliation: four flows + "
          "compiled artifacts, five node attributes match "
          "cell-by-cell"
          + ("" if not bad else " | " + "; ".join(bad[:4])), not bad)
    check("1b M12 edge row self-heals on boot (deliver spec "
          "recompiles back to the blueprint value: fossil "
          "procedure node overwritten by a single deliver node) "
          "+ legacy-name sweep "
          "(intent-creation / debug / qual·procedure retired)",
          eng.store.node("deliver:坏蛋", 0)["kind"] == "deliver"
          and eng.store.node("deliver:坏蛋", 0)["assignee"] == "x·solo"
          and eng.store.node("deliver:坏蛋", 1) is None
          and eng.store.spec("intent-creation") is None
          and eng.store.spec("debug") is None
          and eng.store.spec("qual·procedure") is None)

    # ---- ② Entry separation of powers: qual·rework can only be entered
    # via edge; agents cannot initiate it ------------
    try:
        eng.store.chain_start(FLOW_QUAL_REWORK, issuer="sidecar",
                              intent="坏蛋")
        breached = True
    except PermissionError:
        breached = False
    check("2 M12 entry table: qual·rework head=engine, "
          "sidecar-initiated rejected (chains don't open chains, "
          "humans open chains, rerouting stays inside the edge)",
          not breached)

    # ---- ③ token travel + hop guardrail (MAX_NODE_VISITS=2) -------------
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9817", open_timeout=5)
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

    def rework_ring(seq, exclude=()):
        return next(
            (t for t in eng.store.tasks_recent(40)
             if t.get("spec") == FLOW_QUAL_REWORK and t["seq"] == seq
             and t["status"] == "running"
             and t["id"] not in exclude), None)

    # v16: rework loop currently has no production inbound edge (a
    # physical-layer blowup reports to the human) -- head opens it
    # directly to drive the mechanism guard (swap back to edge-entry
    # here once E-layer failures get wired up)
    t0 = eng.store.chain_start(FLOW_QUAL_REWORK, issuer="engine",
                               intent="坏蛋", payload="参数")
    n0 = wait_for(lambda: rework_ring(0))
    check("3 M12 token travel: head opens the chain, first ring "
          "is n0 (payload/issuer travel as baggage); v16 no "
          "suspension -- intent stays live as usual",
          n0 is not None and n0["id"] == t0["id"]
          and n0["payload"] == "参数" and n0["issuer"] == "engine"
          and eng.store.intent("坏蛋")["status"] == "provisioned")
    post({"verb": "task_done", "task": n0["id"], "outcome": "ok",
          "summary": "修一轮"})
    n1 = wait_for(lambda: rework_ring(1))
    post({"verb": "task_done", "task": n1["id"], "outcome": "failed",
          "summary": "没修好"})
    n0b = wait_for(lambda: rework_ring(0, exclude={n0["id"]}))
    check("4 M12 back-edge: sim fails, reworks n0 (second visit, "
          "within hop count)",
          n1 is not None and n0b is not None
          and n0b["chain_id"] == n0["chain_id"]
          and n0b["origin"] == n1["id"])
    post({"verb": "task_done", "task": n0b["id"], "outcome": "ok",
          "summary": "再修一轮"})
    n1b = wait_for(lambda: rework_ring(1, exclude={n1["id"]}))
    check("5 M12 second-round sim (n1 second visit)",
          n1b is not None)
    post({"verb": "task_done", "task": n1b["id"], "outcome": "failed",
          "summary": "还是没修好"})
    said = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "loop cap" in f.get("text", ""))
    live = [t for t in eng.store.chain(n0["chain_id"])
            if t["status"] in ("queued", "running", "gated")]
    check("6 M12 hop guardrail: n0 visited twice, third visit "
          "blocked -- chain halts pending human (loud on chat "
          "surface); v16 no suspension, intent stays live as "
          "usual",
          said is not None and not live
          and eng.store.intent("坏蛋")["status"] == "provisioned")
    led = {x["chain"]: x for x in eng.store.chains_recent(30)}
    check("6b M12 travel ledger shows: one row per journey "
          "(deliver:坏蛋 -> qual·rework), final state failed",
          led[n0["chain_id"]]["spec"] == FLOW_QUAL_REWORK
          and led[n0["chain_id"]]["status"] == "failed")

    # ---- ④ cancel halts the chain: effect halts too, intent stays at
    # draft ------------------
    eng.store.chain_start(FLOW_QUAL_REWORK, issuer="engine",
                          intent="坏蛋二", payload="")
    m0 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(40)
         if t.get("intent") == "坏蛋二"
         and t.get("spec") == FLOW_QUAL_REWORK and t["seq"] == 0
         and t["status"] == "running"), None))
    c.send(json.dumps({"type": "cancel", "chain": m0["chain_id"]}))
    wait_for(lambda: eng.store.task(m0["id"])["status"] == "cancelled")
    r = post({"verb": "task_done", "task": m0["id"], "outcome": "ok",
              "summary": "修好了"})
    time.sleep(1.0)
    n1c = next((t for t in eng.store.chain(m0["chain_id"])
                if t["seq"] == 1
                and t.get("spec") == FLOW_QUAL_REWORK), None)
    check("7 M12 unified cancel covers rework: the running ring is "
          "interrupted now (cancelled), a late settlement is "
          "refused, no n1 spawns (effect halts)",
          "error" in r and n1c is None
          and eng.store.task(m0["id"])["status"] == "cancelled")

    # ---- journal reconciliation -----------------------------------
    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    routes = [r for r in rows if (r["kind"], r["name"])
              == ("chain", "route")]
    check("8 journal: route(edge=fail,to=qual·rework:0) x back-jump "
          "count / loop-limit all logged (v16: reroute-into-edge "
          "retired, remaining back-edges are just n1->n0 rework)",
          ("chain", "loop-limit") in names
          and sum(1 for r in routes
                  if r.get("to") == f"{FLOW_QUAL_REWORK}:0") >= 1)

print()
print("M12 PASS" if not FAILS else f"M12 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
