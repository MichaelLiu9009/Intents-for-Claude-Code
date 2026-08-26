"""M15 guard: join-key and post-hoc detection surface
(docs/M15-JOINKEY.md) -- schema v7 migration / delivery stamping /
real execution duration / journal<->events dual-write / query by time
window / query by kind / task_window slice coordinates / boot-time
sampling of the local permission block.

Run: PYTHONIOENCODING=utf-8 python tests/test_m15.py
(⑨ "M15's zero-regression suite" is covered by the full test run, not
in this file.)
"""
import json
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
from commander.kernel.store import Store, SCHEMA_VERSION  # noqa: E402

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


def post(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def wait_for(fn, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


# ---- ① schema v7 migration: v6 DB self-heals; old rows' delivered/
#      host/boundary land as NULL
with tempfile.TemporaryDirectory() as tmp:
    dbp = Path(tmp) / "old.db"
    raw = sqlite3.connect(str(dbp))
    # Hand-forge a v6-shaped DB (only build the tables the migration
    # touches -- migration is executescript ALTER/CREATE, it doesn't
    # validate other tables; a real v6 DB runs the full DDL at boot.
    # From v10 on it also touches intent_steps / chain_spec_steps, so
    # add two empty-shell tables)
    raw.executescript("""
      CREATE TABLE intents(name TEXT PRIMARY KEY, title TEXT);
      CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT);
      CREATE TABLE intent_steps(intent TEXT, seq INTEGER, ref TEXT);
      CREATE TABLE chain_spec_steps(spec TEXT, seq INTEGER);
      CREATE TABLE bindings(slot INTEGER PRIMARY KEY,
                            intent TEXT NOT NULL, t TEXT);
      INSERT INTO intents(name,title) VALUES('老意图','旧行');
      INSERT INTO tasks(id,status) VALUES(1,'done');
      PRAGMA user_version=6;
    """)
    raw.close()
    st = Store(dbp)
    cols_t = {r[1] for r in st._db.execute("PRAGMA table_info(tasks)")}
    cols_i = {r[1] for r in st._db.execute("PRAGMA table_info(intents)")}
    ver = st._db.execute("PRAGMA user_version").fetchone()[0]
    old = st._db.execute(
        "SELECT delivered_at, host_session FROM tasks WHERE id=1"
    ).fetchone()
    check("1 v6→v7 migration: tasks gets delivered_at/host_session, "
          "intents gets boundary, events' three indexes complete, "
          "old rows land NULL",
          {"delivered_at", "host_session"} <= cols_t
          and "boundary" in cols_i and "proto" in cols_i
          and ver == SCHEMA_VERSION
          and old[0] is None and old[1] is None
          and {r[0] for r in st._db.execute(
              "SELECT name FROM sqlite_master WHERE type='index' "
              "AND name LIKE 'ix_events%'")}
          == {"ix_events_t", "ix_events_kind", "ix_events_task"})

    # ---- pure store surface: ⑤ by time window ⑥ by kind ⑦ coordinates
    #      (no engine needed)----------
    st.event_put("chain", "deliver", t="2026-08-12 10:00:00",
                 task_id=7, intent="报时", issuer="user", session="s1")
    st.event_put("perm", "prompt", t="2026-08-12 10:00:30",
                 task_id=7, session="s1")
    st.event_put("perm", "deny", t="2026-08-12 10:00:31",
                 task_id=7, session="s1")
    st.event_put("perm", "allow", t="2026-08-12 10:00:40",
                 task_id=7, session="s1")
    st.event_put("chain", "claim", t="2026-08-12 10:01:00",
                 task_id=7, session="s1")
    st.event_put("chain", "deliver", t="2026-08-12 11:00:00",
                 task_id=8, session="s1")     # a row outside the window (11 o'clock)

    win = st.events_between("2026-08-12 10:00:00", "2026-08-12 10:01:00")
    check("5 query by time window: closed interval includes both "
          "ends (10:00:00 and 10:01:00 both in), 11 o'clock "
          "outside the window is not",
          [e["name"] for e in win]
          == ["deliver", "prompt", "deny", "allow", "claim"])

    only_perm = st.events_between("2026-08-12 00:00:00",
                                  "2026-08-12 23:59:59", kinds=["perm"])
    both = st.events_between("2026-08-12 00:00:00", "2026-08-12 23:59:59",
                             kinds=["perm", "chain"])
    ghost = st.events_between("2026-08-12 00:00:00", "2026-08-12 23:59:59",
                              kinds=["不存在的类"])
    check("6 query by kind: kinds single/multi select each as "
          "expected; unknown kind returns empty set, no error; "
          "**occurrence order preserved** (deny before allow -- "
          "the §7 chain signal)",
          [e["name"] for e in only_perm] == ["prompt", "deny", "allow"]
          and len(both) == 6 and ghost == []
          and st.event_kinds()[0]["kind"] in ("chain", "perm"))

    st.close()

# ---- engine real run: ② delivery stamping ③ real duration ④
#      dual-write ⑧ boot-time sampling -------------------------------
with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", scenario="随口一问",
                     steps="Get-Date 报给用户", fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    # Pre-forge the instance home's settings.local.json (the object
    # the engine samples at boot; provision doesn't overwrite it --
    # that's exactly the premise for §4's detection half)
    home = ws_root / "instances" / "sidecar"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.local.json").write_text(json.dumps({
        "permissions": {"allow": ["Read(//d/x/**)", "Bash(git status)"],
                        "deny": ["Read(//d/secret/**)"]},
        "otherKey": True}), encoding="utf-8")

    eng = Engine(ws_root, http_port=9850, ws_port=9851, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ⑧ boot-time sampling: perm/local-accretion lands in events
    #    (dual-written via journal sink)
    acc = wait_for(lambda: eng.store.events_between(
        "2000-01-01 00:00:00", "2999-01-01 00:00:00",
        kinds=["perm"], names=["local-accretion"]))
    f8 = json.loads((acc or [{}])[0].get("fields") or "{}")
    check("8 boot-time sampling: local permissions block lands in "
          "the row (present + counts + rule text; otherKey not "
          "mixed in)",
          acc is not None and len(acc) == 1 and f8.get("present") is True
          and f8.get("counts") == {"allow": 2, "deny": 1}
          and "//d/secret/**" in (f8.get("rules") or "")
          and "otherKey" not in (f8.get("rules") or ""))

    # First feed one hook (with session_id) -- the engine should
    # learn host_session from this
    post(9850, "/api/hook", {"hook_event_name": "Stop",
                             "session_id": "sess-abc-123"})
    time.sleep(0.3)

    # Trigger intent -> deliver -> stamp
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9851", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    # §2m v9: plain intents rerouted to headless, the subject of the
    # transcript join law is the PTY seat -- use validate's sim task
    # (sidecar seat) as the sample
    c.send(json.dumps({"type": "validate", "name": "报时"}))
    ring = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(20)
         if t.get("spec") == "validate" and t["status"] == "running"),
        None))
    check("2 delivery stamping: delivered_at lands immediately, "
          "host_session = sessionId learned from the hook",
          ring is not None and ring.get("delivered_at") is not None
          and ring.get("host_session") == "sess-abc-123")

    # Reconcile -> ③ real duration (>0, and <= the full span of
    # created->now)
    time.sleep(1.1)          # give execution duration a visibly discernible thickness
    post(9850, "/api/mcp", {"verb": "task_done", "task": ring["id"],
                            "outcome": "ok", "summary": "报完了"})
    rec = wait_for(lambda: eng.store.record_for(ring["id"]))
    dur = (rec or {}).get("duration_s")
    check("3 real execution duration: duration_s turns live from "
          "a dead field (>0, excludes queueing ⇒ no more than "
          "the full created→final span)",
          rec is not None and dur is not None and 0 < dur < 60)

    # ⑦ task_window: full coordinates for slicing the transcript, UTC
    #    conversion present
    w = eng.store.task_window(ring["id"])
    check("7 task_window: host_session + local window + UTC "
          "window (transcript timestamp is UTC ISO -- the "
          "timezone gate lives in the store, consumers don't "
          "guess)",
          w is not None and w["host_session"] == "sess-abc-123"
          and w["t0"] == ring["delivered_at"] and not w["queued"]
          and w["t0_utc"].endswith(".000Z") and "T" in w["t0_utc"]
          and w["t1_utc"].endswith(".999Z")
          and w["duration_s"] == dur and w["intent"] == "报时")

    # ④ dual-write: journal jsonl row count == events row count
    #    (counted from the same instant since boot)
    time.sleep(0.5)
    jpath = eng.journal.dir / defaults.JOURNAL_NAME
    jrows = [json.loads(x) for x in
             jpath.read_text(encoding="utf-8").splitlines() if x.strip()]
    erows = eng.store.events_between("2000-01-01 00:00:00",
                                     "2999-01-01 00:00:00", limit=100000)
    sess = {e["session"] for e in erows}
    tid_j = [r for r in jrows
             if r.get("kind") == "chain" and r.get("name") == "deliver"]
    tid_e = [e for e in erows
             if e["kind"] == "chain" and e["name"] == "deliver"]
    check("4 dual-write: journal row count == events row count, "
          "same instant same name; task promotes to task_id "
          "column; session = records dir name",
          len(jrows) == len(erows) and len(jrows) > 5
          and sess == {eng.journal.session}
          and tid_j and tid_e
          and tid_e[0]["task_id"] == tid_j[0]["task"] == ring["id"]
          and tid_e[0]["t"] == tid_j[0]["t"])

    # Wrap up: the stop frame goes through run()'s own teardown
    # (journal.close lives there -- skip it and, on Windows, deleting
    # the temp dir collides with an events.jsonl still open)
    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    check("9 clean wrap-up (journal closes, temp dir can be "
          "deleted)", not th.is_alive())
    c.close()

print()
if FAILS:
    print(f"-- {len(FAILS)} check(s) failed:")
    for f in FAILS:
        print("   " + f)
    sys.exit(1)
print("M15 all green (⑨ full regression suite runs separately "
      "via tests/*.py)")
