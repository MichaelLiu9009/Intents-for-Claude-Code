"""M16 guard (permission-surface consolidation, 2026-08-24 revision):
boundary declaration surface retired (schema/submit/get all clean) /
format checker and union render (boundary.py is a pure-function cold
standby, tested off cold spare parts) / PERM_ALLOW ledger render
(config.json -> sidecar/solo both get allow).
(bcompile persistence and promotion-scheme render retired along with
the pruner seat -- the store access surface is removed; the
boundary_compiled table stays as a fossil under the additive law.)

Run: PYTHONIOENCODING=utf-8 python tests/test_m16.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import _ws  # noqa: E402
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel import boundary                   # noqa: E402
from commander.kernel.store import Store, SCHEMA_VERSION  # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return True

    def inject_chat(self, text):
        pass

    def write_raw(self, data):
        pass

    def replay(self):
        return ""

    def stop(self):
        pass


def post9860(payload):
    return post(9860, payload)


def post(port, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---- ③ checker (pure function, no DB needed)------------------------------------
POL = {"security": {"never_allow": ["state.db", "/.claude/"]},
       "generalization": {"level": "conservative"}}

good = {"allow": ["Read(//d/x/**)", "Bash(git status)", "WebSearch"]}
check("3a checker: 好单 passes the gate (POSIX-form path / "
      "command / bare tool)",
      boundary.check_rules(good, POL) == [])
check("3b shape: non-dict / unknown key / non-str list all "
      "rejected",
      boundary.check_rules(["Read"], POL) != []
      and boundary.check_rules({"allw": []}, POL) != []
      and boundary.check_rules({"allow": [1]}, POL) != [])
check("3c syntax: prose-form dimension (win32:) / unknown tool "
      "(Write probe five) / contains .. / path not POSIX-ized, "
      "each named individually",
      boundary.check_rules({"allow": ["win32: EnumWindows"]}, POL) != []
      and boundary.check_rules({"allow": ["Write(//d/x/a)"]}, POL) != []
      and boundary.check_rules({"allow": ["Read(//d/../y)"]}, POL) != []
      and boundary.check_rules({"allow": ["Read(d:/x/a)"]}, POL) != [])
check("3c2 per-family cut (first live-fire whole-batch-reject "
      "precedent 2026-08-13): command rules containing / aren't "
      "paths, no POSIX check; network rules only accept domain: "
      "form, bare URL rejected and the reason quotes the "
      "offending rule verbatim",
      boundary.check_rules(
          {"allow": ["PowerShell(Get-ChildItem //c/Users/x/琴谱)"]},
          POL) == []
      and boundary.check_rules(
          {"allow": ["Bash(cat a/b.txt)"]}, POL) == []
      and boundary.check_rules(
          {"allow": ["WebFetch(domain:example.com)"]}, POL) == []
      and any("domain" in p for p in boundary.check_rules(
          {"always": ["WebFetch(https://x.com/news)"]}, POL)))
check("3d ceiling: never_allow substring hit on allow/ask "
      "rejects immediately; deny is unrestricted (deny "
      "tightens, it doesn't grant)",
      boundary.check_rules({"allow": ["Read(//d/ws/state.db)"]}, POL) != []
      and boundary.check_rules({"ask": ["Edit(//d/h/.claude/x)"]}, POL) != []
      and boundary.check_rules({"deny": ["Read(//d/ws/state.db*)"]},
                               POL) == [])

rows = [
    {"intent": "好单", "rules": json.dumps(
        {"always": ["Read(//d/x/**)", "Bash(git status)"],
         "allow": ["Read(//d/ledger/**)"]})},      # allow = ledger, not rendered
    {"intent": "好单2", "rules": json.dumps(
        {"always": ["Bash(git status)", "WebSearch"],
         "deny": ["Edit(//d/x/keep.md)"]})},
    {"intent": "病单", "rules": json.dumps(
        {"always": ["Read(//d/ws/state.db)"]})},
    {"intent": "坏JSON", "rules": "{oops"},
]
al, dn, probs = boundary.union_render(rows, POL)
check("3e union (§5f promotion scheme): always dedup-merged, "
      "allow ledger not rendered, deny rendered, 病单 dropped "
      "whole and named",
      al == ["Bash(git status)", "Read(//d/x/**)", "WebSearch"]
      and dn == ["Edit(//d/x/keep.md)"]
      and len(probs) == 2
      and any("病单" in p for p in probs)
      and any("坏JSON" in p for p in probs))

f_, c_, d_ = boundary.vet_rules(
    {"always": ["Bash(git status)", "WebFetch(https://x.com/a)"]}, POL)
check("3f two-tier chart (user ruling 2026-08-13): syntax-sick "
      "rules culled per-entry (a dead rule is harmless, valid "
      "entries aren't dragged down), culled entries named",
      f_ == [] and c_["always"] == ["Bash(git status)"]
      and len(d_) == 1 and "domain" in d_[0])
al2, dn2, probs2 = boundary.union_render(
    [{"intent": "混单", "rules": json.dumps(
        {"always": ["Bash(git log)", "WebFetch(https://x.com/a)"]})}], POL)
check("3f2 union side, same law: 混单 culls sick entries and "
      "keeps good ones, sick-list names them 「剔」",
      al2 == ["Bash(git log)"] and any("dropped" in p for p in probs2))

# ---- ①②④⑤⑥ engine real run ------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    check("1 v8+: fresh db has user_version=SCHEMA_VERSION, "
          "boundary_compiled table present (version not pinned "
          "to a literal number -- lesson from stepping in the "
          "same hole three times)",
          st._db.execute("PRAGMA user_version").fetchone()[0]
          == SCHEMA_VERSION
          and st._db.execute("SELECT name FROM sqlite_master WHERE "
                             "type='table' AND name='boundary_compiled'")
          .fetchone() is not None)
    st.intent_create("报时", title="报时", scenario="s", steps="做",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()
    # Permission-surface consolidation (2026-08-24): the allow ledger
    # = config.json's PERM_ALLOW (settles from human-approved Always /
    # user hand-written) -- here we hand-write two entries to
    # simulate the ledger; at boot the engine should render it into
    # both sidecar's and the executor seat's allow.
    (ws_root / "config.json").write_text(json.dumps(
        {"PERM_ALLOW": ["Bash(git status)", "Read(//d/notes/**)"]},
        ensure_ascii=False), encoding="utf-8")

    eng = Engine(ws_root, http_port=9860, ws_port=9861, spawn_host=False)
    eng.host = FakeHost()
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ④ permission-surface consolidation render: the PERM_ALLOW
    # ledger goes into **both** seats' allow (global effect); the
    # deny floor caps both seats; the executor seat no longer pins
    # defaultMode (mode goes through the spawn flag
    # --permission-mode; pinning project-scope auto on the settings
    # surface would be ignored)
    stj = json.loads((ws_root / "instances" / "sidecar" / ".claude"
                      / "settings.json").read_text(encoding="utf-8"))
    perms = stj["permissions"]
    eng._xhost("solo")          # lazily forge the executor-seat home
    xperms = json.loads(
        (ws_root / "instances" / "x·solo" / ".claude" / "settings.json")
        .read_text(encoding="utf-8"))["permissions"]
    check("4 ledger render: PERM_ALLOW goes into both sidecar's "
          "and the executor seat's allow (engine front door / "
          "pipe floor unchanged), deny caps both seats, executor "
          "seat has no defaultMode pin (mode belongs to the "
          "spawn flag)",
          perms.get("allow") == ["mcp__intentOS", "Bash(git status)",
                                 "Read(//d/notes/**)"]
          and any("state.db" in d for d in perms["deny"])
          and any(".claude/**" in d for d in perms["deny"])
          and "Bash(git status)" in xperms.get("allow", [])
          and "Read(//d/notes/**)" in xperms.get("allow", [])
          and "mcp__intentOS" in xperms.get("allow", [])
          and any("state.db" in d for d in xperms["deny"])
          and "defaultMode" not in xperms)

    # ② boundary surface retired (user ruling 2026-08-24): the allow
    # side belongs to the harness (--permission-mode) + the PERM_ALLOW
    # ledger, so a prose declaration has no consumer. A submitted
    # boundary field is dropped, the provisioned intent.json carries
    # no boundary key, and hand-writing one back is rejected by the
    # schema gate (unknown field) at registration.
    r = post(9860, {"verb": "intent_submit", "name": "带边界",
                    "steps": "1. report 做点事", "scenario": "记点东西",
                    "boundary": "只读 d:/notes 下的 md;跑 git status"})
    row = eng.store.intent("带边界")
    d0 = _ws.decl(eng, "带边界")
    _ws.edit(eng, "带边界", boundary="想复活声明列")
    r2 = _ws.register(post9860, "带边界")
    rold = post(9860, {"verb": "intent_update", "name": "带边界",
                       "boundary": "老通道"})
    check("2 boundary surface retired: submit doesn't persist "
          "it, intent.json doesn't carry it, hand-writing it "
          "back at registration is rejected as unknown field, "
          "intent_update channel stays retired",
          "error" not in r and not (row.get("boundary") or "")
          and "boundary" not in d0
          and "unknown fields" in json.dumps(r2, ensure_ascii=False)
          and "boundary" in json.dumps(r2, ensure_ascii=False)
          and "removed" in rold.get("error", ""))

    # ⑤ dual-column isolation: compiled has no MCP surface at all --
    # intent_get carries no compiled data, and the verb-table lookup
    # has no bcompile-class verbs
    g = post(9860, {"verb": "intent_get", "name": "报时"})
    blob = json.dumps(g, ensure_ascii=False)
    verbs_probe = post(9860, {"verb": "bcompile_get", "intent": "报时"})
    check("5 isolation: intent_get response has no boundary key "
          "and no compiled trace; bcompile has no MCP verb "
          "(unknown verb rejected)",
          "boundary" not in blob
          and "git status" not in blob        # compiled rules never leak
          and "error" in verbs_probe)

    # ⑥ empty ledger = only the engine floor is left (PERM_ALLOW is
    #    the sole allow-increment source)
    ws2 = ws_root / "second"
    st2 = Store(ws2 / "state.db")
    st2.close()
    from commander.kernel.provision import provision_home
    saved_ledger = defaults.PERM_ALLOW
    defaults.PERM_ALLOW = []            # empty-ledger case (process-level knob, restored after)
    try:
        provision_home(ws2, token="t")
        stj2 = json.loads((ws2 / "instances" / "sidecar" / ".claude"
                           / "settings.json").read_text(encoding="utf-8"))
    finally:
        defaults.PERM_ALLOW = saved_ledger
    check("6 empty ledger = only the engine floor remains (mcp "
          "front door), the rest still goes through human "
          "approval",
          stj2["permissions"].get("allow") == ["mcp__intentOS"])
    # audit 2026-08-25: config.json became the always-allow ledger in
    # the 2026-08-24 consolidation and was never added to the deny
    # floor — a seat that could edit it could grant itself anything,
    # on every seat, which is exactly what the floor's own docstring
    # says can never happen
    deny6 = stj2["permissions"].get("deny") or []
    check("6b config.json (the PERM_ALLOW ledger) is on the engine "
          "deny floor — a seat cannot write its own allow list",
          any("config.json" in d and d.startswith("Edit(")
              for d in deny6))

    # ---- never_allow ceiling, enforced (audit + user ruling
    # 2026-08-25): advertised in README/CONFIG since M16, wired
    # nowhere until now. _grant_rules is the single choke point every
    # Always click passes through, so the ceiling belongs there —
    # above the human, because perm_gate sits on the executor's own
    # tool face and the card asking for the grant can be agent-
    # authored.
    eng._perm_grants = set()
    eng._grant_rules(["Edit(//d/ws/instances/sidecar/CLAUDE.md)",
                      "WebFetch(domain:example.com)"])
    check("6c ceiling holds at the grant choke point: a never_allow "
          "rule is refused and never reaches the ledger; a clean "
          "rule in the same batch still lands",
          "WebFetch(domain:example.com)" in eng._perm_grants
          and not any("CLAUDE.md" in r for r in eng._perm_grants))
    ledger6 = json.loads((ws_root / "config.json").read_text(
        encoding="utf-8")).get("PERM_ALLOW") or []
    check("6d the refused rule is absent from config.json too (the "
          "ceiling is not merely an in-memory filter), while the "
          "clean rule of the same batch did persist",
          not any("CLAUDE.md" in r for r in ledger6)
          and "WebFetch(domain:example.com)" in ledger6)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9861", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    c.close()
    check("7 clean shutdown", not th.is_alive())

print()
if FAILS:
    print(f"—— {len(FAILS)} checks failed:")
    for f in FAILS:
        print("   " + f)
    sys.exit(1)
print("M16 stage one all green")
