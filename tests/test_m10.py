"""M10 guard: provision plane (INTENT_SPEC §3c, ruling 2026-08-11) --
class / scope / migrated_to / caller pipeline / container law
(hot/cold, §2m v4/v10: container = bound + session usage, cap total
held steady, eviction only, never intrusive) / scoring law /
cold-store search contract. FakeHost stands in for the host, HTTP
face acts as the bridge.

Run with: PYTHONIOENCODING=utf-8 python tests/test_m10.py
"""
import json
import queue
import shutil
import sqlite3
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
from commander.kernel.store import Store, SCHEMA_VERSION  # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    def __init__(self):
        self.trust = True
        self.sent = []

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return self.trust

    def inject_chat(self, text):
        self.sent.append(text)

    def replay(self):
        return ""

    def stop(self):
        pass


def wait_for(fn, timeout=6.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def post(payload):
    req = urllib.request.Request(
        "http://127.0.0.1:9807/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# Container miniaturized (read when engine is invoked): cap=4 -- boot
# binds 1 + get/trigger/bind three usages fill it exactly; the 5th
# usage triggers eviction.
defaults.CONTAINER_CAP = 4

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # Windows handle race; cleanup failure isn't fatal
    ws_root = Path(tmp)

    # ---- v5 additive migration: run it once against a real v4 DB
    # (fixture) ----------------
    fx = Path(__file__).parent / "fixtures" / "playground-2026-08-11.db"
    if fx.is_file():
        mig = ws_root / "mig.db"
        shutil.copy(fx, mig)
        Store(mig).close()
        db = sqlite3.connect(str(mig))
        db.row_factory = sqlite3.Row
        v = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute("SELECT class, scope, migrated_to, last_touched,"
                         " use_score, updated_at FROM intents "
                         "LIMIT 1").fetchone()
        cols = {r[1] for r in db.execute(
            "PRAGMA table_info(chain_spec_steps)")}
        check("1 §3c v4 real DB additive-migrates to current "
              "version (v5 provision cols + v6 node five attrs; "
              "version pinned to SCHEMA_VERSION, no re-edit per "
              "bump), last_touched backfills updated_at",
              v == SCHEMA_VERSION and row["class"] == "未分类"
              and row["scope"] == "sidecar"
              and row["migrated_to"] is None
              and row["last_touched"] == row["updated_at"]
              and {"accounting", "template", "effect", "on_ok",
                   "on_fail"} <= cols)
        db.close()
    else:
        check("1 §3c v4 fixture missing (skip real DB migration "
              "check)", True)

    # ---- population: class / recency / frequency / binding all
    # seeded -------------------------------
    st = Store(ws_root / "state.db")
    pop = [("旧一", "效率"), ("旧二", "效率"), ("常客", "学习"),
           ("新近一", "学习"), ("新近二", "工具"), ("绑着", "工具")]
    for n, c in pop:
        st.intent_create(n, title=n, scenario=f"情景:{n}",
                         steps=f"1. report {n}", cls=c)
        st.intent_revise(n, status="provisioned")
    st.compile_delivery("新近一")
    st.close()
    db = sqlite3.connect(str(ws_root / "state.db"))
    stamps = {"旧一": "2026-01-01 00:00:00", "旧二": "2026-01-02 00:00:00",
              "常客": "2026-01-03 00:00:00", "绑着": "2026-01-04 00:00:00",
              "新近一": "2026-06-01 00:00:00",
              "新近二": "2026-06-02 00:00:00"}
    for n, ts in stamps.items():
        db.execute("UPDATE intents SET last_touched=?, use_score=? "
                   "WHERE name=?", (ts, 5.0 if n == "常客" else 0.0, n))
    db.commit()
    db.close()

    eng = Engine(ws_root, http_port=9807, ws_port=9808, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    # ---- container-law changeover: bind section retired
    # (2026-08-23) -> container starts at zero -------
    check("2 §2m changeover: container zeroed (bound tier retired "
          "with the bind module, pure session LRU) -- everyone "
          "sinks cold, retrieval reverts to vector",
          set(eng._hot) == set())

    # ---- caller pipeline: token minted into .mcp.json, bad token
    # rejected ----------------
    cfg = json.loads((ws_root / "instances" / "sidecar" / ".mcp.json")
                     .read_text(encoding="utf-8"))
    env_tok = cfg["mcpServers"]["intentOS"]["env"].get(
        defaults.MCP_TOKEN_ENV)
    check("3 §3c identity minted by engine: token written into "
          ".mcp.json env",
          env_tok == eng.token and len(env_tok) >= 16)
    r = post({"verb": "intent_memory_index", "token": "bogus"})
    check("4 §3c bad token rejected with reason (English face)",
          "token unrecognized" in r.get("error", ""))

    # ---- index: hot grouped by class + class list + cold-store
    # count (exposes zero score) -----------
    r = post({"verb": "intent_memory_index", "token": env_tok})
    hot = r.get("hot", [])
    check("5 §2l index: hot flattened (class rollup retired -- "
          "class is the user's filing axis, the creation face "
          "shows no menu), container empty at boot",
          r.get("ok") and hot == [])
    check("5b tier tagging retired (bound tier retired with the "
          "bind module, hot is just the session set)",
          all("seg" not in m for m in hot)
          and "bound" not in r.get("note", ""))
    check("6 §2l index: classes key retired; cold_count=6; "
          "container watermark reported",
          "classes" not in r and r.get("cold_count") == 6
          and r.get("container") == f"0/{defaults.CONTAINER_CAP}")

    # ---- cold-store search contract: (class?, query?, limit?) ->
    # {items,total,mode} ------
    r = post({"verb": "intent_search"})
    check("7 §3c search with empty args: full cold set returned "
          "(filters the container, all cold at boot), "
          "total_matched=6, mode=mechanical",
          r.get("ok") and r["total_matched"] == 6
          and {m["name"] for m in r["items"]}
          == {"旧一", "旧二", "常客", "新近一", "新近二", "绑着"}
          and r["mode"] == "mechanical")
    r = post({"verb": "intent_search", "query": "旧一"})
    check("8 §3c/M24 with query = vector dual-column: name hit "
          "bonus ranks first",
          r.get("mode") == "vector" and r["items"]
          and r["items"][0]["name"] == "旧一")
    r = post({"verb": "intent_search", "class": "工具"})
    check("9 class retired: a stray class arg is ignored, "
          "mechanical mode returns the full cold library",
          r.get("ok") and r["total_matched"] == 6
          and r["mode"] == "mechanical")
    r = post({"verb": "intent_catalog"})
    check("9b catalog finalized: usage-ranked top flattened "
          "(no class menu), rows carry only name+scenario "
          "(saves tokens), total covers the whole library",
          r.get("ok") and "classes" not in r and r["total"] == 6
          and {e["name"] for e in r["items"]}
          == {"旧一", "旧二", "常客", "新近一", "新近二", "绑着"}
          and set(r["items"][0]) == {"name", "scenario"})
    defaults.CATALOG_TOP = 1
    r = post({"verb": "intent_catalog"})
    defaults.CATALOG_TOP = 50
    check("9b2 tool is capped: flat top-N by usage, 常客 "
          "(use_score 5) must be the survivor; total still "
          "covers the whole library",
          len(r["items"]) == 1 and r["total"] == 6
          and r["items"][0]["name"] == "常客")
    r = post({"verb": "intent_detail", "name": "旧一"})
    check("9c intent_detail removed (redundant after get went "
          "tiered, viewing=using merged): signpost points to "
          "intent_get",
          "removed" in r.get("error", "")
          and "intent_get" in r["error"])

    # ---- char-flood gate: scenario/steps over limit rejects
    # the whole submission, reason includes char count ----------
    r = post({"verb": "intent_submit", "name": "灌字",
              "steps": "字" * (defaults.INTENT_STEPS_MAX + 1),
              "class": "效率"})
    check("9d char-flood gate on submit: steps over limit rejects "
          "the whole submission, reason carries char count and "
          "next step",
          "over the cap" in r.get("error", "") and "memory" in r["error"]
          and eng.store.intent("灌字") is None)
    _ws.edit(eng, "旧一",
             scenario="水" * (defaults.INTENT_SCENARIO_MAX + 1))
    r = _ws.register(post, "旧一")
    _ws.edit(eng, "旧一", scenario="带 空格")
    r2 = _ws.register(post, "旧一")
    _ws.edit(eng, "旧一", scenario="旧一")     # reset
    check("9e §2u word-gate at registration: scenario "
          "over-length/containing whitespace both rejected (a "
          "one-word context tag), the store record untouched",
          "over the cap" in r.get("error", "")
          and "one word" in r2.get("error", "")
          and eng.store.intent("旧一")["scenario"].endswith("旧一"))
    r = post({"verb": "caveat_add", "intent": "旧一",
              "text": "训" * 10})
    check("9f caveat_add removed: any call gets the signpost "
          "rejection (lessons folded into steps)",
          "removed" in r.get("error", ""))

    # ---- class retired (2026-08-25): the axis is gone, the column
    # is a fossil ------------------
    r = post({"verb": "intent_submit", "name": "野类", "class": "自创",
              "steps": "做"})
    check("9g class retired: any passed-in class is ignored, the "
          "row keeps the fossil column default",
          r.get("ok")
          and (eng.store.intent("野类") or {}).get("class") == "未分类")
    d10 = _ws.decl(eng, "旧一")
    d10["class"] = "自创"
    _ws.write(eng, "旧一", d10)
    r = _ws.register(post, "旧一")
    d10.pop("class")
    _ws.write(eng, "旧一", d10)
    check("9g2 no class field in the declaration (fields outside "
          "the schema table the engine doesn't recognize, called "
          "out at registration)",
          "unknown fields" in r.get("error", "")
          and eng.store.intent("旧一")["class"] == "效率")
    r = post({"verb": "intent_submit", "name": "挤爆", "steps": "做"})
    check("9h no depth gate: the library keeps accepting -- the "
          "cap governs hotness, not store size",
          r.get("ok") and eng.store.intent("挤爆") is not None)

    # ---- name = a word OR a short phrase (user ruling 2026-08-26:
    # English names are phrases -- cap the length, not the word
    # count; dots/path separators stay impossible) ----
    r = post({"verb": "intent_submit", "name": "screen recording",
              "steps": "做"})
    check("9h2 phrase name passes the gate and founds a workspace",
          r.get("ok")
          and eng.store.intent("screen recording") is not None)
    r = post({"verb": "intent_submit", "name": "bad..name",
              "steps": "做"})
    r2 = post({"verb": "intent_submit", "name": "sub/dir",
               "steps": "做"})
    r3 = post({"verb": "intent_submit",
               "name": "way too long a name for any key face at all",
               "steps": "做"})
    check("9h3 dots, path separators and over-length still refused "
          "(path-escape audit holds, cap = INTENT_NAME_MAX; edge "
          "whitespace is stripped before the gate, not refused)",
          "phrase" in r.get("error", "")
          and "phrase" in r2.get("error", "")
          and "phrase" in r3.get("error", ""))

    # ---- scoring law: exposure scores zero, get is partial credit,
    # trigger is full credit ----------------------
    s0 = eng.store.intent("旧一")["use_score"]
    post({"verb": "intent_memory_index"})
    post({"verb": "intent_search"})
    post({"verb": "intent_catalog"})
    post({"verb": "intent_detail", "name": "旧一"})   # dead endpoint doesn't score
    check("10 §3c meta exposure scores zero (index/search/catalog "
          "don't move the score; the dead detail endpoint doesn't "
          "move it either)",
          eng.store.intent("旧一")["use_score"] == s0)
    r = post({"verb": "intent_get", "name": "旧一", "token": env_tok})
    it = eng.store.intent("旧一")
    check("11 §3c intent_get: full record (steps/acceptance; "
          "chain retired per the physical-layer ruling) "
          "+ get is partial credit",
          r.get("ok") and r["steps"] == "1. report 旧一"
          and "chain" not in r and "acceptance" in r
          and it["use_score"] == s0 + defaults.SCORE_GET
          and it["last_touched"] != stamps["旧一"])
    r = post({"verb": "intent_get", "name": "旧一", "part": "steps",
              "token": env_tok})
    check("11b tiered fetch (correcting the mixed-body split): "
          "part=steps returns only that layer, note points to "
          "the rest",
          r.get("ok") and r["steps"] == "1. report 旧一"
          and "acceptance" not in r
          and "layered" in r.get("note", ""))

    # ---- runtime usage enters the container: get means entering
    # the container ------------------------------------
    r = post({"verb": "intent_memory_index"})
    check("12 §3c runtime usage enters the container: 旧一 "
          "enters, cold_count 5, meta carries no steps",
          any(m["name"] == "旧一" and "steps" not in m
              for m in r["hot"])
          and r["cold_count"] == 5)

    # ---- trigger full credit (deliver chain runs as usual) ---------------
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9808", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    frames: queue.Queue = queue.Queue()

    def pump_ws():
        while True:
            try:
                frames.put(json.loads(c.recv()))
            except Exception:
                return

    threading.Thread(target=pump_ws, daemon=True).start()
    s1 = eng.store.intent("新近一")["use_score"]
    c.send(json.dumps({"type": "intent", "name": "新近一", "input": ""}))
    ok = wait_for(lambda: eng.store.intent("新近一")["use_score"]
                  == s1 + defaults.SCORE_TRIGGER)
    check("13 §3c trigger is full credit (piggybacks on "
          "chain_start, zero new sync points)",
          bool(ok))

    # ---- submit: scope stamped; class retired off the card ----------
    post({"verb": "intent_submit", "name": "归档下载", "title": "归档",
          "class": "生活", "scenario": "归档",
          "steps": "1. report 清点并归类", "token": env_tok})
    r = _ws.register(post, "归档下载")
    tpl = (ws_root / "runtime" / "tasks" / str(r["task"])
           / "template.md").read_text(encoding="utf-8")
    row = eng.store.intent("归档下载")
    check("14 §3c ticketing: scope=caller stamped by the engine, "
          "the agent has no such field; passed-in class ignored "
          "(fossil default)",
          row["scope"] == "sidecar" and row["class"] == "未分类")
    check("15 class retired off the registration card: no Class "
          "line renders",
          "Class:" not in tpl and "新类" not in tpl)

    # ---- §2u no door outside the schema table: scope/migrated_to
    # aren't in the declaration at all --------
    d16 = _ws.decl(eng, "旧一")
    d16["migrated_to"] = "emailer/寄信"
    _ws.write(eng, "旧一", d16)
    r = _ws.register(post, "旧一")
    d16.pop("migrated_to")
    _ws.write(eng, "旧一", d16)
    check("16 §3c editor-only columns aren't in the schema table: "
          "registration calls out unknown fields (the engine "
          "can't even look up what's outside the table)",
          "unknown fields" in r.get("error", "")
          and not (eng.store.intent("旧一") or {}).get("migrated_to"))

    # ---- migrated_to: a redirect, not a move (store-side editor
    # action) -----------
    eng.store.intent_revise("旧二", migrated_to="emailer/每日简报")
    r = post({"verb": "intent_search", "query": "旧二"})
    check("17 §3c/M24 pointer = retrieval bridge: migrated rows "
          "don't enter the intent column, the referenced protocol "
          "aggregate surfaces (still findable, identity switched "
          "columns)",
          r.get("mode") == "vector"
          and all(m["name"] != "旧二" for m in r["items"])
          and r.get("protocols")
          and r["protocols"][0]["name"] == "emailer"
          and "旧二" in r["protocols"][0]["hits"])
    r = post({"verb": "intent_submit", "name": "旧二", "scenario": "旧二",
              "steps": "x"})
    check("18 §3c a migrated-away name is still held by the "
          "pointer: rejection carries the migrated signpost",
          "migrated" in r.get("error", "") and "intent.json"
          in r.get("error", ""))

    # ---- usage fills the container to capacity (bind is removed,
    # get enters the container) ------------------------
    post({"verb": "intent_get", "name": "新近二", "token": env_tok})
    post({"verb": "intent_get", "name": "绑着", "token": env_tok})
    r = post({"verb": "intent_memory_index"})
    check("18b §3c usage fills the container to 4/4 capacity, "
          "cold_count 2",
          r["cold_count"] == 2 and r.get("container") == "4/4"
          and {"旧一", "新近一", "新近二", "绑着"} == set(eng._hot))

    # ---- container law: cap total held steady, LRU eviction,
    # discard only, never intrusive ------------------
    post({"verb": "intent_get", "name": "常客", "token": env_tok})
    check("18c §2m container full: the fifth entrant evicts the "
          "least-recently-used member (旧一 leaves; bound "
          "protection retired with the bind module, pure LRU)",
          "常客" in eng._hot and "旧一" not in eng._hot
          and len(eng._hot) == 4)
    r = post({"verb": "intent_search", "query": "旧一"})
    check("18d §2m v10 discard-only, never intrusive: leaving the "
          "container != leaving the store -- the row stays "
          "intact, vector recall still hits (the gate governs "
          "hotness, not recall)",
          eng.store.intent("旧一") is not None
          and r.get("mode") == "vector" and r["items"]
          and r["items"][0]["name"] == "旧一")
    post({"verb": "intent_get", "name": "旧一", "token": env_tok})
    check("18e §2m v10 re-use re-enters the container (LRU): "
          "旧一 re-enters, this time 新近一 sinks out",
          "旧一" in eng._hot and "新近一" not in eng._hot
          and len(eng._hot) == 4)
    r = post({"verb": "intent_get", "names": ["旧一", "旧二"],
              "part": "acceptance", "token": env_tok})
    check("18f intent_get batch (names, part applies to the "
          "whole batch): both items carry only the acceptance "
          "layer (chain layer retired per the physical-layer "
          "ruling)",
          r.get("ok") and len(r.get("items", [])) == 2
          and all(x.get("ok") and "acceptance" in x
                  and "steps" not in x for x in r["items"]))

    # ---- static instruction surface + journal ------------------------------
    md = (ws_root / "instances" / "sidecar" / "CLAUDE.md").read_text(
        encoding="utf-8")
    check("19 §3c CLAUDE.md: boot-time self-serve instructions "
          "(index/search/get three faces), no static intent list",
          "intent_memory_index" in md and "intent_search" in md
          and "class" not in md.lower()
          and "你的 intents" not in md)
    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    getrow = next((r for r in rows if (r["kind"], r["name"])
                   == ("intent", "get")), {})
    evictrows = [r for r in rows if (r["kind"], r["name"])
                 == ("intent", "container-evict")]
    check("20 journal: container-reset logs the changeover, get "
          "logs caller, each eviction logs an entry (旧一, 新近一, "
          "新近二 -- 18f's batch get brings 旧二 into the "
          "container, pure LRU evicts one more member)",
          ("intent", "container-reset") in names
          and getrow.get("caller") == "sidecar"
          and {r.get("intent") for r in evictrows}
          == {"旧一", "新近一", "新近二"})

print()
print("M10 PASS" if not FAILS else f"M10 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
