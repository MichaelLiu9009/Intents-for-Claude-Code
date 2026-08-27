"""M22 guard (§2i): executor-seat permission gate + multiple-choice
form -- card-stream blocking arbitration, allow-just-this-once,
deny/timeout fail-safe, xhost wired to --permission-prompt-tool,
member-count law rejects out-of-bounds.

Run: PYTHONIOENCODING=utf-8 python tests/test_m22.py
"""
import json
import queue
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


def post(path, payload, timeout=30):
    req = urllib.request.Request(
        f"http://127.0.0.1:9748{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


SKILL_X = """# 观测聚合
## intent:报时
Get-Date。
"""

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    st = Store(ws_root / "state.db")
    for nm in ("报时", "写卡", "查天"):
        st.intent_create(nm, title=nm, steps=nm + "步骤", fires=1)
        st.intent_revise(nm, status="provisioned")
        st.compile_delivery(nm)
    st.close()

    eng = Engine(ws_root, http_port=9748, ws_port=9749, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9749", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    frames: queue.Queue = queue.Queue()

    def pump_ws():
        while True:
            try:
                frames.put(json.loads(c.recv()))
            except Exception:
                return

    threading.Thread(target=pump_ws, daemon=True).start()

    def frame_where(pred, timeout=8.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                f = frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(f):
                return f
        return None

    # ---- v14: permission gate / multiple-choice question wired to
    #      the general executor seat x·solo -----------------------
    r = post("/api/mcp", {"verb": "intent_submit", "name": "观测",
                          "kind": "protocol", "subtype": "executor"})
    check("1 §2m v14 subtype gate: executor kind is dismantled, "
          "rejection carries a signpost",
          "multi-round bracket" in r.get("error", ""))
    xh = eng._xhost("solo")
    check("3 §2i x v14 executor seat wired to the permission gate "
          "(--permission-prompt-tool points at "
          "mcp__intentOS__perm_gate)",
          xh is not None and xh.perm_tool == defaults.XPERM_TOOL)
    xtok = next(k for k, v in eng._tokens.items() if v == "x·solo")

    # ---- perm_gate: allow path -------------------------------------------
    res: dict = {}

    def call_gate(key, payload):
        res[key] = post("/api/mcp", payload, timeout=40)

    th = threading.Thread(target=call_gate, args=("allow", {
        "verb": "perm_gate", "tool_name": "Edit",
        "input": {"file_path": "D:/手账/x.md"}, "token": xtok}))
    th.start()
    card = frame_where(lambda f: f.get("type") == "card"
                       and "Permission request" in str(f.get("title")))
    check("4 §2i permission-request card: seat name + tool + rough "
          "target, allow/always/deny three-way choice (permission "
          "surface consolidated 2026-08-24: Always = enters the "
          "PERM_ALLOW ledger)",
          card is not None and "Edit" in card.get("body", "")
          and {o["action"] for o in card["options"]}
          == {"allow", "always", "deny"})
    # gate-card exemption (live-fire 2026-08-14): a human typing at
    # the terminal must not sweep the gate card -- sweeping it while
    # the gate is still blocking would leave a wait nobody can
    # answer; the gate card only closes on card_answer / timeout
    c.send(json.dumps({"type": "cli_in", "data": "x"}))
    swept = frame_where(lambda f: f.get("type") == "card_close"
                        and f.get("id") == card["id"], timeout=1.5)
    check("4b card-sweep law gate-card exemption: cli-engaged "
          "doesn't sweep the executor-seat permission-request card",
          swept is None and th.is_alive())
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "allow"}))
    th.join(timeout=10)
    check("5 §2i allow just-this-once: returns behavior=allow + "
          "updatedInput unchanged",
          res.get("allow", {}).get("behavior") == "allow"
          and res["allow"]["updatedInput"]["file_path"] == "D:/手账/x.md")
    check("5b allow-once banks nothing: the result carries no "
          "updatedPermissions, so the CLI has no rule to persist -- "
          "and the deck's Approve key takes options[0], which is "
          "Allow once by construction (_seat_approve)",
          "updatedPermissions" not in res.get("allow", {})
          and "decisionClassification" not in res.get("allow", {}))

    # ---- perm_gate: deny path --------------------------------------------
    th = threading.Thread(target=call_gate, args=("deny", {
        "verb": "perm_gate", "tool_name": "Bash",
        "input": {"command": "rm -rf /"}, "token": xtok}))
    th.start()
    card = frame_where(lambda f: f.get("type") == "card"
                       and "Permission request" in str(f.get("title"))
                       and "Bash" in str(f.get("body")))
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "deny"}))
    th.join(timeout=10)
    check("6 §2i deny: behavior=deny + message carries the "
          "rejection reason (English face)",
          res.get("deny", {}).get("behavior") == "deny"
          and "denied" in res["deny"].get("message", ""))

    # ---- perm_gate: always -> the CLI banks its own copy -------------------
    th = threading.Thread(target=call_gate, args=("always", {
        "verb": "perm_gate", "tool_name": "Glob",
        "input": {"pattern": "*.md"}, "token": xtok}))
    th.start()
    card = frame_where(lambda f: f.get("type") == "card"
                       and "Permission request" in str(f.get("title"))
                       and "Glob" in str(f.get("body")))
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "always"}))
    th.join(timeout=10)
    a = res.get("always", {})
    up = a.get("updatedPermissions") or []
    check("5c §2i Always allow -> the result carries updatedPermissions, "
          "so **the CLI persists the rule into this seat's own "
          "settings** and the next order raises no card at all "
          "(live-fire 2026-08-25 probes D/E/F: a prompt-tool allow "
          "runs the same persistence the native card's don't-ask-"
          "again row does; probe L: that landing is local scope, the "
          "one scope an untrusted headless home still honors)",
          a.get("behavior") == "allow"
          and a.get("decisionClassification") == "user_permanent"
          and len(up) == 1 and up[0].get("type") == "addRules"
          and up[0].get("behavior") == "allow"
          and up[0].get("destination") == "localSettings"
          and up[0].get("rules") == [{"toolName": "Glob"}])

    # ---- perm_gate: timeout fail-safe -------------------------------------
    defaults.XGATE_WAIT_S = 2.0
    r = post("/api/mcp", {"verb": "perm_gate", "tool_name": "Write",
                          "input": {}, "token": xtok}, timeout=40)
    check("7 §2i waiting-for-human timeout defaults to deny "
          "(fail-safe), message points to the summary callout "
          "(English face)",
          r.get("behavior") == "deny"
          and "no answer" in r.get("message", ""))
    defaults.XGATE_WAIT_S = 300.0

    # ---- ask_user_through_os: form path ---------------------------------------------
    th = threading.Thread(target=call_gate, args=("form", {
        "verb": "ask_user", "question": "手账写到哪本?",
        "options": ["主本", "灵感本", "废纸篓"], "token": xtok}))
    th.start()
    card = frame_where(lambda f: f.get("type") == "card"
                       and "Executor question" in str(f.get("title")))
    check("8 §2i multiple-choice card: prompt + options (isomorphic "
          "to AskUserQuestion)",
          card is not None and "手账写到哪本" in card.get("body", "")
          and [o["label"] for o in card["options"]]
          == ["主本", "灵感本", "废纸篓"])
    c.send(json.dumps({"type": "card_answer", "id": card["id"],
                       "action": "opt:1"}))
    th.join(timeout=10)
    check("9 §2i form answer echoes the option verbatim",
          res.get("form", {})
          .get("choice") == "灵感本")
    r = post("/api/mcp", {"verb": "ask_user", "question": "",
                          "options": [], "token": xtok})
    check("10 §2i form gate: empty question/empty options rejected",
          "error" in r)

    c.send(json.dumps({"type": "stop"}))
    time.sleep(2)
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(
                encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    check("11 journal: xgate perm / form logged",
          {("xgate", "perm"), ("xgate", "form")} <= names)

print()
print("M22 PASS" if not FAILS else f"M22 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
