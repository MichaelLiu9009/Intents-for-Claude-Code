"""I-E-R execution contract guard (user ruling 2026-08-16).

Ruling chain: turn feature reusability into the executor's
structured spec -- I (input, procedure prepared) · E (execution,
guard-routed command sequence) · R (report, three-state acceptance
criteria, fixed at compile time -- the executor never invents its
own standard). Overlap between steps and instructions eliminated:
instructions -> acceptance (DB keeps the instructions column as a
fossil, the declaration-surface key is renamed). The three states
ride a binary edge (M12: no third-state edge) -- ok_issue routes
as ok, issue is booked, feeding the consolidate loop mechanically
(threshold wiring still pending).

Run: PYTHONIOENCODING=utf-8 python tests/test_ier.py
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import tempfile                                          # noqa: E402

from commander import defaults, mcp                      # noqa: E402
from commander.engine import Engine                      # noqa: E402
from commander.kernel import wspace                      # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ws                                               # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


def wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.15)
    return None


# ---- 1. pure schema surface ---------------------------------------------
check("1a schema:acceptance in table, instructions retired",
      "acceptance" in wspace.SCHEMA and "instructions" not in wspace.SCHEMA)
check("1b steps teaching = intent is a function (grammar + verb "
      "table travels with the schema table)",
      "pseudo-code function body" in wspace.SCHEMA["steps"]["desc"]
      and "if" in wspace.SCHEMA["steps"]["desc"]
      and "the only semantically open" in wspace.SCHEMA["steps"]["desc"])
check("1c steps budget = craft, not a cap (600 for Chinese text, "
      "x2 with the English word list — same-meaning English runs "
      "~2x the characters, the sink-down pressure is unchanged)",
      defaults.INTENT_STEPS_MAX == 1200)

probs = wspace.validate({"name": "x", "scenario": "x",
                         "steps": "1. 做事 | 成→ok;其余→failed",
                         "acceptance": "办成就行"})
check("1d acceptance light validation: missing ok:/failed: "
      "skeleton flagged",
      any("acceptance" in p and "ok:" in p for p in probs))
probs = wspace.validate({"name": "x", "scenario": "x",
                         "steps": "1. report 一句话",
                         "acceptance": "ok: 全走通\nfailed: 其余"})
check("1e valid criteria (half-width colon) + valid E passes "
      "validation", not probs)
probs = wspace.validate({"name": "x", "scenario": "x",
                         "steps": "1. report 一句话",
                         "acceptance": "ok: 全走通\nfailed: 其余"
                         .replace(":", ":")})
check("1f full-width colon also passes (Chinese declarations "
      "aren't picky about punctuation)", not probs)

t = {x["name"]: x for x in mcp.TOOLS}
check("1g MCP task_done three states + issue field",
      t["task_done"]["inputSchema"]["properties"]["outcome"]["enum"]
      == ["ok", "ok_issue", "failed"]
      and "issue" in t["task_done"]["inputSchema"]["properties"])
check("1h MCP intent_submit declaration surface = acceptance "
      "(instructions retired)",
      "acceptance" in t["intent_submit"]["inputSchema"]["properties"]
      and "instructions" not in t["intent_submit"]["inputSchema"]
      ["properties"])

# Bridge field pass-through guard (live incident 2026-08-16:
# _dispatch dropped issue -> the whole ok_issue booking path was
# unusable, executor tried four phrasings and all were rejected;
# same sweep found acceptance also leaking). The suite normally
# connects straight to HTTP, **which happens to bypass this
# bridge** -- so every field must be checked cell by cell.
_sent: dict = {}


def _fake_call(ws, payload, timeout=None):
    _sent.clear()
    _sent.update(payload)
    return "{}"


_orig_call = mcp._call_engine
mcp._call_engine = _fake_call
try:
    mcp._dispatch(Path("."), "task_done",
                  {"task": 1, "outcome": "ok_issue", "summary": "s",
                   "issue": "第 2 行老出摩擦"})
    ok_td = _sent.get("issue") == "第 2 行老出摩擦"
    mcp._dispatch(Path("."), "intent_submit",
                  {"name": "x", "acceptance": "ok: 成\nfailed: 其余"})
    ok_is = "成" in str(_sent.get("acceptance") or "")
finally:
    mcp._call_engine = _orig_call
check("1i bridge passes through task_done.issue (ok_issue's "
      "lifeline: drop it and the whole path is void)",
      ok_td)
check("1j bridge passes through intent_submit.acceptance "
      "(instructions decommissioned)",
      ok_is)
# Prompt-surface minesweep (before the third 2026-08-16 volley):
# the bridge and engine **layer names** must also match -- the
# enum said instructions while the engine expected acceptance, so
# neither name would fetch the criteria.
# After the physical-layer ruling (same night) the chain layer
# retired: part is now just steps/acceptance
check("1k intent_get's part enum = the layer names the engine "
      "recognizes (steps/acceptance; chain retired along with the "
      "physical-layer ruling)",
      t["intent_get"]["inputSchema"]["properties"]["part"]["enum"]
      == ["steps", "acceptance"])
# sim verifies E; if the criteria don't travel with the task it
# has to invent its own standard (proven in a live incident)
check("1l sim/retry's PACKAGE_MD carries an acceptance-criteria "
      "section",
      "{acceptance}" in defaults.PACKAGE_MD
      and "Acceptance criteria" in defaults.PACKAGE_MD)
# CASELAW 56: convention is a compile-time contract, the executor
# can't see it -- the section header must not contradict this
check("1m WS_GUIDE convention section header reworded "
      "(compile-time compliance, not executor compliance)",
      "compile-time" in defaults.WS_GUIDE_MD
      and "never the executor" in defaults.WS_GUIDE_MD)
check("1n executor's standing convention points to the M section "
      "(don't go hunting on your own)",
      "M · methods" in defaults.XSOLO_CLAUDE_MD)
check("1o creation surface carries the toolkit-glance-first rule "
      "(CASELAW 44's sidecar side)",
      "glance at the shared toolkit"
      in defaults.SKILL_INTENT_CREATION_MD)

# ---- 1p+ physical-layer ruling (user ruling, night of 2026-08-16):
# procedure = control-protocol physical layer, built into the
# engine, bound to key positions; chain retires entirely from the
# intent declaration surface; the fires dual-form pairing is voided
# as a result (the fires=0 definition has no content left) -- single
# form only, steps is now required
check("1p schema:chain no longer in table (procedure isn't "
      "declared by intent)",
      "chain" not in wspace.SCHEMA)
check("1q schema:steps required (single form -- intent IS this "
      "E)",
      wspace.SCHEMA["steps"]["required"] is True)
probs = wspace.validate({"name": "x", "scenario": "x",
                         "steps": "1. report 一句",
                         "acceptance": "ok: 成\nfailed: 其余",
                         "chain": ["p"]})
check("1r declaring with chain gets flagged by CASELAW 25's "
      "unknown-key gate (hard rejection, not silent)",
      any("chain" in p for p in probs))
check("1s v18 physical-layer word table: one entry, screenshot "
      "(ime retired along with the trigger-flow concept); value "
      "shape {desc, entry}, entry points to the kernel/procs/ "
      "implementation (extending the table = human-ruled engine "
      "source edit, same rule as the E verb table)",
      set(defaults.PHYS_PROCEDURES) == {"screenshot"}
      and all(("desc" in v and "entry" in v)
              for v in defaults.PHYS_PROCEDURES.values()))
check("1s+ schema:procedures is an optional names field (v18 "
      "prelude declaration hangs off intent)",
      wspace.SCHEMA["procedures"]["required"] is False
      and wspace.SCHEMA["procedures"]["kind"] == "names")
check("1t MCP intent_submit surface has no chain field, teaching "
      "no longer recites the 「零 token」 three-box mnemonic "
      "(motive source of three volleys' feature-stuffing)",
      "chain" not in t["intent_submit"]["inputSchema"]["properties"]
      and "零 token" not in t["intent_submit"]["description"]
      and "physical layer" in t["intent_submit"]["description"])

# ---- 2. engine surface: package rendering + three-state booking -------
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

    def write_raw(self, data):
        pass

    def stop(self):
        pass


# ignore_cleanup_errors: the engine thread holds the journal handle,
# Windows cleanup race is not load-bearing (doesn't affect the
# check's own verdict) -- added 2026-08-23 after two consecutive
# hangs in live testing
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    eng = Engine(ws_root, http_port=9896, ws_port=9897, spawn_host=False)
    fake = FakeHost()
    eng.host = fake

    class FakeXHost:
        def __init__(self):
            self.delivered = []

        def alive(self):
            return True

        def deliver(self, tid, line):
            self.delivered.append((tid, line))
            return True

        def reap(self, tid):
            pass

        def stop(self):
            pass

    xfake = FakeXHost()
    eng._xhosts["solo"] = xfake
    eng._tokens["xst"] = "x·solo"
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.2)

    def post(payload):
        req = urllib.request.Request(
            "http://127.0.0.1:9896/api/mcp",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9897", open_timeout=5)

    # agent-authored intent: E writes the command, R writes the
    # acceptance criteria (declaration-surface acceptance)
    post({"verb": "intent_submit", "name": "查灯", "scenario": "灯测",
          "steps": "1. read 状态文件 -> if 有, (next, failed(无状态文件))\n"
                   "2. report 数字",
          "acceptance": "ok: 报出数字\nok_issue: 报出但格式糊\n"
                        "failed: 其余"})
    r = _ws.register(post, "查灯")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.intent("查灯") or {})
             .get("status") == "provisioned")
    check("2a acceptance lands in DB (instructions fossil column)",
          "报出数字" in (eng.store.intent("查灯") or {})
          .get("instructions", ""))

    eng._on_intent("查灯", "看看灯")
    wait_for(lambda: xfake.delivered)
    tid = xfake.delivered[-1][0]
    pkg = (ws_root / "runtime" / "tasks" / str(tid)
           / "package.md").read_text(encoding="utf-8")
    check("2b package has all three sections (I·E·R) + "
          "interpreter discipline + full criteria text",
          "I · input" in pkg and "E · execution" in pkg
          and "R · report" in pkg and "interpreter" in pkg
          and "报出但格式糊" in pkg)
    # User ruling 2026-08-16: registry entry is filled by the
    # engine -- agent only references the name inside E
    check("2b2 M section is in the order (name maps to path, "
          "executor doesn't need to go hunting) + shared toolkit "
          "pointed to by the order (CASELAW 44's discoverability)",
          "M · methods" in pkg and "toolkit" in pkg)
    # saves a round trip: headless delivery pushes the full text
    # directly, instead of making it Read its own instructions first
    line = xfake.delivered[-1][1]
    check("2b3 headless delivery carries package full text "
          "(pointer form reserved for the PTY seat)",
          f"[task {tid}]" in line and "E · execution" in line
          and "报出但格式糊" in line)
    # Second-pass fix-up (2026-08-17): envelope first line carries
    # no path -- live incident: full text traveled with the task but
    # the header still read "package: <path>", and the executor saw
    # the path and went and Read it anyway
    head = line.splitlines()[0]
    check("2b5 headless envelope first line has no path (order "
          "full text below, no need to read disk)",
          "full order below" in head and "package:" not in head)

    r = post({"verb": "task_done", "task": tid, "outcome": "ok_issue",
              "summary": "报了", "token": "xst"})
    check("2c ok_issue missing issue is rejected (flags the "
          "consolidate-loop feed)",
          "issue" in r.get("error", ""))
    r = post({"verb": "task_done", "task": tid, "outcome": "ok_issue",
              "summary": "报了", "issue": "格式糊,第 2 行该收紧",
              "token": "xst"})
    check("2d ok_issue rides the ok edge: task done, chain "
          "finished, intent as usual",
          r.get("ok") is True
          and (eng.store.task(tid) or {}).get("status") == "done"
          and (eng.store.intent("查灯") or {})
          .get("status") == "provisioned")
    # Live incident 2026-08-16: ok_issue fell into the else branch
    # in the receipt and reported back "failed" -- the sim seat
    # believed it "didn't pass" and went to fix a failure that never
    # happened, burning a whole human-gate cycle for nothing
    check("2d2 receipt is also three-state: ok_issue returns "
          "status=done + explicitly notes not a failure",
          r.get("status") == "done"
          and "not a failure" in str(r.get("note") or ""))
    rec = eng.store.track("查灯")
    check("2e issue enters the track record (mechanical feed for "
          "the consolidate loop)",
          any("issue: 格式糊" in r2["outcome"] for r2 in rec))

    # default criteria: declaration omits acceptance -> renders the
    # default section
    post({"verb": "intent_submit", "name": "素单", "scenario": "素测",
          "steps": "1. report 一句话"})
    r = _ws.register(post, "素单")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.intent("素单") or {})
             .get("status") == "provisioned")
    n = len(xfake.delivered)
    eng._on_intent("素单", "")
    wait_for(lambda: len(xfake.delivered) > n)
    pkg2 = (ws_root / "runtime" / "tasks"
            / str(xfake.delivered[-1][0])
            / "package.md").read_text(encoding="utf-8")
    check("2f acceptance omitted -> renders default criteria "
          "(three states still present)",
          "defaults apply" in pkg2 and "ok_issue" in pkg2)

    # ---- 2g+ physical layer (user ruling, night of 2026-08-16) ----
    # delivery chain single node: the procedure node retires
    # (effect suspend / reroute-to-rework lose their hook point --
    # a physical-layer blowup reports to the human, not the intent)
    spec_steps = [dict(r) for r in eng.store._db.execute(
        "SELECT * FROM chain_spec_steps WHERE spec=? ORDER BY seq",
        ("deliver:查灯",))]
    check("2g delivery chain = single deliver node (procedure "
          "node retired along with the physical layer, no suspend "
          "effect, no reroute-to-rework)",
          len(spec_steps) == 1 and spec_steps[0]["kind"] == "deliver"
          and not spec_steps[0]["effect"])
    # word-table registration: boot-time upsert of built-ins,
    # rows outside the table retire
    seeded = {p["name"] for p in eng.store.procs(status="provisioned")}
    check("2h boot-time registers the physical-layer word table "
          "(procedures table = built-in name registry)",
          seeded == set(defaults.PHYS_PROCEDURES))
    # bind module has been dismantled (user ruling 2026-08-23):
    # bind-time word-table validation retires along with it,
    # reconcile now only cross-checks protocols
    check("2i bind surface fully dismantled (engine has no "
          "_on_bind, store has no bind)",
          not hasattr(eng, "_on_bind")
          and not hasattr(eng.store, "bind"))
    check("2k reconcile as usual (bindings.chain check retired "
          "along with the module)",
          eng.store.reconcile(eng.utility) == [])
    # submitting with chain: the rejection reason is the signpost
    # (the physical layer isn't the agent's to declare)
    r = post({"verb": "intent_submit", "name": "带前奏",
              "scenario": "测", "steps": "1. report 一句",
              "chain": ["ime"]})
    check("2l submitting with chain is rejected, reason points to "
          "the procedures field (v18)",
          "procedures" in r.get("error", ""))
    # ---- 2m+ second-pass fix-up (2026-08-17) -----------------------
    from commander.host.headless import HeadlessHost
    hh = HeadlessHost.__new__(HeadlessHost)
    hh._cli = "claude"
    hh.model = defaults.XSOLO_MODEL
    hh.perm_tool = defaults.XPERM_TOOL
    hh.tools = defaults.XSOLO_CLI_TOOLS
    hh.allow_tools = []          # P1-a floor flag (see test_p1fix)
    a = hh.spawn_args("sid")
    check("2m executor seat built-in tools whitelist (--tools "
          "five file tools + shell; the WebSearch/Agent row never "
          "enters the prompt, deferral has nothing to trigger on)",
          "--tools" in a
          and a[a.index("--tools") + 1] == "Bash,Read,Write,Edit,"
                                           "Glob,Grep")
    # Volley-5 task 3 (2026-08-17): -p <long multi-line text> through
    # CreateProcess makes the whole thing evaporate after a newline
    # -- the task text must go via stdin, never appear in argv
    check("2m2 order never enters argv (-p with no arg = read "
          "stdin; envelope full text piped straight through)",
          "-p" in a and a[a.index("-p") + 1].startswith("--"))
    from commander.kernel.provision import provision_solo_home
    sh = provision_solo_home(ws_root)
    mc = json.loads((sh / ".mcp.json").read_text(encoding="utf-8"))
    check("2n executor seat MCP pins alwaysLoad (three-piece "
          "schema stays resident, the ToolSearch handle-hunting "
          "round trip is gone)",
          mc["mcpServers"]["intentOS"].get("alwaysLoad") is True)
    check("2o toolkit-glance-first rule enters the standing "
          "surface (HOME_CLAUDE_MD -- during the P1 groping phase "
          "skill isn't readable, CASELAW 59: every reset repays "
          "8,600 out)",
          "glance at the shared toolkit" in defaults.HOME_CLAUDE_MD
          and "blood in their comments" in defaults.HOME_CLAUDE_MD)

    c.send(json.dumps({"type": "stop"}))
    time.sleep(1.5)
    c.close()

print()
print("IER PASS" if not FAILS else f"IER FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
