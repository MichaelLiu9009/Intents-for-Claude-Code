"""P1 double-fix guard (live-incident precedent 2026-08-23, from the
standalone-version from-scratch rebuild, review 5 §3) --

P1-a: x·solo's allow floor goes through the spawn flag
(--allowedTools) -- when the headless home has no trust record the
harness doesn't honor settings' allow, and booking (task_done) had
been falling through to a perm_gate card popping up; also, while a
gate card is pending, the timeout rule must not reap the task, and
the timer must reset once the gate clears (time spent waiting on a
human doesn't count against the machine clock -- proven live by
incident A, where finished work got judged dead).

P1-b: PtyHost's re-render on first trust flip -- the wizard screen's
bytes satisfy the ready() probe, so releasing the gate the instant
trust flips true means opening-injection lands in the re-render's
blank window (a freshly opened seat is always blind on first open).
Reset only fires once the wizard has actually been seen; a
seat that was already trusted does not re-render (an idle seat
emits zero new bytes, so resetting would just hang it forever).

Run: PYTHONIOENCODING=utf-8 python tests/test_p1fix.py
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.host.headless import HeadlessHost        # noqa: E402
from commander.host import pty as pty_mod               # noqa: E402
from commander.kernel.provision import solo_allow_rules  # noqa: E402
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


# ---- 1 . P1-a floor flag: spawn_args carries --allowedTools -------------
with tempfile.TemporaryDirectory() as tmp:
    ws = Path(tmp)
    rules = solo_allow_rules(ws, ["Bash(git log:*)", "mcp__intentOS"])
    check("1a solo_allow_rules contains mcp__intentOS and "
          "dedupes",
          rules.count("mcp__intentOS") == 1)
    check("1b solo_allow_rules contains the toolkit read floor",
          any(r.startswith("Read(") and "toolkit" in r for r in rules))
    h = HeadlessHost(ws, "sonnet", ws / "tasks",
                     perm_tool="mcp__intentOS__perm_gate",
                     tools="Bash,Read", allow_tools=rules)
    h._cli = "claude"                # don't actually spawn the CLI,
                                      # just check the command line
    args = h.spawn_args("sid-1")
    check("1c spawn_args has --allowedTools", "--allowedTools" in args)
    i = args.index("--allowedTools")
    check("1d variadic flag is last and rules are complete",
          args[i + 1:] == rules)
    h2 = HeadlessHost(ws, "sonnet", ws / "tasks")
    h2._cli = "claude"
    check("1e no allow_tools passed means no flag (the floor is "
          "the caller's job)",
          "--allowedTools" not in h2.spawn_args("sid-2"))
    # Permission surface consolidation (2026-08-24): mode is
    # pre-written by the engine's spawn flag, read dynamically from
    # SEAT_PERMISSION_MODE at spawn time (config.json override
    # applies)
    check("1f spawn_args pre-writes --permission-mode (default "
          "auto)",
          "--permission-mode" in args
          and args[args.index("--permission-mode") + 1] == "auto"
          and args.index("--permission-mode") < args.index("--tools"))
    old_mode = defaults.SEAT_PERMISSION_MODE
    try:
        defaults.SEAT_PERMISSION_MODE = "acceptEdits"
        a3 = h.spawn_args("sid-3")
        check("1g the knob takes effect dynamically (config "
              "surface can change it)",
              a3[a3.index("--permission-mode") + 1] == "acceptEdits")
        defaults.SEAT_PERMISSION_MODE = ""
        check("1h empty string = no flag (escape hatch, falls "
              "back to the harness default)",
              "--permission-mode" not in h.spawn_args("sid-4"))
    finally:
        defaults.SEAT_PERMISSION_MODE = old_mode

# ---- 2 . P1-b re-render on first trust flip ------------------------------
with tempfile.TemporaryDirectory() as tmp:
    fake_home = Path(tmp) / "userhome"
    fake_home.mkdir()
    seat = Path(tmp) / "ws" / "instances" / "x·t"
    seat.mkdir(parents=True)
    cfg = fake_home / ".claude.json"
    real_home = Path.home
    try:
        Path.home = staticmethod(lambda: fake_home)   # noqa: E731

        # Scenario 1: wizard first, trust second -> hangs a
        # re-render gate (second fix: a quiet window, not a byte
        # count reset)
        cfg.write_text(json.dumps({"projects": {}}), encoding="utf-8")
        host = pty_mod.PtyHost(seat, model="sonnet")
        host._out_bytes = 9999          # probe satisfied by wizard screen
        host._born = time.monotonic() - 999
        host._last_out = time.monotonic()      # re-render spray in progress
        check("2a no trust record = untrusted", host.trusted() is False)
        cfg.write_text(json.dumps({"projects": {
            str(seat): {"hasTrustDialogAccepted": True}}}),
            encoding="utf-8")
        check("2b trusted flips true after acceptance",
              host.trusted() is True)
        check("2c screen-flip gate engages: doesn't release "
              "while the spray hasn't stopped",
              host._flip_t is not None and not host.ready())
        # simulate re-render ending (flip past SETTLE and quiet past
        # QUIET)
        host._flip_t = time.monotonic() - 4.0
        host._last_out = time.monotonic() - 2.0
        check("2d gate releases once the quiet window is met, "
              "and clears itself once",
              host.ready() and host._flip_t is None)
        # hard cap: continuous repaint still doesn't lock up
        host3 = pty_mod.PtyHost(seat, model="sonnet")
        host3._out_bytes = 9999
        host3._born = time.monotonic() - 999
        host3._saw_untrusted = True
        host3._trusted = True
        host3._flip_t = time.monotonic() - 16.0
        host3._last_out = time.monotonic()     # keeps emitting bytes
        check("2d2 hard CAP forces the gate open (repaint "
              "doesn't lock up)", host3.ready())

        # Scenario 1 addendum: flush check ordering (third fix) --
        # trusted() registers the flip first, and the immediately
        # following ready() must see the gate (same tick doesn't
        # release)
        cfg.write_text(json.dumps({"projects": {}}), encoding="utf-8")
        host4 = pty_mod.PtyHost(seat, model="sonnet")
        host4._out_bytes = 9999
        host4._born = time.monotonic() - 999
        host4._last_out = time.monotonic()
        check("2g trusted is False during the wizard (and "
              "registers _saw_untrusted)",
              host4.trusted() is False and host4._saw_untrusted)
        cfg.write_text(json.dumps({"projects": {
            str(seat): {"hasTrustDialogAccepted": True}}}),
            encoding="utf-8")
        # flush order: trusted() first (registers flip) -> ready()
        # after (holds as soon as it sees the gate)
        t_ok = host4.trusted()
        r_ok = host4.ready()
        check("2h same tick doesn't release: the tick trusted "
              "flips true, ready must be False",
              t_ok and not r_ok)

        # Scenario 2: already trusted -> no gate hangs (protects
        # idle seats)
        host2 = pty_mod.PtyHost(seat, model="sonnet")
        host2._out_bytes = 9999
        host2._born = time.monotonic() - 999
        check("2e already-trusted seat is trusted immediately",
              host2.trusted() is True)
        check("2f no gate when the wizard was never seen (idle "
              "seat injection doesn't hang)",
              host2._flip_t is None and host2.ready())
    finally:
        Path.home = real_home

# ---- 3 . P1-a gate clock: pending gate isn't reaped + timer resets ------
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="t", steps="Get-Date 报给用户", fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9778, ws_port=9779, spawn_host=False)

    class FakeXHost:
        def alive(self):
            return True

        def deliver(self, tid, line):
            return "sid"

        def reap(self, tid):
            pass

        def stop(self):
            pass

    eng._xhosts["solo"] = FakeXHost()
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)
    eng._on_intent("报时", "", by="test")
    t = wait_for(lambda: next(
        (x for x in eng.store.tasks_recent(10)
         if x["status"] == "running"
         and x["executor"] == defaults.XSOLO_SEAT), None))
    check("3a the single delivered task is running", t is not None)

    old_timeout = defaults.TASK_TIMEOUT_S
    try:
        defaults.TASK_TIMEOUT_S = -1        # every running task expires
        eng._gate_busy[defaults.XSOLO_SEAT] = 1
        eng._reap_overdue()
        row = eng.store.task(t["id"])
        check("3b gated task isn't reaped (still running)",
              row["status"] == "running")

        # timer resets when the gate clears: _gate_wait's timeout
        # path (0.3s) also goes through the finally-block touch.
        # The manual gate isn't cleared yet (with TASK_TIMEOUT_S=-1
        # the pump would instantly reap any ungated task)
        before = eng.store.task(t["id"])["updated_at"]
        time.sleep(1.2)          # timestamps are second-granular,
                                  # so widen the gap first
        eng._gate_wait("perm", "t", "b",
                       [{"action": "allow", "label": "A"}],
                       0.3, instance=defaults.XSOLO_SEAT)
        after = eng.store.task(t["id"])["updated_at"]
        check("3c gate clearing re-stamps (updated_at "
              "refreshes)", after > before)
        check("3d gate clearing pops its own entry (only the "
              "manual one is left)",
              eng._gate_busy.get(defaults.XSOLO_SEAT) == 1)

        eng._gate_busy.pop(defaults.XSOLO_SEAT, None)   # clear manual gate
        eng._reap_overdue()
        row = eng.store.task(t["id"])
        check("3e no gate restores the timeout law (expiry "
              "reaps it)", row["status"] == "failed")
    finally:
        defaults.TASK_TIMEOUT_S = old_timeout
    try:
        eng.stop()
    except Exception:
        pass

print()
if FAILS:
    print("P1FIX FAIL:", FAILS)
    sys.exit(1)
print("P1FIX PASS")
