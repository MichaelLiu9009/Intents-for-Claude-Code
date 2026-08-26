"""M24 guard: vector surface v1 -- the mechanical embedder (1+2-gram
cosine), intent_search's two columns (top-25 candidates -> <=5
intents + <=1 protocol, a pointer = the retrieval bridge), a
similarity threshold (legitimately coming back empty), rev
re-embedding self-invalidates, match_protocol's merged scoring;
hard gates (steps cap; the procedure gate has retired along with the
physical-layer ruling).

Run: PYTHONIOENCODING=utf-8 python tests/test_vector.py
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
from commander.kernel import vector                     # noqa: E402
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
        "http://127.0.0.1:9756/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


SKILL_X = """# 手账聚合
## intent:报时
Get-Date 写进手账。
"""

# ---- embedder pure-function check (no engine needed) ------------------
va, vb = vector.embed("练琴看谱"), vector.embed("练琴节奏")
check("0a embedder: same-domain scenario similarity clearly > "
      "cross-domain",
      vector.sim(va, vb) > vector.sim(va, vector.embed("记录开销")))
check("0b embedder: empty text → empty vector, sim=0",
      vector.embed("") == {} and vector.sim({}, va) == 0.0)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    for nm, scen in (("煮面", "做饭煮面"), ("备菜", "做饭备菜")):
        st.intent_create(nm, title=nm, steps=f"1. report {nm}步骤", fires=1,
                         cls="生活", scenario=scen)
        st.intent_revise(nm, status="provisioned")
        st.compile_delivery(nm)
    st.close()

    eng = Engine(ws_root, http_port=9756, ws_port=9757, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9757", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))

    # ---- prerequisite: the protocol 手账 absorbs three members
    #      (migrated-out pointers in place) -----------------------------
    scratch = ws_root / "instances" / "sidecar" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "sk.md").write_text(SKILL_X, encoding="utf-8")
    r = _ws.proto_ready(
        post, eng, "手账", SKILL_X,
        [_ws.member_decl("报时", scenario="看点"),
         _ws.member_decl("写卡", scenario="记事"),
         _ws.member_decl("查天", scenario="看天")])
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    ok1 = wait_for(lambda: (eng.store.proto_get("手账") or {})
                   .get("status") == "provisioned")
    check("1 prereq: three members declared along with the book, "
          "whole-book one-gate atomic go-live (v17 compilation "
          "unit)",
          bool(ok1) and bool(eng.store.proto_of_member("报时"))
          and (eng.store.intent("写卡") or {}).get("proto") == "手账")

    # ---- two columns: pointer = retrieval bridge -----------------------
    r = post({"verb": "intent_search", "query": "记事写卡"})
    check("2 M24 two lanes: hitting a migrated-out member surfaces "
          "the aggregate protocol (≤1, carries hits member names)",
          r.get("mode") == "vector" and len(r.get("protocols", [])) == 1
          and r["protocols"][0]["name"] == "手账"
          and "写卡" in r["protocols"][0]["hits"]
          and r["protocols"][0]["score"] > 0)
    check("3 M24 two lanes: pointed items don't enter the intent "
          "lane (intent match only includes unpointed ones)",
          all(m["name"] not in ("报时", "写卡", "查天")
              for m in r.get("items", [])))

    # ---- unpointed recall + rows carrying context -----------------------
    r = post({"verb": "intent_search", "query": "做饭"})
    names = {m["name"] for m in r["items"]}
    check("4 M24 scenario recall: unpointed items enter items by "
          "cosine",
          r["mode"] == "vector" and names == {"煮面", "备菜"}
          and not r.get("protocols"))
    check("5 M24 rows carry name/title/scenario + score (context "
          "comes back with the hit)",
          all(k in r["items"][0] for k in
              ("name", "title", "scenario", "score")))

    # ---- name-hit bonus (second lane of multi-path recall) --------------
    r = post({"verb": "intent_search", "query": "煮面"})
    check("6 M24 name-hit bonus: a named match ranks first",
          r["items"] and r["items"][0]["name"] == "煮面")

    # ---- threshold: rather empty than forced -----------------------------
    r = post({"verb": "intent_search", "query": "qqqq"})
    check("7 M24 similarity threshold: below threshold is "
          "legitimately empty-handed (empty lane + total 0)",
          r.get("ok") and r["items"] == [] and r.get("protocols") == []
          and r["total_matched"] == 0 and r["mode"] == "vector")

    # ---- top-of-cap truncation -------------------------------------------
    defaults.SEARCH_TOP_INTENTS = 1
    r = post({"verb": "intent_search", "query": "做饭"})
    check("8 M24 intent lane truncates at the cap (total still "
          "carries the full count)",
          len(r["items"]) == 1 and r["total_matched"] == 2)
    defaults.SEARCH_TOP_INTENTS = 5

    # ---- rev re-embedding self-invalidates -------------------------------
    _ws.edit(eng, "煮面", scenario="看盘")
    r = _ws.register(post, "煮面")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    ok9 = wait_for(lambda: (eng.store.intent("煮面") or {})
                   .get("scenario") == "看盘")
    check("9a §2u revision channel works (edit intent.json's "
          "scenario + re-register)",
          bool(ok9))
    r = post({"verb": "intent_search", "query": "做饭"})
    r2 = post({"verb": "intent_search", "query": "看盘"})
    check("9 M24 edit = instant re-embed (rev self-invalidates, "
          "zero hooks): old scenario no longer matches, new "
          "scenario matches right away",
          {m["name"] for m in r["items"]} == {"备菜"}
          and {m["name"] for m in r2["items"]} == {"煮面"})

    # ---- mechanical mode unchanged ----------------------------------------
    r = post({"verb": "intent_search"})
    check("10 no query = mechanical as before (zero contract "
          "breach)",
          r.get("mode") == "mechanical" and "protocols" not in r)

    # ---- match_protocol merged scoring ------------------------------------
    r = post({"verb": "match_protocol", "scenario": "记事"})
    check("11 M24 match_protocol merged: family pool carries a "
          "same-scale score",
          r["protocols"]["手账"]["score"] > 0)

    # ---- §2m v6 hard gate ----------------------------------------------
    r = post({"verb": "intent_submit", "name": "长文", "title": "长文",
              "scenario": "写作",
              "steps": "x" * (__import__("commander.defaults",
                                         fromlist=["x"])
                              .INTENT_STEPS_MAX + 1)})
    check("12 hard gate: steps over the cap rejects the whole "
          "submission (gate still stands after I-E-R relaxation)",
          "steps over the cap" in r.get("error", ""))
    r = post({"verb": "intent_submit", "name": "双链", "title": "双链",
              "scenario": "写作", "steps": "y",
              "chain": ["甲", "乙"]})
    check("13 submitting with chain rejects the whole submission "
          "(v18 rejection points to the procedures field; the old "
          "CHAIN_MAX/40 line gate retired along with the "
          "agent-submit interface)",
          "procedures" in r.get("error", ""))

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)

print()
print("VECTOR PASS" if not FAILS else f"VECTOR FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
