"""seed template guard (user ruling 2026-08-24): the system ships
two built-ins -- timecheck (a standalone intent) + translator (an
interactive-bracket protocol whose member translate carries a
screenshot prelude). Guards four things: (1) the template passes its
own registration gate (schema + E grammar -- format is the product);
(2) seed lands in the DB + the workspace source-of-truth is complete
with zero local paths; (3) the engine boots to a clean reconciliation
with the protocol spec/skill rendered and in place; (4) member-step
prelude: pressing a member key runs the prelude first, the step
envelope's tail carries the materials pointer; a prelude blowup means
no step is delivered and it's reported to a human.

Run: PYTHONIOENCODING=utf-8 python tests/test_seed.py
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import cli, defaults                     # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel import wspace                     # noqa: E402
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


class FakeProtoHost:
    """Protocol-seat stand-in (kind=pty: _deliver takes the
    pointer-envelope path)."""
    kind = "pty"

    def __init__(self):
        self.delivered = []
        self.steps = []

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return True

    def deliver(self, tid, line):
        self.delivered.append((tid, line))
        return True

    def enqueue_step(self, line, member=None):
        self.steps.append((member, line))

    def flush(self):
        pass

    def reap(self, tid):
        pass

    def stop(self):
        pass


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)

    # ---- (1) seed: template self-check + lands in the DB --------------
    rc = cli.main(["seed", "--workspace", str(ws_root)])
    check("1 seed passes its own gate (the template is the "
          "textbook, sick means refuse to seed)", rc == 0)
    rc2 = cli.main(["seed", "--workspace", str(ws_root)])
    check("1b seed is idempotent (rerun doesn't blow up or "
          "duplicate)", rc2 == 0)

    st = Store(ws_root / "state.db")
    tc = st.intent("timecheck") or {}
    pr = st.proto_get("translator") or {}
    tr = st.intent("translate") or {}
    lg = st.intent("language") or {}
    check("2 DB side: timecheck provisioned; translator "
          "provisioned, members = language/translate, members "
          "stamped with the proto seal",
          tc.get("status") == "provisioned"
          and pr.get("status") == "provisioned"
          and pr.get("members") == ["language", "translate"]
          and lg.get("proto") == "translator"
          and tr.get("proto") == "translator")
    check("2b translate member carries a screenshot prelude "
          "(procedures column lands in the DB)",
          json.loads(tr.get("procedures") or "[]") == ["screenshot"])
    home = ws_root / "instances" / "sidecar"
    pd = home / "translator"
    blob = "".join(p.read_text(encoding="utf-8")
                   for p in [home / "timecheck" / "intent.json",
                             pd / "protocol.json", pd / "skill.md",
                             pd / "members" / "language" / "intent.json",
                             pd / "members" / "translate" / "intent.json"])
    check("2c workspace source of truth complete (intent.json/"
          "protocol.json/skill.md/members/*/, flat layout) + "
          "schema.md textbook present",
          (pd / "schema.md").is_file()
          and (home / "timecheck" / "schema.md").is_file())
    check("2d template has zero local paths (environment agnostic: "
          "no drive letters/usernames)",
          ":\\" not in blob and ":/" not in blob
          and "Users" not in blob)
    # registration-gate re-check: the workspace source of truth is
    # re-run through resolve_members (whole-protocol validation after
    # member preludes are unlocked -- what's on disk is what can
    # actually register)
    decl, err = wspace.read_decl(pd)
    mdecls, _, mprobs = wspace.resolve_members(pd, decl or {})
    check("2e on-disk source of truth re-registers cleanly "
          "(resolve_members zero issues, procedures flow with "
          "member declarations)",
          not err and mprobs == [] and len(mdecls) == 2
          and mdecls[1].get("procedures") == ["screenshot"])
    st.close()

    # ---- (3) engine boot: reconciliation + rendering -------------------
    eng = Engine(ws_root, http_port=9891, ws_port=9892, spawn_host=False)
    eng.host = FakeHost()
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)
    check("3 boot: protocol spec in place, skill render artifact "
          "present, reconciliation clean",
          eng.store.spec("protocol:translator") is not None
          and wspace.utility_skill_path(ws_root, "translator").is_file()
          and eng.store.reconcile(eng.utility) == [])

    # ---- (4) member-step prelude (procrun stand-in: no real
    # screenshot taken) --------------------------------------------------
    from commander import engine as eng_mod
    fx = FakeProtoHost()
    eng._xhosts["translator"] = fx
    r = eng._proto_start("translator")
    check("4 protocol opened (Start)", r.get("ok") is True)
    wait_for(lambda: fx.delivered)   # bracket package delivered = running

    real_run = eng_mod.procrun.run_step
    calls = []

    def fake_run(entry, td, **kw):
        calls.append(entry.name)
        (td / "materials").mkdir(parents=True, exist_ok=True)
        shot = td / "materials" / "shot.png"
        shot.write_bytes(b"\x89PNG fake")
        return True, "", [{"kind": "file", "label": "screenshot",
                           "path": str(shot)}]

    try:
        eng_mod.procrun.run_step = fake_run
        r2 = eng._proto_member("translator", "translate", "")
        check("5 member key triggers a with-prelude receipt",
              r2.get("prelude") == ["screenshot"])
        got = wait_for(lambda: fx.steps)
        check("5b prelude runs first, step envelope tail carries "
              "the materials pointer",
              got and got[0][0] == "translate"
              and "| materials: " in got[0][1]
              and "shot.png" in got[0][1]
              and calls == ["shot.py"])
        # member without a prelude: delivered as-is directly (doesn't
        # take the threaded path)
        r3 = eng._proto_member("translator", "language", "")
        check("5c prelude-less member delivered directly (language "
              "has no procedures)",
              r3.get("ok") is True and "prelude" not in r3
              and wait_for(lambda: len(fx.steps) >= 2)
              and "materials" not in fx.steps[1][1])

        # prelude blows up: reported to a human, no step delivered,
        # bracket stays open
        def boom_run(entry, td, **kw):
            return False, "boom (test)", []
        eng_mod.procrun.run_step = boom_run
        n0 = len(fx.steps)
        eng._proto_member("translator", "translate", "")
        time.sleep(1.0)
        fails = eng.store.events_between(
            "2000-01-01 00:00:00", "2999-01-01 00:00:00",
            kinds=["procedure"], names=["step-prelude-failed"])
        br = eng._bracket_of("translator")
        check("6 prelude blows up = no step delivered + logged + "
              "bracket still open",
              len(fx.steps) == n0 and fails and br is not None)
    finally:
        eng_mod.procrun.run_step = real_run

    # ---- (8) protocol-close consolidate offer (reshaped 2026-08-25:
    # approve suspends the booklet and opens a consolidate order on
    # sidecar; revival passes the registration gate) -------
    r4 = eng._proto_close("translator")
    check("8 protocol close settles the books", r4.get("ok") is True)
    with eng._card_lock:
        ccard = next((cd for cd in eng._cards.values()
                      if cd["kind"] == "offer"
                      and "Consolidate" in str(cd.get("title"))), None)
    check("8b consolidate offer pops after protocol close (only "
          "raised when there are member steps; kind=offer — not "
          "swept by terminal engagement)",
          ccard is not None
          and ccard["options"][0]["action"] == "consolidate")
    eng._on_card_answer(ccard["id"], "consolidate",
                        ccard["options"][0]["data"])
    ct8 = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("spec") == "consolidate"), None))
    check("8c consolidate approve: booklet suspended (draft — Start "
          "refused by the provisioned-only guard) + consolidate "
          "order opened on sidecar with the bracket as origin",
          ct8 is not None and ct8.get("intent") == "translator"
          and (eng.store.proto_get("translator") or {})
          .get("status") == "draft")

    eng._engine_shutdown()
    th.join(timeout=10)
    check("9 clean shutdown", not th.is_alive())

print()
if FAILS:
    print("SEED FAIL:", FAILS)
    sys.exit(1)
print("SEED PASS")
