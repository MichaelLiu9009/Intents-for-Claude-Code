"""Live-acceptance-day repair guard (live incident 2026-08-23 -- five
issues found on the real deck rig) --

(1) intent_retire: no verb existed before this for retirement (could
    only stop-the-engine and hand-edit) -- agent proposes
    -> qual·退役 human gate -> effect soft-retire (drops from the
    roster, keeps history).
(2) _seat_approve with multiple cards stacked: the key always answers
    the newest card, and how many remain must be stated explicitly.
(3) Injection watchdog long-think grace period: during extended
    thinking the transcript emits zero bytes, so the mtime signal
    goes blind -- seeing a thinking marker at the screen tail must
    grant unlimited grace, not false-alarm "swallowed by the wizard".
(4) retry acceptance-card copy: must make explicit that this is the
    acceptance surface for a retry the user themselves initiated.
(5) ask_user free-text capture: the panel's typed-line action=line
    was previously rejected -- forcing the agent to invent a fake
    "manual input" button.

Run: PYTHONIOENCODING=utf-8 python tests/test_fix0823.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel import prune_report               # noqa: E402
from commander.kernel.provision import instance_home    # noqa: E402
from commander.kernel.store import FLOW_RETIRE, Store   # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


def wait_for(fn, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="t", steps="Get-Date 报给用户", fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.intent_create("独令", title="t2", steps="echo", fires=1)
    st.intent_revise("独令", status="provisioned")
    st.compile_delivery("独令")
    st.intent_create("册员", title="m", steps="x", fires=1)
    st.intent_revise("册员", status="provisioned", proto="某册")
    st.close()

    eng = Engine(ws_root, http_port=9788, ws_port=9789, spawn_host=False)
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    # ---- (1) intent_retire: proposal -> human gate -> soft-retire ------
    r = eng._mcp_call({"verb": "intent_retire", "name": "报时",
                       "why": "与册员重复"})
    check("1a proposal opens the gate (gated task)",
          r.get("ok") and r.get("task"))
    t = eng.store.task(r["task"])
    check("1b gate ticket lands on qual·退役", t is not None
          and t.get("spec") == FLOW_RETIRE and t["status"] == "gated")
    r2 = eng._mcp_call({"verb": "intent_retire", "name": "报时"})
    check("1c duplicate proposal doesn't stack a gate "
          "(points back to the original task)",
          r2.get("ok") and r2.get("task") == r["task"])
    check("1d still on the roster before approval",
          any(x["name"] == "报时" for x in eng._intent_menu()))
    eng._on_approve(r["task"])
    check("1e approval soft-retires it immediately",
          (eng.store.intent("报时") or {})
          .get("status") == "retired")
    check("1f off the roster (no longer listed in the IME dictionary)",
          not any(x["name"] == "报时" for x in eng._intent_menu()))
    r3 = eng._mcp_call({"verb": "intent_retire", "name": "报时"})
    check("1g retiring an already-retired intent = reject "
          "(not on the shelf)",
          "not on the shelf" in str(r3.get("error")))
    r4 = eng._mcp_call({"verb": "intent_retire", "name": "册员"})
    check("1h bundle member rejected, signpost points to "
          "resubmitting the whole bundle",
          "workspace_submit" in str(r4.get("error")))
    r5 = eng._mcp_call({"verb": "intent_retire", "name": "不存在"})
    check("1i nonexistent name rejected", "不存在" in str(r5.get("error")))

    # ---- (2) _seat_approve multi-card stacking made explicit -----------
    seat = defaults.XPROTO_PREFIX + defaults.XSOLO_NAME
    c1 = eng._card_open("ask", "老卡", "b",
                        options=[{"action": "a", "label": "A"}],
                        instance=seat)
    c2 = eng._card_open("ask", "新卡", "b",
                        options=[{"action": "b", "label": "B"}],
                        instance=seat)
    ra = eng._seat_approve(seat, "Solo")
    check("2a key answers the newest card", ra.get("card") == c2["id"])
    check("2b remaining card count is stated (left=1)",
          ra.get("left") == 1)
    rb = eng._seat_approve(seat, "Solo")
    check("2c press again, turn goes to the old card, left=0 clears",
          rb.get("card") == c1["id"] and rb.get("left") == 0)

    # ---- (4) consolidate offer copy (reshaped 2026-08-25: the
    # acceptance bracket died — a settled retry raises an offer) -----
    eng._consolidate_offer("intent", "独令", 42,
                           extra="Retry of '独令' settled (ok).")
    with eng._card_lock:
        rc = next((c for c in eng._cards.values()
                   if any(o.get("action") == "consolidate"
                          for o in (c.get("options") or []))), None)
    check("4a consolidate offer: kind=offer (survives cli sweep), "
          "names suspension + the registration-gate revival",
          rc is not None and rc["kind"] == "offer"
          and "Consolidate '独令'?" in rc["title"]
          and "suspended" in rc["body"]
          and "your gate" in rc["body"])

    # ---- (5) ask_user free-text capture (action=line) -------------------
    box = {}

    def ask():
        box["r"] = eng._mcp_call({"verb": "ask_user",
                                  "question": "刷新架构选哪种?",
                                  "options": ["快照页", "本地代理"]})

    th = threading.Thread(target=ask, daemon=True)
    th.start()
    card = wait_for(lambda: next(
        (c for c in list(eng._cards.values())
         if c["kind"] == "ask" and "刷新架构" in c.get("body", "")), None))
    check("5a ask gate card is open", card is not None)
    eng._on_card_answer(card["id"], "line", "两个都不要,纯文本就行")
    th.join(timeout=8)
    ans = box.get("r") or {}
    check("5b typed answer carries through as choice (typed flag)",
          ans.get("choice") == "两个都不要,纯文本就行"
          and ans.get("typed") is True)

    # ---- (3) injection watchdog: long-think grace vs real-drop alarm ---
    class FakeHost:
        def __init__(self):
            self.screen = "✻ still thinking with high effort"

        def alive(self):
            return True

        def replay(self):
            return self.screen

        def inject_chat(self, text):
            pass

    eng.host = FakeHost()
    tdir = prune_report.transcript_dir(instance_home(ws_root, eng.module))
    tdir.mkdir(parents=True, exist_ok=True)
    tf = tdir / "s.jsonl"
    tf.write_text(json.dumps({"type": "user", "message": {
        "content": [{"type": "text", "text": "别的话"}]}}) + "\n",
        encoding="utf-8")
    old = time.time() - 3600
    os.utime(tf, (old, old))  # transcript already quiet (mtime blind spot)
    w = {"wall": time.time(), "t": time.monotonic() - 999,
         "t0": time.monotonic() - 999, "brief": "注入的那句回执"}
    eng._inject_watch = [w]
    eng._inject_ack()
    check("3a thinking marker seen at screen tail -> unlimited "
          "grace (re-enters watch, no alarm)",
          len(eng._inject_watch) == 1)
    with eng._card_lock:
        alarm = any("not have landed" in c.get("title", "")
                    for c in eng._cards.values())
    check("3b zero false-alarm cards during long-think", not alarm)
    eng.host.screen = "$ 普通提示符,没在思考"
    eng._inject_watch[0]["t"] = time.monotonic() - 999
    eng._inject_ack()
    with eng._card_lock:
        alarm = any("not have landed" in c.get("title", "")
                    for c in eng._cards.values())
    check("3c not long-thinking and genuinely dropped -> alarms "
          "as usual", alarm)

    try:
        eng.stop()
    except Exception:
        pass
    # clean up test transcript residue (slug dir under real home)
    try:
        tf.unlink()
        tdir.rmdir()
    except OSError:
        pass

print()
if FAILS:
    print("FIX0823 FAIL:", FAILS)
    sys.exit(1)
print("FIX0823 PASS")
