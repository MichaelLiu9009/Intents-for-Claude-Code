"""·启/·收 made-real guard (user ruling 2026-08-24) --

Open/close are **two system-native steps**, not members: the
protocol-open envelope carries a ·启 section (the protocol's
declared prep, defaulting to a greeting/standing-by); Shutdown first
delivers a ·收 step (the protocol's declared wrapup, defaulting to
wrapping up whatever is in flight), and only books + closes the seat
**after** step_done(·收) or the grace clock -- fixing the live
observation that "close-out just killed the seat outright, with no
semantic graceful shutdown". Pressing Shutdown again = force;
engine shutdown cascades into force too. A user-declared member
cannot occupy a reserved slot.

Run: PYTHONIOENCODING=utf-8 python tests/test_wrapup.py
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

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


def wait_for(fn, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


class LiveInst(ProtoInstance):
    """Live-seat stand-in: doesn't spin up a real PTY, only keeps an
    envelope queue + ledger surface (must pass isinstance)."""

    def __init__(self, pname):
        self.pname = pname
        self.seat = defaults.XPROTO_PREFIX + pname
        self._pending, self._steps = [], []
        self.step_name = None
        self.step_state = None
        self.last_output = time.monotonic()
        self._lock = threading.Lock()
        self._step_ready = lambda: True
        self.wrap_evt = threading.Event()
        self._spawned = True
        self.stopped = []

        class H:
            def alive(self):
                return True
        self.host = H()

    def stop(self, graceful=False):
        self.stopped.append(graceful)
        self._spawned = False


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    # v19 migration landed: protocols grew two columns, prep/wrapup
    st.proto_stage("练琴", subtype="interactive", boundary="", cls="乐",
                   scenario="练琴", staged_hash="h1",
                   prep="读册内态,报上次练到哪", wrapup="流水结算落盘,一句收场白")
    p = st.proto_approve("练琴")
    check("0a v19: prep/wrapup persist at stage, carried through approve",
          p is not None and p["prep"].startswith("读册内态")
          and p["wrapup"].startswith("流水结算"))
    st.proto_stage("画画", subtype="interactive", boundary="", cls="艺",
                   scenario="画画", staged_hash="h2")  # no wrapup (default path)
    st.proto_approve("画画")
    st.close()

    # ---- 0b. schema gate: a declared wrapup is refused with the
    # teaching reason (field retired 2026-08-26 — engine-owned) ----
    from commander.kernel import wspace as _wsp
    probs = _wsp.validate({"name": "画画", "scenario": "画画",
                           "subtype": "interactive",
                           "wrapup": "自定义收场"}, "protocol")
    check("0b declared wrapup refused at the schema gate with the "
          "teaching reason, not a generic unknown-field",
          any("engine-owned" in p for p in probs)
          and not any("unknown fields" in p and "wrapup" in p
                      for p in probs))

    eng = Engine(ws_root, http_port=9768, ws_port=9769, spawn_host=False)
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    # ---- 1 . close-out ritual: ·收 goes first, booking + closing the
    #        seat only happens after step_done releases it -----------
    br = eng.store.chain_start("protocol:练琴", issuer="user", intent="练琴")
    inst = LiveInst("练琴")
    eng._xhosts["练琴"] = inst
    eng._tokens["ptk"] = defaults.XPROTO_PREFIX + "练琴"

    r = eng._proto_shutdown("练琴")
    check("1a Shutdown posts ·收 step first — the engine-owned "
          "final-cleanup contract, the declared wrapup never rides "
          "(user ruling 2026-08-26: a declared one blocked shutdown)",
          r.get("note") == "wrap-up first" and len(inst._steps) == 1
          and "·收" in inst._steps[0]
          and "FINAL cleanup" in inst._steps[0]
          and "流水结算落盘" not in inst._steps[0])
    check("1b Step bar flips to ·收 running",
          inst.step_name == "·收" and inst.step_state == "running")
    check("1c Mid-ritual: bracket not booked yet (still open)",
          eng._bracket_of("练琴") is not None and "练琴" in eng._wrapping)
    check("1d Seat not killed", inst.stopped == [])
    ans = eng._mcp_call({"verb": "step_done", "member": "·收",
                         "token": "ptk"})
    check("1e step_done(·收) accepted (exec-face verb)",
          ans.get("ok") is True
          or "member" in str(ans))
    done = wait_for(lambda: (eng.store.task(br["id"]) or {})
                    .get("status") == "done" and inst.stopped)
    check("1f After ack: books + closes seat gracefully",
          done is not None
          and inst.stopped == [True] and "练琴" not in eng._wrapping)

    # ---- 2 . second press forces it: pressing Shutdown again while
    #        the ritual is in flight = finishes immediately ----------
    br2 = eng.store.chain_start("protocol:画画", issuer="user", intent="画画")
    inst2 = LiveInst("画画")
    eng._xhosts["画画"] = inst2
    r = eng._proto_shutdown("画画")
    check("2a Nothing declared: the same fixed contract delivers "
          "(one wrapup for every booklet)",
          r.get("note") == "wrap-up first"
          and "FINAL cleanup" in inst2._steps[0])
    r2 = eng._proto_shutdown("画画")
    check("2b Second press = force (wakes the teardown thread)",
          r2.get("note") == "forcing close")
    done = wait_for(lambda: (eng.store.task(br2["id"]) or {})
                    .get("status") == "done" and inst2.stopped)
    check("2c After force: books + closes seat", done is not None
          and inst2.stopped == [True])

    # ---- 3 . force=True (engine shutdown cascades into this):
    #        skips the ritual and lands directly ----------------------
    br3 = eng.store.chain_start("protocol:练琴", issuer="user", intent="练琴")
    inst3 = LiveInst("练琴")
    eng._xhosts["练琴"] = inst3
    r = eng._proto_shutdown("练琴", force=True)
    check("3a force skips ritual (no ·收 posted, books directly)",
          r.get("note") != "wrap-up first" and inst3._steps == [])
    done = wait_for(lambda: (eng.store.task(br3["id"]) or {})
                    .get("status") == "done" and inst3.stopped)
    check("3b After force: books + closes seat", done is not None)

    # ---- 4 . protocol-open envelope carries a ·启 section (template
    #        slot) ------------------------------------------------------
    check("4a PROTOCOL_PACKAGE_MD has a {prep} slot",
          "{prep}" in defaults.PROTOCOL_PACKAGE_MD
          and "·启" in defaults.PROTOCOL_PACKAGE_MD)
    body = defaults.PROTOCOL_PACKAGE_MD.format(
        name="练琴", tid=1, input="(none)", members="a",
        prep="读册内态,报上次练到哪", roster="-", skill="s")
    check("4b prep content renders into open-book envelope",
          "读上次练到哪" in body
          or "读册内态" in body)

    # ---- 5 . reserved slots: a user member cannot occupy ·启/·收 -------
    check("5a Reserved-name roster complete (·启/·收/开启/结束/收场)",
          {"·启", "·收", "开启", "结束", "收场"}
          <= set(defaults.PROTO_RESERVED_MEMBERS))

    # ---- 6 . draining + teardown sped up (pending order 2026-08-24) ---
    st6 = eng._on_trigger({"engine": "status"})
    check("6a Normally engine=status is not draining",
          st6.get("status") == "up" and st6.get("draining") is False)
    br6 = eng.store.chain_start("protocol:画画", issuer="user",
                                intent="画画")
    inst6 = LiveInst("画画")
    eng._xhosts["画画"] = inst6
    eng._proto_shutdown("画画")     # ·收 ritual opens -> _wrapping
    ps6 = eng._on_trigger({"protocol": "画画", "op": "status"})
    check("6b Mid-ritual op=status reports draining (word + flag)",
          ps6.get("status") == "draining" and ps6.get("draining") is True)
    eng._proto_shutdown("画画")     # second press forces the ritual to finish
    wait_for(lambda: "画画" not in eng._wrapping and inst6.stopped)

    class Stubborn(LiveInst):
        """stop doesn't clear _spawned: holds alive true, pinning the
        draining window open for the assertion."""
        def stop(self, graceful=False):
            self.stopped.append(graceful)

    hold = Stubborn("画画")
    eng._xhosts["画画"] = hold
    eng.host = None                # skip the host's /exit section
    r1 = eng._engine_shutdown()
    r2 = eng._engine_shutdown()
    es6 = eng._on_trigger({"engine": "status"})
    check("6c shutdown reentry idempotent (no second teardown thread)",
          r1.get("ok") is True and r2.get("note") == "already draining")
    check("6d Mid-teardown engine=status reports draining",
          es6.get("status") == "draining" and es6.get("draining") is True)
    t6 = time.monotonic()
    hold._spawned = False       # seat dies -> polling exits immediately
    check("6e teardown sped up: seat dies -> exits immediately, no "
          "full-tick wait (old version slept a fixed 7s)",
          eng._stop.wait(3.0) and time.monotonic() - t6 < 3.0)

    try:
        eng.stop()
    except Exception:
        pass

print()
if FAILS:
    print("WRAPUP FAIL:", FAILS)
    sys.exit(1)
print("WRAPUP PASS")
