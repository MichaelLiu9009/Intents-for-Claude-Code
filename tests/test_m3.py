"""M3 guard: wizard gate (CASELAW 14) + MCP report-back face
(INTENT_SPEC §6 report law). Host is stood in by FakeHost
(wizard state controllable), bridge runs a real subprocess over
newline JSON-RPC.

Run: PYTHONIOENCODING=utf-8 python tests/test_m3.py
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander.engine import Engine                     # noqa: E402
from commander.kernel.store import Store                # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


class FakeHost:
    """Host stand-in with controllable wizard state -- the engine only
    knows the host through the alive/trusted/inject_chat three faces,
    the stand-in serves exactly those three per contract."""

    def __init__(self):
        self.trust = False
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


def post(port, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ignore_cleanup_errors: Windows keeps the journal handle briefly past
# engine shutdown (same flake class as test_m20's teardown)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)

    st = Store(ws_root / "state.db")
    st.intent_create("报时", title="报告当前时间", scenario="随口一问",
                     steps="Get-Date 报给用户,直接在对话里说",
                     fires=1)
    st.intent_revise("报时", status="provisioned")
    st.compile_delivery("报时")
    st.close()

    eng = Engine(ws_root, http_port=9770, ws_port=9771, spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # ---- wiring artifacts: .mcp.json / engine.json --------------------
    home = ws_root / "instances" / "sidecar"
    cfg = json.loads((home / ".mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["intentOS"]["args"]
    check("1 §settlement .mcp.json lands with the forge, args point "
          "back to workspace + admin face (M26 two-face law)",
          "commander.mcp" in args and "--face" in args
          and "admin" in args
          and any(a for a in args
                  if not a.startswith("-") and Path(a).is_dir()))
    st_json = json.loads((home / ".claude" / "settings.json")
                         .read_text(encoding="utf-8"))
    dirs = st_json["permissions"]["additionalDirectories"]
    check("1b permission floor forges along: runtime / utility / "
          "toolkit go into additionalDirectories (task face skips "
          "the human gate, precedent 2026-08-11; toolkit gap "
          "precedent 2026-08-13 -- the maintenance seat is a "
          "read-write seat)",
          any(d.endswith("runtime") for d in dirs)
          and any(d.endswith("utility") for d in dirs)
          and any(d.endswith("toolkit") for d in dirs))
    check("1c hook face forges along (M13/M18/§2f): Notification + "
          "Stop + PreToolUse (bus) -> hookfwd mailbox + "
          "PermissionRequest -> permfwd",
          set(st_json.get("hooks", {}))
          == {"Notification", "Stop", "PreToolUse",
              "PermissionRequest"}
          and "hookfwd.py" in st_json["hooks"]["Stop"][0]["hooks"][0]
          ["command"])
    mem = Path(st_json["autoMemoryDirectory"])
    check("1c2 private memory pinned: autoMemoryDirectory nailed "
          "into the instance home (default git-repo-root routing "
          "would mix DBs across seats in the same repo); the dir "
          "is prepped, and it's outside engine territory (that's "
          "his own private property)",
          mem == (home / "memory").resolve() and mem.is_dir()
          and not any("memory" in d for d in
                      st_json["permissions"]["deny"]))
    deny = st_json["permissions"]["deny"]
    check("1d engine territory forges along (accountability "
          "mechanism): truth layer read/edit denied, ledger edit "
          "denied, belongings edit denied (incl. settings.local "
          "-- agent can't self-grant)",
          any(d.startswith("Read(") and d.endswith("state.db*)")
              for d in deny)
          and any(d.startswith("Edit(") and d.endswith("state.db*)")
                  for d in deny)
          and any("/records/**)" in d for d in deny)
          and any(d.endswith("/CLAUDE.md)") for d in deny)
          and any(d.endswith("/.claude/**)") for d in deny)
          # Write(path) doesn't participate in file permission matching
          # (verified by test) -- only Edit is listed, so we don't hang
          # a false line of defense
          and not any(d.startswith("Write(") for d in deny))
    check("1e deny paths are POSIX-absolute form (//... -- CLI "
          "normalizes before matching)",
          all(d.partition("(")[2].startswith("//") for d in deny))
    # Same policy, rendered per-platform -- no real mac box here, so we
    # pin it down with a pure-function comparison
    from commander.kernel.provision import posix_rule       # noqa: E402
    check("1f cross-platform render: Windows drive letter folds "
          "into a segment / mac·linux pass through as-is (one "
          "policy, two forms)",
          posix_rule("D:/intents/playground/state.db")
          == "//d/intents/playground/state.db"
          and posix_rule("C:/Users/Y/ws/records")
          == "//c/Users/Y/ws/records"
          and posix_rule("/Users/alice/commander/state.db")
          == "//Users/alice/commander/state.db"
          and posix_rule("/home/y/.local/share/commander")
          == "//home/y/.local/share/commander")
    info = json.loads((ws_root / "runtime" / "engine.json")
                      .read_text(encoding="utf-8"))
    check("2 §settlement port-truth file engine.json (the bridge's "
          "discovery face)",
          info["http"] == 9770 and info["ws"] == 9771)
    md = (home / "CLAUDE.md").read_text(encoding="utf-8")
    # Communication constitution (user ruling 2026-08-12, ahead of the
    # M13 cockpit standing up): the return channel = the terminal
    # itself (the human is right there at the cockpit), so
    # report_to_user's **mandatory** call is retired; the verb itself
    # waits for M14 to be removed (bridge still serves it, the nine
    # tools are unchanged)
    check("3 §settlement CLAUDE.md settlement section: task_done "
          "kept; the report-back duty has retired, layered "
          "reply-back law holds (OS layer replies with OS tools, "
          "chat layer replies in chat)",
          "task_done" in md and "report_to_user" not in md
          and "depends on where it came from" in md)

    # ---- WS observation face -------------------------------------------
    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9771", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))
    frames: queue.Queue = queue.Queue()

    def pump_ws():
        while True:
            try:
                frames.put(json.loads(c.recv()))
            except Exception:
                return

    threading.Thread(target=pump_ws, daemon=True).start()

    def frame_where(pred, timeout=6.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                f = frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(f):
                return f
        return None

    # ---- CASELAW 14: during wizard, chat isn't injected, delivery
    # holds off, and it says so loudly ------------------------------
    c.send(json.dumps({"type": "chat", "text": "你好"}))
    hint = frame_where(lambda f: f.get("type") == "chat"
                       and f.get("name") == "engine"
                       and "wizard" in f.get("text", ""))
    check("4 §14 during wizard, chat is held back and loudly "
          "flagged (not silent)",
          hint is not None and not fake.sent)

    class FakeXHost:            # §2m v9: stand-in for a bare intent's
                                 # execution seat
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

    fx = FakeXHost()
    eng._xhosts["solo"] = fx
    eng._tokens["xst"] = "x·solo"
    c.send(json.dumps({"type": "intent", "name": "报时",
                       "input": "顺便带秒"}))
    row = wait_for(lambda: next(
        (r for r in eng.store.tasks_recent(10)
         if r.get("intent") == "报时" and r["status"] == "running"),
        None))
    check("5 §14x§2m v9 wizard only governs the sidecar seat: a "
          "bare intent reroutes headless, delivered as usual "
          "(zero injection into sidecar)",
          row is not None and fx.delivered and not fake.sent)
    check("6 §6 after delivery the ring holds at running, not "
          "immediately done (the envelope sits at the execution "
          "seat)",
          "[task" in fx.delivered[0][1]
          and eng.store.task(row["id"])["status"] == "running")
    pkg = (ws_root / "runtime" / "tasks" / str(row["id"])
           / "package.md").read_text(encoding="utf-8")
    check("7 §6 payload renders into package's \"user input\" "
          "section", "顺便带秒" in pkg)

    # ---- report-back verbs: rejection comes with a reason (CASELAW 19)
    r = post(9770, {"verb": "task_done", "task": 999,
                    "outcome": "ok", "summary": "x"})
    check("8 §19 nonexistent task: refusal carries a reason "
          "(English face)",
          "no task" in r.get("error", ""))
    r = post(9770, {"verb": "task_done", "task": row["id"],
                    "outcome": "大成功", "summary": "x",
                    "token": "xst"})
    check("9 §19 unknown outcome: refusal carries a reason "
          "(English face)",
          "outcome must be" in r.get("error", ""))
    r = post(9770, {"verb": "task_done", "task": row["id"],
                    "outcome": "ok", "summary": "已报给用户",
                    "token": "xst"})
    check("10 §6 settlement lands: done + track record row",
          r.get("ok") and eng.store.task(row["id"])["status"] == "done"
          and [x["outcome"] for x in eng.store.track("报时")]
          == ["已报给用户"])
    r = post(9770, {"verb": "task_done", "task": row["id"],
                    "outcome": "ok", "summary": "再报一次",
                    "token": "xst"})
    check("11 §19 duplicate settlement: refusal carries a reason "
          "(status isn't running, English face)",
          "only running" in r.get("error", ""))

    # ---- residual wizard-law duty (sidecar seat): sim holds off,
    # resumes delivery once trust lands ---------------------------------
    c.send(json.dumps({"type": "validate", "name": "报时"}))
    time.sleep(1.5)
    sim = next((r for r in eng.store.tasks_recent(10)
                if r.get("spec") == "validate"), None)
    check("11b §14 during wizard the sidecar seat holds delivery "
          "without failing (sim parks at queued)",
          sim is not None and sim["status"] == "queued" and not fake.sent)
    fake.trust = True
    ok = wait_for(lambda: fake.sent
                  and eng.store.task(sim["id"])["status"] == "running")
    check("11c §6 once trust lands, delivery auto-resumes "
          "(sidecar seat), envelope injected",
          bool(ok) and "[task" in fake.sent[0])
    post(9770, {"verb": "task_done", "task": sim["id"],
                "outcome": "ok", "summary": "sim 过检"})   # close out sim job

    # ---- bridge subprocess: full newline JSON-RPC handshake -----------
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    br = subprocess.Popen(
        [sys.executable, "-m", "commander.mcp", str(ws_root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    bq: queue.Queue = queue.Queue()

    def pump_br():
        for line in br.stdout:
            try:
                bq.put(json.loads(line.decode("utf-8", "replace")))
            except ValueError:
                pass

    threading.Thread(target=pump_br, daemon=True).start()

    def rpc(mid, method, params=None):
        br.stdin.write((json.dumps(
            {"jsonrpc": "2.0", "id": mid, "method": method,
             "params": params or {}}, ensure_ascii=False) + "\n")
            .encode("utf-8"))
        br.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < 10:
            try:
                m = bq.get(timeout=0.2)
            except queue.Empty:
                continue
            if m.get("id") == mid:
                return m
        return None

    m = rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
    check("12 bridge initialize handshake (echoes protocol "
          "version, reports tools capability)",
          m and m["result"]["serverInfo"]["name"] == "intentOS"
          and m["result"]["protocolVersion"] == "2025-06-18")
    m = rpc(2, "tools/list")
    check("13 bridge tools/list (M26 two-face law): default = "
          "admin face's nine -- settle/issue/register/retire/"
          "index/search/catalog/sense/fetch; perm_gate/ask_user_through_os "
          "belong to the exec face",
          m and {t["name"] for t in m["result"]["tools"]}
          == {"task_done", "intent_submit", "workspace_submit",
              "intent_retire",
              "match_protocol", "intent_memory_index", "intent_search",
              "intent_catalog", "intent_get"})
    m = rpc(3, "tools/call", {"name": "report_to_user",
                              "arguments": {"text": "现在 21:00 整"}})
    txt14 = (m or {}).get("result", {}).get("content",
                                             [{}])[0].get("text", "")
    check("14 M14 report_to_user is dead: off-face verbs are "
          "refused, no second chat face anymore (chat is "
          "singular = the terminal)",
          m and "not on this seat's face" in txt14)
    m = rpc(4, "tools/call", {"name": "task_done",
                              "arguments": {"task": row["id"],
                                            "outcome": "ok",
                                            "summary": "又来"}})
    txt = m["result"]["content"][0]["text"] if m else ""
    check("15 §19 bridge relays the engine's refusal verbatim "
          "(refusal is an answer, not a protocol error; post-v9 "
          "reroute, a tokenless bridge call on x·solo's task = "
          "executor refuses)",
          "Engine refused" in txt and "not you" in txt)
    br.stdin.close()
    br.wait(timeout=10)

    # ---- shutdown + journal reconciliation -----------------------------
    c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    check("16 clean shutdown", not th.is_alive())
    jdir = next((ws_root / "records" / "sidecar").iterdir())
    rows = [json.loads(x) for x in
            (jdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {(r["kind"], r["name"]) for r in rows}
    check("17 journal: wizard-held / deliver / claim all logged",
          {("host", "wizard-held"), ("chain", "deliver"),
           ("chain", "claim")} <= names)

print()
print("M3 PASS" if not FAILS else f"M3 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
