"""M11 guard: knowledge three-layer (skills casting + CLAUDE.md
slimming).

The former second half (pure procedure-chain self-handling /
firing-failed rework loop) has fully retired along with the v16
physical-layer ruling (user's call, night of 2026-08-16): procedure =
engine-builtin key binding, not part of the delivery chain; qual·rework
currently has no inbound edge (left for future wiring when E-layer
failures are hooked up -- at that point the rework-loop guard should
be restored here).

Run: PYTHONIOENCODING=utf-8 python tests/test_m11.py
"""
import json
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

import _ws  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel.store import FLOW_QUAL_REWORK, Store  # noqa: E402

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


with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)

    # ---- Seed: one ordinary intent (guard just needs life) ----
    st = Store(ws_root / "state.db")
    st.intent_create("回声", title="回一声", steps="1. report 一句",
                     cls="utility")
    st.intent_revise("回声", status="provisioned")
    st.compile_delivery("回声")
    st.close()

    # Stale leftover (pre-rename creation/) -- casting should
    # overwrite and clear the whole region
    stale = (ws_root / "instances" / "sidecar" / ".claude" / "skills"
             / "creation")
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("旧册", encoding="utf-8")

    eng = Engine(ws_root, http_port=9812, ws_port=9813, spawn_host=False)
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

    # ---- Knowledge three-layer: skills casting + CLAUDE.md slimming --
    home = ws_root / "instances" / "sidecar"
    sk1 = home / ".claude" / "skills" / "task-delivery" / "SKILL.md"
    sk2 = home / ".claude" / "skills" / "intent-creation" / "SKILL.md"
    check("1 §3c skills land via casting (task-delivery / "
          "intent-creation), stale region fully cleared",
          sk1.is_file() and sk2.is_file() and not stale.exists()
          and "name: task-delivery" in sk1.read_text(encoding="utf-8")
          and "name: intent-creation" in sk2.read_text(encoding="utf-8"))
    check("1b §3c role trim: sidecar doesn't cast mode-creation "
          "(knowledge pack = role definition)",
          not (home / ".claude" / "skills" / "mode-creation").exists()
          and not hasattr(defaults, "SKILL_MODE_CREATION_MD"))
    md = (home / "CLAUDE.md").read_text(encoding="utf-8")
    check("2 §3c CLAUDE.md slimmed: iron rules kept + points to "
          "two skill books (report-back duty retired -- user's "
          "call 2026-08-12)",
          "task_done" in md and "report_to_user" not in md
          and "task-delivery" in md and "intent-creation" in md
          and "class" not in md.lower())
    sk2_text = sk2.read_text(encoding="utf-8")
    check("3 §5 intent-creation carries §2u two-phase submit "
          "flow + v16 physical-layer boundary (can write tools, "
          "can't write procedure); old procedure contract and "
          "wrap-up flow are both absent from the sidecar book",
          "workspace_submit" in sk2_text
          and "physical layer" in sk2_text
          and "def run(ctx)" not in sk2_text
          and "steps 引用律" not in sk2_text
          and "收束流程" not in sk2_text)

    # ---- v16: second half retired (procedure chain / rework loop) --
    # trigger one ordinary intent to verify the seam between the
    # knowledge layer and the execution layer still connects
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9813", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    c.send(json.dumps({"type": "intent", "name": "回声", "input": ""}))
    t1 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(10)
         if t.get("intent") == "回声" and t["status"] == "running"
         and t.get("executor") == "x·solo"), None))
    check("4 v16 trigger delivers straight to the execution "
          "slot (single-hop delivery chain; engine self-loop "
          "retired along with the physical layer)",
          t1 is not None and len(xfake.delivered) == 1)

    # ---- journal reconciliation -----------------------------------
    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    check("5 journal: deliver recorded; procedure/firing-failed "
          "lines no longer appear (that path retired along with "
          "the physical layer)",
          ("chain", "deliver") in names
          and ("chain", "procedure") not in names
          and ("chain", "firing-failed") not in names)

print()
print("M11 PASS" if not FAILS else f"M11 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
