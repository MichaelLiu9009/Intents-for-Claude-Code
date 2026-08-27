"""§2j guard, post class-retirement (user ruling 2026-08-25): the
catalog is a flat usage top, the class column is a fossil (default
value only, engine never computes one), the workspace layout is flat
(root/<name>) with a one-shot legacy flatten, the protocol-family
sample pool + match_by_scenario, the protocol total-count gate.

Run: PYTHONIOENCODING=utf-8 python tests/test_cluster.py
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
from commander.kernel import wspace                     # noqa: E402
from commander.kernel.store import Store                # noqa: E402

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

    def replay(self):
        return ""

    def stop(self):
        pass


def wait_for(fn, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def post(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:9752/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


SKILL_X = """# 手账聚合
## intent:报时
Get-Date 写进手账。
"""

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    for nm, cls, scen in (("练琴", "生活", "练琴"),):
        st.intent_create(nm, title=nm, steps=nm + "步骤", fires=1,
                         cls=cls, scenario=scen)
        st.intent_revise(nm, status="provisioned")
        st.compile_delivery(nm)
    st.close()

    # stale textbook fixture: rendered by an older engine, still
    # teaching a retired field — boot must re-render it
    stale_ws = ws_root / "instances" / "sidecar" / "练琴"
    stale_ws.mkdir(parents=True, exist_ok=True)
    (stale_ws / "intent.json").write_text(json.dumps(
        {"name": "练琴", "scenario": "练琴", "steps": "1. report x"},
        ensure_ascii=False), encoding="utf-8")
    (stale_ws / "schema.md").write_text(
        "- `caveats` — stale row from an older engine\n",
        encoding="utf-8")

    eng = Engine(ws_root, http_port=9752, ws_port=9753, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9753", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))

    # ---- class retired (2026-08-25): the engine computes no class,
    # the row carries only the fossil default --
    r = post({"verb": "intent_submit", "name": "煮面", "title": "煮面",
              "scenario": "做饭", "steps": "煮一碗面"})
    check("00 class retired: submit files nothing -- the row keeps "
          "the fossil column default",
          r.get("ok")
          and (eng.store.intent("煮面") or {}).get("class") == "unfiled")
    check("00b flat layout: the workspace lands at root/<name> "
          "(no class shell)",
          (_ws.home(eng) / "煮面" / "intent.json").is_file())
    # ---- name gate (audit 2026-08-25): the name doubles as the
    # workspace directory name and reaches wspace.provision as a
    # path component, written by the engine itself -- so neither the
    # CLI permission system nor the deny floor is in that path --
    siblings = {p.name for p in _ws.home(eng).parent.iterdir()}
    # "a b" left the hostile list (user ruling 2026-08-26: a name is
    # a word or a short phrase — internal spaces are legal now, the
    # positive pin lives in test_m10 9h2); path shapes stay hostile.
    hostile = ["../evil", "..\\evil", "C:/evil", "//srv/share/evil",
               "a.b", "x/y", ""]
    refused = all(
        "name" in post({"verb": "intent_submit", "name": h,
                        "title": "x", "scenario": "x",
                        "steps": "1. report x"}).get("error", "")
        for h in hostile)
    check("00d path-shaped names refused before anything is written "
          "(no directory escapes the home, no orphan row)",
          refused
          and {p.name for p in _ws.home(eng).parent.iterdir()} == siblings
          and all(eng.store.intent(h) is None for h in hostile))
    sc = (stale_ws / "schema.md").read_text(encoding="utf-8")
    check("00c boot re-renders every workspace's schema.md from "
          "the live table (a stale textbook can't keep teaching a "
          "retired field)",
          "caveats" not in sc and "field sheet" in sc)

    # ---- graduating "手账" migrates the three 工具-class members out ---------------------------------
    scratch = ws_root / "instances" / "sidecar" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "sk.md").write_text(SKILL_X, encoding="utf-8")
    r = _ws.proto_ready(
        post, eng, "手账", SKILL_X,
        [_ws.member_decl("报时", scenario="看点"),
         _ws.member_decl("写卡", scenario="记事"),
         _ws.member_decl("查天", scenario="看天")])
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    ok0 = wait_for(lambda: (eng.store.proto_get("手账") or {})
                   .get("status") == "provisioned")
    check("0 pre-req: three members declared with the book, whole "
          "book lands atomically in one gate (v17 compile unit)",
          bool(ok0) and bool(eng.store.proto_of_member("报时")))
    # ---- catalog: flat usage top, tool-capped ------
    r = post({"verb": "intent_catalog"})
    check("1 catalog flat shape: usage-top list, rows carry only "
          "name+scenario (migrated-out entries still listed -- "
          "material identity distinguishable via search's two "
          "columns)",
          "classes" not in r and r.get("total") == 4
          and {x["name"] for x in r["items"]}
          == {"报时", "写卡", "查天", "练琴"}
          and all(set(x) == {"name", "scenario"} for x in r["items"]))
    defaults.CATALOG_TOP = 2
    r = post({"verb": "intent_catalog"})
    defaults.CATALOG_TOP = 50
    check("2 tool cap: flat top-2, total still carries the full "
          "count",
          len(r["items"]) == 2 and r["total"] == 4)
    r = post({"verb": "intent_catalog", "category": "工具"})
    check("3 filter-param rejected: category arg hits the guard "
          "(the catalog takes no filter)",
          "no filter" in r.get("error", ""))

    # ---- legacy layout flatten (one-shot boot migration) ----
    legacy = _ws.home(eng) / "旧类"
    (legacy / "老件").mkdir(parents=True, exist_ok=True)
    (legacy / "老件" / wspace.DECL_NAME).write_text(
        json.dumps({"name": "老件", "scenario": "旧", "steps": "1. "
                    "report x"}, ensure_ascii=False), encoding="utf-8")
    moved = wspace.flatten_legacy(_ws.home(eng))
    check("4 legacy <class>/<name>/ flattens to root/<name> (shell "
          "removed, decl intact)",
          any("老件" in m for m in moved)
          and (_ws.home(eng) / "老件" / wspace.DECL_NAME).is_file()
          and not legacy.exists())
    r = post({"verb": "intent_submit", "name": "算账2", "title": "算账2",
              "steps": "再算一笔", "scenario": "记账"})
    check("5 no depth gate: the library keeps accepting (caps "
          "govern hotness, not store size)",
          bool(r.get("ok"))
          and eng.store.intent("算账2") is not None)

    # ---- match_protocol carries only the family pool ---------------
    r = post({"verb": "match_protocol", "scenario": "记账"})
    check("6 match_protocol: response carries only the "
          "protocol-family pool",
          "classes" not in r and r.get("ok"))
    check("7 §2j protocol-family sample pool: member scenario "
          "belongs to the protocol",
          set(r["protocols"]["手账"]["samples"])
          == {"看点", "记事", "看天"}
          and r["protocols"]["手账"]["subtype"] == "interactive")

    # ---- protocol total-count gate ----------------------------------------------------
    defaults.PROTO_TOTAL_MAX = 1
    r = _ws.open_proto(post, "另族")
    check("8 §2j protocol total-count gate: full quota rejects "
          "new (same-name resubmit doesn't consume quota)",
          "total count is full" in r.get("error", ""))
    r = _ws.open_proto(post, "手账")
    check("9 §2j same-name resubmit allowed (revision doesn't "
          "consume new quota)", bool(r.get("ok")))
    defaults.PROTO_TOTAL_MAX = 50

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)

print()
print("CLUSTER PASS" if not FAILS else f"CLUSTER FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
