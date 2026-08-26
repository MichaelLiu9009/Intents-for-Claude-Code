"""Store guard -- check labels cite INTENT_SPEC v4b clauses.

Run: PYTHONIOENCODING=utf-8 python tests/test_store.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commander.kernel.store import Store          # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    st = Store(root / "state.db")

    # ---- §1 intent body: text + status + chain ----------------------
    st.intent_create(
        "open_sheets", title="开谱铺屏", scenario="练琴:琴谱目录×满屏铺主屏",
        steps="按几何铺前5页", fires=1,
        step_refs=["shot@1"], tools=[("open_pages", "1")])
    it = st.intent("open_sheets")
    check("1 §1 one row is the whole body: text/status/rev/chain/"
          "tools all present",
          it["status"] == "draft" and it["rev"] == 1
          and it["step_refs"] == ["shot@1"]
          and it["tools"] == [("open_pages", "1")])

    # ---- §3 revision: update row rev++ (task revision carrier) ------
    rev = st.intent_revise("open_sheets", steps="改为按显示器几何铺",
                           step_refs=["shot@1", "geometry@1"])
    check("2 §3 sim fails -> update the row, rev++",
          rev == 2 and st.intent("open_sheets")["step_refs"]
          == ["shot@1", "geometry@1"])

    # ---- §1 dimension-reduction list: provision/decommission/
    #      migrate/quota/unused ---------------------------------------
    st.intent_revise("open_sheets", status="provisioned")
    st.intent_create("rec_audio", title="点名录音", fires=0,
                     step_refs=["record@1"])
    st.intent_revise("rec_audio", status="provisioned")
    check("3 §1 provision = UPDATE; dual quota = COUNT",
          st.count("sidecar") == 2)
    st.migrate_owner(["rec_audio"], "practice")
    check("4 §1 mode relocation = one transaction UPDATE owner",
          st.count("sidecar") == 1 and st.count("practice") == 1)
    check("5 §1 \"never-used intent\" = LEFT JOIN records",
          set(st.never_used()) == {"open_sheets", "rec_audio"})
    check("6 §1 tool impact surface = intent_steps.ref reverse "
          "lookup",
          st.tool_impact("shot@1") == ["open_sheets"])

    # ---- §1 caveats accessors retired 2026-08-25 (table = fossil,
    #      no writer/reader; lessons flow back via sidecar revision) --
    check("7 §1 caveats table is a fossil: no accessor on Store",
          not hasattr(st, "caveat_add") and not hasattr(st, "caveats"))

    # ---- §6 v5 declaration surface: a spec must exist before a
    #      chain; load the task-chain query surface -------------------
    st.spec_put("intent-creation", head="sidecar", priority=0,
                consequence="创建一条新 intent,经两道人闸与 sim 验收",
                steps=[
                    {"assignee": "user", "kind": "gate",
                     "gate": "批 task request"},
                    {"assignee": "sidecar", "kind": "procedure",
                     "ref": "construct"},
                    {"assignee": "user", "kind": "gate",
                     "gate": "批 template"},
                ])
    st.spec_put("maintenance", head="engine", priority=1,
                consequence="引擎维护,加塞档",
                steps=[{"assignee": "engine", "kind": "procedure",
                        "ref": "prune@1"}])
    mine = st.startable("sidecar")
    check("8 §6 access query face: what can I initiate + "
          "consequence",
          [s["name"] for s in mine] == ["intent-creation"]
          and "人闸" in mine[0]["consequence"])

    # ---- §6 access rule: only head can initiate ----------------------
    try:
        st.chain_start("intent-creation", issuer="practice")
        breached = True
    except PermissionError:
        breached = False
    check("9 §6 non-head initiation is refused (access law)",
          not breached)

    t1 = st.chain_start("intent-creation", issuer="sidecar",
                        intent="open_sheets")
    check("10 §6 first ring forges per spec step 0: a gate ring "
          "lands gated directly, with a semantic name",
          t1["status"] == "gated" and t1["gate"] == "批 task request"
          and t1["executor"] == "user" and t1["priority"] == 0)

    # ---- §6 routing: after submission the engine delivers straight
    #      to the next assignee ---------------------------------------
    try:
        st.advance(t1["id"])
        premature = True
    except ValueError:
        premature = False
    check("11 §6 previous ring not done, advance is refused",
          not premature)
    st.task_update(t1["id"], status="done")
    t2 = st.advance(t1["id"])
    check("12 §6 engine delivers per spec to the next assignee, "
          "priority inherited",
          t2["executor"] == "sidecar" and t2["status"] == "queued"
          and t2["seq"] == 1 and t2["chain_id"] == t1["chain_id"]
          and t2["priority"] == 0)
    st.task_update(t2["id"], status="done")
    t3 = st.advance(t2["id"])
    st.task_update(t3["id"], status="done")
    check("13 §6 spec runs out, chain ends (advance returns "
          "None)",
          st.advance(t3["id"]) is None)
    p1 = st.chain_start("maintenance", issuer="engine",
                        payload="参数随行")
    check("13b §6 payload rides the whole chain (IME v2: user "
          "input is the chain's baggage)",
          p1["payload"] == "参数随行")
    st.task_update(p1["id"], status="done")  # leaves the queue, so it
                                              # doesn't disturb the
                                              # later queue-order check

    # ---- §6 breakpoint/retry: same ring rev++, doesn't start a new
    #      chain ------------------------------------------------------
    st.task_update(t2["id"], status="failed")
    st.task_update(t2["id"], status="queued", bump_rev=True)
    check("14 §6 retry = same ring rev++, track record stays "
          "continuous",
          st.task(t2["id"])["rev"] == 2
          and len(st.chain(t1["chain_id"])) == 3)

    # ---- §6 priority rule: higher tier cuts in, same tier is FIFO ---
    m1 = st.chain_start("maintenance", issuer="engine")
    q = st.queue_for("sidecar")     # t2 reopened (p0) is in the queue
    qe = st.queue_for("engine")
    check("15 §6 priority: a cut-in tier goes first, same tier "
          "is FIFO",
          m1["priority"] == 1 and [x["id"] for x in q] == [t2["id"]]
          and qe and qe[0]["id"] == m1["id"])

    # ---- §6 issuer ledger ---------------------------------------------
    check("16 §6 an instance can see the tasks it issued and "
          "their status",
          {x["id"] for x in st.issued_by("sidecar")}
          == {t1["id"], t2["id"], t3["id"]}
          and len(st.issued_by("engine")) == 2)   # m1 + 13b's p1

    # ---- §6 intent delivery = chain-spec specialization -------------
    spec_name = st.compile_delivery("open_sheets")
    dsp = st.spec(spec_name)
    check("17 §6 v7 delivery-chain compiles = a single deliver "
          "node (v16 physical layer: the procedure ring retires "
          "-- chain hangs on the keyed seat, doesn't enter the "
          "delivery chain; suspend/reroute-to-furnace lose their "
          "hook along with it)",
          dsp["head"] == "sidecar"
          and [s["kind"] for s in dsp["steps"]] == ["deliver"]
          and dsp["steps"][-1]["assignee"] == "x·solo"
          and dsp["steps"][-1]["template"] == "xsolo"
          and dsp["steps"][-1]["ref"] == "open_sheets"
          and not dsp["steps"][-1]["effect"])

    # ---- §3 [TEST] separate accounting ---------------------------------
    st.record(t1["id"], "open_sheets", is_test=True, outcome="pass")
    st.record(t1["id"], "open_sheets", is_test=False, outcome="ok",
              duration_s=3.2)
    check("18 §3 test track record and real track record settle "
          "separately, success rate isn't watered down",
          len(st.track("open_sheets")) == 1
          and len(st.track("open_sheets", include_test=True)) == 2)

    # ---- §2 boot-time reconciliation (M20 §2c): row<->disk
    #      cross-verified by hash ---------------------------------------
    util = root / "utility"
    st.proc_seed({"ime": "IME 唤起", "截屏": "鼠标位置截图"})
    seeded = {p_["name"]: p_ for p_ in st.procs(status="provisioned")}
    check("19 v16 proc_seed: the built-in wordlist upserts as "
          "provisioned (physical layer = engine built-in, no "
          "agent submission port)",
          set(seeded) == {"ime", "截屏"}
          and not hasattr(st, "proc_stage")
          and not hasattr(st, "proc_approve"))
    st.proc_seed({"ime": "IME 唤起(改口)"})
    check("19b v16 reseed: in-table entries update, off-table "
          "entries retire (截屏 -> retired)",
          (st.proc_get("ime") or {}).get("desc") == "IME 唤起(改口)"
          and (st.proc_get("截屏") or {}).get("status") == "retired")
    check("19c bind face has been dismantled (user ruling "
          "2026-08-23): store has no bind/bindings verb",
          not hasattr(st, "bind") and not hasattr(st, "bindings"))
    st.proc_seed({"ime": "IME 唤起", "截屏": "鼠标位置截图"})
    check("20 reconcile settles clean (the bindings.chain check "
          "retired along with its module)",
          st.reconcile(util) == [])

    # ---- §6 cancel rule (v2): chain is the unit, once accepted it
    #      doesn't roll back --------------------------------------------
    cu = st.chain_start("intent-creation", issuer="user")
    check("20b §6 issuer law: user bypasses the head check (the "
          "human owns the surface)",
          cu["issuer"] == "user" and cu["status"] == "gated")
    n = st.chain_cancel(cu["chain_id"], actor="user")
    check("20c §6 cancel: an unaccepted ring (gated) voids, "
          "flag drops",
          n == 1 and st.task(cu["id"])["status"] == "cancelled"
          and st.chain_cancelled(cu["chain_id"]))
    cr = st.chain_start("intent-creation", issuer="user")
    st.task_update(cr["id"], status="done")
    r2 = st.advance(cr["id"])
    st.task_update(r2["id"], status="running")
    st.chain_cancel(cr["chain_id"], actor="user")
    check("20d §6 an accepted ring doesn't roll back (stays "
          "running)",
          st.task(r2["id"])["status"] == "running")
    st.task_update(r2["id"], status="done")
    check("20e §6 after settlement, advance checks the flag and "
          "halts the chain (no next ring forged)",
          st.advance(r2["id"]) is None)
    led = {c["chain"]: c["status"] for c in st.chains_recent(50)}
    check("20f §6 chain ledger: cancelled flag overrides the "
          "last ring's status",
          led[cu["chain_id"]] == "cancelled"
          and led[cr["chain_id"]] == "cancelled")
    qv = st.queue_view()
    check("20g §6 queue_view: sorted by priority law (the "
          "subtask queue's face)",
          qv == sorted(qv, key=lambda x: (-x["priority"], x["id"])))

    # ---- §7 guardrail 3: an old engine refuses to write a downgrade
    #      into a newer DB ------------------------------------------
    st.close()
    import sqlite3
    db = sqlite3.connect(str(root / "state.db"))
    db.execute("PRAGMA user_version=99")
    db.commit(); db.close()
    try:
        Store(root / "state.db")
        refused = False
    except RuntimeError:
        refused = True
    check("21 §7 user_version ahead -> refuses to open, no "
          "silent downgrade", refused)

    # ---- §7 migration durability (audit 2026-08-25) -----------------
    # executescript auto-commits each step, so the version must be
    # stamped step by step: an interrupt mid-chain has to leave the
    # db at the last step that actually landed, or the next boot
    # replays applied ALTERs into "duplicate column name" and the
    # workspace is unopenable for good. A fresh db walks the whole
    # V2..V19 chain on its first boot, so this is the common path.
    from commander.kernel import store as _store
    real = _store._MIGRATIONS
    broken = dict(real)
    broken[5] = "THIS IS NOT SQL;"
    brk = root / "broken.db"
    _store._MIGRATIONS = broken
    try:
        Store(brk)
    except Exception:
        pass
    finally:
        _store._MIGRATIONS = real
    db = sqlite3.connect(str(brk))
    stalled = db.execute("PRAGMA user_version").fetchone()[0]
    db.close()
    check("22 §7 a failed migration step leaves user_version at the "
          "last step that landed (4, not 1) — every applied step is "
          "self-describing", stalled == 4)
    st2 = Store(brk)                     # the repair path: resume from 5
    db = sqlite3.connect(str(brk))
    healed = db.execute("PRAGMA user_version").fetchone()[0]
    db.close()
    st2.close()
    check("22b §7 reopening resumes the chain from where it stalled "
          "and reaches the current schema (no duplicate-column "
          "brick)", healed == _store.SCHEMA_VERSION)

print()
print("STORE PASS" if not FAILS else f"STORE FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
