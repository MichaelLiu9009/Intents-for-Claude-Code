"""M2 guard: runner vertical slice (no-host mode) -- preload render /
phrase trigger / breakpoint not silent / gate approve verb.

Run: PYTHONIOENCODING=utf-8 python tests/test_m2.py
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel.store import Store                # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)

    # ---- seed (seed goes via store, before engine boots) --------------
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", scenario="随口一问",
                     steps="Get-Date 报给用户", fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.spec_put("试闸", head="sidecar", priority=0, consequence="测 gate",
                steps=[{"assignee": "user", "kind": "gate",
                        "gate": "批一下"}])
    g0 = st.chain_start("试闸", issuer="sidecar")
    st.close()

    eng = Engine(ws_root, http_port=9760, ws_port=9761, spawn_host=False)
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ---- §3 preload: approved renders into CLAUDE.md ------------------
    md = (ws_root / "instances" / "sidecar" / "CLAUDE.md").read_text(
        encoding="utf-8")
    # §3c (2026-08-11): preload retired, provision now self-fetches on
    # boot -- CLAUDE.md keeps only the byte-stable instruction face,
    # the intent face goes through intent_memory_index
    check("1 §3c provision self-fetches on boot: CLAUDE.md carries "
          "the instruction face, not the listing",
          "intent_memory_index" in md and "报时" not in md)

    from websockets.sync.client import connect
    with connect("ws://127.0.0.1:9761", open_timeout=5) as c:
        c.send(json.dumps({"type": "hello"}))

        def recv_until(kind, timeout=8):
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    f = json.loads(c.recv(timeout=timeout))
                except Exception:
                    return None
                if f.get("type") == kind:
                    return f
            return None

        recv_until("surface")
        menu = recv_until("intents")
        check("2 §3 IME dictionary face delivered with hello "
              "(name+title)",
              menu is not None and {"name": "报时", "title": "报告当前时间"}
              in menu["rows"])
        # ---- §3 explicit trigger -> delivery chain; host absent =
        # breakpoint not silent -----------------------------------------
        # (IME ruling: engine doesn't sniff chat text, trigger is an
        # explicit UI action)
        c.send(json.dumps({"type": "chat", "text": "报时"}))
        c.send(json.dumps({"type": "intent", "name": "报时",
                           "input": "顺便带上星期几"}))
        deadline = time.time() + 8
        row = None
        while time.time() - deadline < 0:
            f = recv_until("chains")
            if f is None:
                break
            row = next((r for r in f["rows"]
                        if r.get("intent") == "报时"
                        and r.get("status") == "failed"), None)
            if row:
                break
        check("2b §3 explicit trigger opens a chain; plain chat "
              "doesn't; no host -> failed breakpoint",
              row is not None and row["spec"] == "deliver:报时"
              and sum(1 for r in f["rows"]
                      if r.get("intent") == "报时") == 1)
        check("2c §3 IME v2: params (payload) ride along with the "
              "chain row",
              row.get("payload") == "顺便带上星期几")

        # ---- §6 gate approve verb: only moves once a human clicks -----
        c.send(json.dumps({"type": "approve", "task": g0["id"]}))
        f = recv_until("chains")
        ok = (f is not None and any(
            r["id"] == g0["id"] and r["status"] == "done" for r in f["rows"]))
        check("3 §6 approve verb: gated -> done (chain complete, "
              "single-step spec)", ok)

        c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    check("4 clean shutdown", not th.is_alive())

    # ---- journal: breakpoint is logged loudly -------------------------
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    check("5 §6 breakpoint logged to journal (kind=chain "
          "name=breakpoint, cause written separately -- graceful "
          "fail)",
          any(r["kind"] == "chain" and r["name"] == "breakpoint"
              and "host" in r.get("reason", "") for r in rows))

print()
print("M2 PASS" if not FAILS else f"M2 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
