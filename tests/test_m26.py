"""M26 guard: deck compile + /trigger binding flow + instance seats +
the dual-face law.

1. The register-is-compile half of the deck: protocol keyset lands in
   the book's directory (fixed four keys + member slots, self-routing
   URLs); intents keyset (one-way trigger) lands in utility.
2. /trigger: an intent one-way trigger posts to x·solo; the protocol
   four ops (start/approve/interrupt/shutdown) + member slots route
   by seat.
3. Parallel law: two books open brackets at the same time, each in
   its own seat, unrelated to each other.
4. The Approve key = the top choice on that instance's most recent
   card-stream card.
5. Dual-face law: the admin/exec faces have different tool tables,
   the wrong-face verb is rejected.

Run: PYTHONIOENCODING=utf-8 python tests/test_m26.py
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import _ws  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander.engine import Engine, ProtoInstance      # noqa: E402
from commander.kernel import deckgen, wspace            # noqa: E402
from commander import defaults as _defaults             # noqa: E402

_defaults.PROTO_EXIT_GRACE_S = 0.5      # FakeHost never dies, don't wait for the real grace period

FAILS = []
PORT = 9917


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


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
        f"http://127.0.0.1:{PORT}/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def trigger(qs):
    with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/trigger?{qs}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class FakeHost:
    def __init__(self):
        self.sent = []
        self.keys = []
        self.sizes = []

    def resize(self, cols, rows):
        self.sizes.append((cols, rows))

    def alive(self):
        return True

    def ready(self):
        return True

    def trusted(self):
        return True

    def inject_chat(self, text):
        self.sent.append(text)

    def write_raw(self, data):
        self.keys.append(data)

    def replay(self):
        return ""

    def stop(self):
        pass


class FakeXHost:
    def __init__(self):
        self.delivered = []
        self.reaped = []

    def alive(self):
        return True

    def deliver(self, tid, line):
        self.delivered.append((tid, line))
        return True

    def reap(self, tid):
        self.reaped.append(tid)

    def stop(self):
        pass


def fake_instance(eng, ws_root, pname):
    inst = ProtoInstance(pname, ws_root / "instances" / ("x·" + pname),
                         "sonnet", spawn=False)
    inst.host = FakeHost()
    inst._spawned = True
    eng._xhosts[pname] = inst
    return inst


def read_keyset(path):
    z = zipfile.ZipFile(path)
    try:
        names = z.namelist()
        page = json.loads(z.read(next(
            n for n in names
            if "Profiles/" in n and n.endswith("manifest.json"))))
        top = json.loads(z.read(next(
            n for n in names
            if n.count("/") == 1 and n.endswith("manifest.json"))))
        acts = page["Controllers"][0]["Actions"] or {}
        return top, {a["States"][0]["Title"]: a["Settings"]["path"]
                     for a in acts.values()}
    finally:
        z.close()


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    eng = Engine(ws_root, http_port=PORT, ws_port=PORT + 1,
                 spawn_host=False)
    fake = FakeHost()
    eng.host = fake
    xfake = FakeXHost()
    eng._xhosts["solo"] = xfake
    eng._tokens["xst26"] = "x·solo"
    threading.Thread(target=eng.run, daemon=True).start()
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect(f"ws://127.0.0.1:{PORT + 1}", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))

    # ---- prep: standalone intent 报时 + two books (练题 / 复盘)------------------
    r = post({"verb": "intent_submit", "name": "报时", "title": "报时",
              "scenario": "报时", "steps": "1. report 现在时刻,一句",
              "token": eng.token})
    r = post({"verb": "workspace_submit", "name": "报时",
              "token": eng.token})
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.intent("报时") or {})
             .get("status") == "provisioned")

    r = _ws.proto_ready(
        post, eng, "练题", "# 练题\n主持多轮。",
        [_ws.member_decl("提示", scenario="练题",
                         steps="1. report 给一句提示"),
         _ws.member_decl("解答", scenario="练题",
                         steps="1. report 给出解答")],
        scenario="练题")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.proto_get("练题") or {})
             .get("status") == "provisioned")
    r = _ws.proto_ready(
        post, eng, "复盘", "# 复盘\n主持复盘。",
        [_ws.member_decl("记录", scenario="复盘",
                         steps="1. report 记一笔")],
        scenario="复盘")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.proto_get("复盘") or {})
             .get("status") == "provisioned")

    # ---- 1. register-is-compile: keyset lands in the book's
    #      directory (doesn't enter the toolkit layer)-----------------
    kpath = ws_root / "utility" / "protocols" / "练题" \
        / "练题.streamDeckProfile"
    check("1a protocol keyset lands in the book's directory with the batch",
          kpath.is_file())
    top, keys = read_keyset(kpath)
    check("1b fixed four keys + member slots complete "
          "(Start/Approve/Interrupt/Shutdown + 提示/解答)",
          {"Start", "Approve", "Interrupt", "Shutdown",
           "提示", "解答"} <= set(keys))
    check("1c keys = /trigger self-routing URL (op and member each in "
          "their lane)",
          "op=start" in keys["Start"] and "op=shutdown" in keys["Shutdown"]
          and "member=" in keys["提示"]
          and f"127.0.0.1:{PORT}/trigger" in keys["Start"])
    ipath = ws_root / "utility" / "intents.streamDeckProfile"
    check("1d intents keyset lands in utility with the rack",
          ipath.is_file())
    _, ikeys = read_keyset(ipath)
    check("1e intents keys = one-way trigger (intent=, no op/approve "
          "keys)",
          "报时" in ikeys and "intent=" in ikeys["报时"]
          and not any(t in ikeys for t in ("Approve", "Start")))
    check("1f members don't occupy intents keyset (single-fire locked, "
          "book-internal use)",
          "提示" not in ikeys)

    # ---- 2. /trigger: intent one-way trigger -> x·solo -----------------------
    n0 = len(xfake.delivered)
    ans = trigger("intent=%E6%8A%A5%E6%97%B6")     # 报时
    dt = wait_for(lambda: len(xfake.delivered) > n0)
    check("2a /trigger?intent= posts to x·solo (URL-encoded name "
          "decodes fine)",
          ans.get("ok") is True and bool(dt))
    tk = trigger("engine=task")
    check("2b Task bar probe: latest in-flight solo task (name + "
          "running state)",
          tk.get("name") == "报时" and tk.get("status") == "running"
          and tk.get("more") == 0)
    post({"verb": "task_done", "task": xfake.delivered[-1][0],
          "outcome": "ok", "summary": "报了", "token": "xst26"})
    tk = trigger("engine=task")
    check("2c Task bar flips to done once the task settles",
          tk.get("status") == "done")
    ans = trigger("engine=approve")
    check("2d Solo·Approve gives a rejection reason when no card is "
          "pending",
          ans.get("error") == "nothing pending")
    n1 = len(xfake.delivered)
    trigger("intent=%E6%8A%A5%E6%97%B6")
    wait_for(lambda: len(xfake.delivered) > n1)
    tid2 = xfake.delivered[-1][0]
    ans = trigger("engine=cancel")
    check("2e Solo·Cancel force-interrupts: reap kills the process + "
          "task judged cancelled directly (skips the settle edge, "
          "zero surgical replay)",
          ans.get("ok") is True and ans.get("task") == tid2
          and tid2 in xfake.reaped
          and eng.store.task(tid2)["status"] == "cancelled")

    # ---- 3. protocol's four ops + member slots (seat-based
    #      routing)---------------------
    qinst = fake_instance(eng, ws_root, "练题")
    finst = fake_instance(eng, ws_root, "复盘")
    ans = trigger("protocol=%E7%BB%83%E9%A2%98&op=start")
    br = wait_for(lambda: eng._bracket_of("练题", queued=False))
    check("3a op=start opens the bracket, seats at x·练题",
          ans.get("ok") is True and br is not None
          and br.get("executor") == "x·练题")
    ans2 = trigger("protocol=%E7%BB%83%E9%A2%98&op=start")
    check("3b Start is idempotent: already-open points back to the "
          "same instance, no double-open",
          ans2.get("note") == "already open"
          and (eng._bracket_of("练题") or {}).get("id") == br["id"])
    trigger("protocol=%E7%BB%83%E9%A2%98&member=%E6%8F%90%E7%A4%BA")
    env = wait_for(lambda: next(
        (s for s in qinst.host.sent
         if "intent 提示" in s and "step" in s), None))
    check("3c member slot key posts a step envelope into this book's "
          "instance", env is not None)
    # terminal true-responsive: the cli_size frame resizes ConPTY per
    # seat, values are clamped to a sane range
    c.send(json.dumps({"type": "cli_size", "cols": 100, "rows": 30,
                       "instance": "x·练题"}))
    wait_for(lambda: qinst.host.sizes)
    c.send(json.dumps({"type": "cli_size", "cols": 999, "rows": 3}))
    wait_for(lambda: fake.sizes)
    check("3f cli_size: resizes that instance's PTY per seat, main "
          "host is a separate path, UI-reported numbers never "
          "trusted directly (999x3 -> 400x8)",
          qinst.host.sizes == [(100, 30)]
          and fake.sizes == [(400, 8)])
    # status probe (the dial's data half): read-only, zero side
    # effects, doesn't enter the journal
    n_ev = eng.store._db.execute(
        "SELECT COUNT(*) FROM events").fetchone()[0]
    ans = trigger("protocol=%E7%BB%83%E9%A2%98&op=status")
    ge = trigger("engine=status")
    n_ev2 = eng.store._db.execute(
        "SELECT COUNT(*) FROM events").fetchone()[0]
    check("3g op=status probe: open-book live state (word status + "
          "step ledger), engine=status word is up, polling doesn't "
          "enter journal",
          ans.get("open") is True and ans.get("task") == br["id"]
          and ans.get("live") is True and ans.get("pending") == 0
          and ans.get("status") == "running"
          and ans.get("step") == "提示"
          and ans.get("step_state") == "running"
          and ge.get("ok") is True and ge.get("open") >= 1
          and ge.get("status") == "up"
          and n_ev == n_ev2)

    # parallel: 复盘 opens at the same time, each in its own seat
    trigger("protocol=%E5%A4%8D%E7%9B%98&op=start")
    br2 = wait_for(lambda: eng._bracket_of("复盘", queued=False))
    check("3d M26 parallel law: two books open brackets at the same "
          "time, each seated in its own seat",
          br2 is not None and br2.get("executor") == "x·复盘"
          and (eng._bracket_of("练题") or {}).get("id") == br["id"])
    envf = wait_for(lambda: any(
        "protocol 复盘" in s for s in finst.host.sent))
    check("3e 复盘's package only enters 复盘's own seat",
          bool(envf)
          and not any("protocol 复盘" in s for s in qinst.host.sent))

    # ---- 4. Approve key = the top choice on that seat's most recent
    #      card --------------------------
    eng._tokens["ptk26"] = "x·练题"
    got = {}

    def ask():
        got["ans"] = post({"verb": "ask_user", "token": "ptk26",
                           "question": "下一步?",
                           "options": ["继续", "换题"]})

    th = threading.Thread(target=ask, daemon=True)
    th.start()
    card = wait_for(lambda: next(
        (cd for cd in eng._cards.values()
         if cd.get("instance") == "x·练题" and cd.get("kind") == "ask"),
        None))
    check("4a exec-seat ask card carries an owner (instance = "
          "x·练题)", card is not None)
    trigger("protocol=%E7%BB%83%E9%A2%98&op=approve")
    th.join(timeout=10)
    check("4b op=approve answers the latest card, takes the top "
          "choice",
          not th.is_alive() and isinstance(got.get("ans"), dict)
          and got["ans"].get("choice") == "继续")
    r = post({"verb": "step_done", "member": "提示", "token": "ptk26"})
    st = trigger("protocol=%E7%BB%83%E9%A2%98&op=status")
    check("4c step_done is a lightweight settle: Step flips to done, "
          "book face returns to idle (opens/closes no task)",
          r.get("ok") and st.get("step_state") == "done"
          and st.get("step") == "提示" and st.get("status") == "idle"
          and (eng._bracket_of("练题") or {}).get("id") == br["id"])
    r = post({"verb": "step_done", "token": "xst26"})
    check("4d step_done rejects by seat: x·solo gets the rejection "
          "reason (it goes through task_done)",
          "protocol-seat" in r.get("error", ""))

    # ---- 5. interrupt / shutdown ------------------------------------
    trigger("protocol=%E7%BB%83%E9%A2%98&op=interrupt")
    check("5a op=interrupt = ESC passes straight through to that "
          "seat",
          "\x1b" in qinst.host.keys)
    ans = trigger("protocol=%E7%BB%83%E9%A2%98&op=shutdown")
    check("5b op=shutdown posts the ·wrap step first (wrap-up "
          "ceremony, user's ruling 08-24): bracket not closed, seat "
          "not killed",
          ans.get("ok") is True and ans.get("note") == "wrap-up first"
          and eng._bracket_of("练题") is not None
          and "练题" in eng._xhosts
          and wait_for(lambda: any("·wrap" in s
                                   for s in qinst.host.sent)) is not None)
    post({"verb": "step_done", "member": "·wrap", "token": "ptk26"})
    check("5b2 step_done(·wrap) clears it: closes bracket + stops seat "
          "(ownership retained), other books unaffected",
          wait_for(lambda: eng._bracket_of("练题") is None
                   and "练题" not in eng._xhosts) is not None
          and eng._bracket_of("复盘") is not None)
    check("5c Shutdown = graceful (ESC + /exit keystrokes, tree-kill "
          "is the fallback)",
          wait_for(lambda: "/exit" in qinst.host.sent) is not None
          and "\x1b" in qinst.host.keys)

    # ---- 5d. lazy-spawn retirement + IME two faces (user's ruling
    #      2026-08-23)----------
    ans = trigger("protocol=%E7%BB%83%E9%A2%98&member=%E6%8F%90%E7%A4%BA")
    check("5d member key on a closed book is rejected (lazy-spawn "
          "retired, member keys don't reopen the book)",
          ans.get("error") == "bracket closed"
          and eng._bracket_of("练题") is None)
    ans = trigger("protocol=%E7%BB%83%E9%A2%98&op=status")
    check("5d2 status probe truthfully reports the closed book "
          "(closed word, step ledger buried with it)",
          ans.get("ok") is True and ans.get("open") is False
          and ans.get("task") is None and ans.get("live") is False
          and ans.get("status") == "closed"
          and ans.get("step") is None)
    menu = [r["name"] for r in eng._intent_menu()]
    check("5e sidecar IME lists only non-protocol intents (members "
          "and ·open/·wrap never surface)",
          "报时" in menu and "提示" not in menu and "记录" not in menu
          and not any(n.endswith("·open") or n.endswith("·wrap")
                      for n in menu))
    fi = eng._flow_intents_frame("x·练题")
    check("5f seat IME only has this book's member words",
          fi is not None and fi.get("instance") == "x·练题"
          and [r["name"] for r in fi["rows"]] == ["提示", "解答"]
          and eng._flow_intents_frame("sidecar") is None)

    # ---- 5g. flow-window lifecycle: hello reports seat -> word list;
    #      shutdown -> closes window --
    fc = connect(f"ws://127.0.0.1:{PORT + 1}", open_timeout=5)
    fc.send(json.dumps({"type": "hello", "instance": "x·复盘"}))
    fi_frame = None
    t0 = time.time()
    while time.time() - t0 < 6:
        f = json.loads(fc.recv(timeout=6))
        if (f.get("type") == "intents"
                and f.get("instance") == "x·复盘"):
            fi_frame = f
            break
    check("5g flow window hello reports its seat -> returns this "
          "book's member word-list frame",
          fi_frame is not None
          and [r["name"] for r in fi_frame["rows"]] == ["记录"])
    check("5h one-seat-one-window rule: flow_alive only recognizes "
          "live windows on the books",
          eng.channel.flow_alive("x·复盘")
          and not eng.channel.flow_alive("x·练题"))

    # hub shell (user's ruling 2026-08-23: all instances collapse
    # into one window, one seat per tab): hub reports its seat as
    # ·hub, flow_open's add-tab command is sent only to hub
    hc = connect(f"ws://127.0.0.1:{PORT + 1}", open_timeout=5)
    hc.send(json.dumps({"type": "hello", "instance": "·hub"}))
    wait_for(lambda: eng.channel.flow_alive("·hub"))
    eng.channel.flow_open("x·复盘")
    fo = None
    t0 = time.time()
    while time.time() - t0 < 6:
        f = json.loads(hc.recv(timeout=6))
        if f.get("type") == "flow_open":
            fo = f
            break
    check("5i hub reports its seat + flow_open tab command reaches "
          "hub directly",
          eng.channel.flow_alive("·hub") and fo is not None
          and fo.get("instance") == "x·复盘")
    with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/hub?i=x", timeout=10) as r:
        hub_body = r.read().decode("utf-8")
    check("5j /hub route comes online (tab shell page, observe "
          "folded in as the permanent first tab)",
          "flow_open" in hub_body and "iframe" in hub_body
          and "·engine" in hub_body and '"/observe"' in hub_body)

    trigger("protocol=%E5%A4%8D%E7%9B%98&op=shutdown")   # opens the ·wrap ceremony
    trigger("protocol=%E5%A4%8D%E7%9B%98&op=shutdown")   # second press = force
    got_close = None
    t0 = time.time()
    while time.time() - t0 < 6:
        f = json.loads(fc.recv(timeout=6))
        if f.get("type") == "flow_close":
            got_close = f
            break
    check("5k Shutdown sends flow_close downstream (window "
          "self-close command, instance matches)",
          got_close is not None
          and got_close.get("instance") == "x·复盘")
    hub_close = None
    t0 = time.time()
    while time.time() - t0 < 6:
        f = json.loads(hc.recv(timeout=6))
        # 练题's shutdown's late flow_close might also be in the
        # stream (the teardown thread is async) -- only recognize
        # the one for this book
        if (f.get("type") == "flow_close"
                and f.get("instance") == "x·复盘"):
            hub_close = f
            break
    check("5l flow_close is copied to hub (the tab-removal half)",
          hub_close is not None
          and hub_close.get("instance") == "x·复盘")
    fc.close()
    hc.close()

    # ---- 6. deckgen idempotence (same input, same bytes)--------------------------------
    p1 = deckgen.protocol_keyset(ws_root / "k1", "样例",
                                 ["甲", "乙"], PORT)
    b1 = p1.read_bytes()
    p2 = deckgen.protocol_keyset(ws_root / "k1", "样例",
                                 ["甲", "乙"], PORT)
    check("6 keyset compile is deterministic (same input, same "
          "bytes, hash cross-verifies the law)",
          b1 == p2.read_bytes())

    # ---- 6b. M26b plugin shape (sidebar custom actions; user's
    #      third revision ① 2026-08-22 night: one independent set per
    #      book + a system intents set)--------------
    proot = ws_root / "sdplugins"
    proot.mkdir()
    # pre-seed: the retired merged version + someone else's plugin
    # (sweeping only touches our own prefix)
    (proot / "com.intentos.deck.sdPlugin").mkdir()
    (proot / "com.other.vendor.sdPlugin").mkdir()
    js = SRC / "commander" / "deckplugin" / "plugin.js"
    launch_spec = {"argv": ["python", "-m", "commander", "run"],
                   "cwd": "D:/ws", "env": {"PYTHONPATH": "D:/src"}}
    WSTAG = deckgen.ws_tag("D:/ws")
    paths, swept = deckgen.compile_plugins(
        proot, [("练题", ["提示", "解答"]), ("复盘", [])],
        ["报时"], PORT, js, launch=launch_spec, ws_port=PORT + 1,
        tag=WSTAG)
    by_name = {p.name: p for p in paths}
    pdir = proot / f"{deckgen.proto_plugin_uuid('练题', WSTAG)}.sdPlugin"
    idir = proot / f"{deckgen.intents_plugin_uuid(WSTAG)}.sdPlugin"
    mf = json.loads((pdir / "manifest.json").read_text(encoding="utf-8"))
    mi = json.loads((idir / "manifest.json").read_text(encoding="utf-8"))
    names = [a["Name"] for a in mf["Actions"]]
    check("6b one plugin per book (Category=book name, open/close "
          "merged key + two fixed keys + bare member names) + "
          "intents plugin (engine power-toggle key -- DECK-UI "
          "face-lift)",
          len(paths) == 3 and pdir.name in by_name
          and idir.name in by_name
          and mf["Name"] == "练题" and mf["Category"] == "练题"
          and len(mf["Actions"]) == 3 + 2 + 2
          and "Start / Shutdown" in names and "提示" in names
          and "Status" in names and "Step" in names
          and mf["CodePath"] == "bin/plugin.js"
          and mi["Category"] == "IntentOS · Intents"
          and [a["Name"] for a in mi["Actions"]]
          == ["Engine", "Engine · Status", "Engine · Task",
              "Solo · Approve", "Solo · Cancel", "报时"])
    st_act = next(a for a in mf["Actions"] if a["Name"] == "Status")
    sp_act = next(a for a in mf["Actions"] if a["Name"] == "Step")
    rq = json.loads((pdir / "routes.json").read_text(encoding="utf-8"))
    check("6b3 Status/Step dial: Encoder action ($B1 touch strip), "
          "route carries poll/bar flags, status-color icons ship "
          "with it",
          st_act["Controllers"] == ["Encoder"]
          and st_act["Encoder"] == {"layout": "$B1"}
          and rq[st_act["UUID"]].get("poll") is True
          and rq[st_act["UUID"]].get("bar") == "proto-status"
          and rq[st_act["UUID"]].get("title") == "练题"
          and "op=status" in rq[st_act["UUID"]]["url"]
          and rq[sp_act["UUID"]].get("bar") == "proto-step"
          and (pdir / "imgs" / "st_ok.png").is_file()
          and (idir / "imgs" / "st_run.png").is_file())
    ri = json.loads((idir / "routes.json").read_text(encoding="utf-8"))
    pw_uid = next(a["UUID"] for a in mi["Actions"]
                  if a["Name"] == "Engine")
    check("6b2 engine power-toggle key (press-to-trigger, user's "
          "ruling 08-23 night): tap probes state -- running->stop "
          "/ stopped->start (url+url2+status_url, no hold flag) "
          "+ revival command + power glyph",
          "engine=start" in ri[pw_uid]["url"]
          and "engine=shutdown" in ri[pw_uid]["url2"]
          and "engine=status" in ri[pw_uid]["status_url"]
          and "hold" not in ri[pw_uid]
          and ri[pw_uid].get("launch") == launch_spec
          and ri[pw_uid].get("glyph") == "power")
    # revamped keyface syntax: system keys are pure glyphs with no
    # text, custom keys (member/intent) carry text
    ap_act = next(a for a in mi["Actions"] if a["Name"] == "Solo · Approve")
    ca_act = next(a for a in mi["Actions"] if a["Name"] == "Solo · Cancel")
    it_act = next(a for a in mi["Actions"] if a["Name"] == "报时")
    ps_act = next(a for a in mf["Actions"]
                  if a["Name"] == "Start / Shutdown")
    pm_act = next(a for a in mf["Actions"] if a["Name"] == "提示")
    check("6b4 keyface syntax: system keys ShowTitle=False + glyph "
          "vocabulary (green check/red square force-stop/power "
          "glyph/yellow square), custom keys carry text",
          ap_act["States"][0]["ShowTitle"] is False
          and ri[ap_act["UUID"]].get("glyph") == "check"
          and ri[ca_act["UUID"]].get("glyph") == "stop"
          and ps_act["States"][0]["ShowTitle"] is False
          and it_act["States"][0]["ShowTitle"] is True
          and pm_act["States"][0]["ShowTitle"] is True)
    rq2 = json.loads((pdir / "routes.json").read_text(encoding="utf-8"))
    check("6b5 book open/close merged key (power toggle=open: "
          "url/url2/status_url) + control-key glyphs complete "
          "(power/check/square) + bus address __bus__ + st_queue "
          "status icon",
          {rq2[a["UUID"]].get("glyph")
           for a in mf["Actions"]
           if a["Name"] in ("Start / Shutdown", "Approve", "Interrupt")}
          == {"power", "check", "square"}
          and "op=start" in rq2[ps_act["UUID"]]["url"]
          and "op=shutdown" in rq2[ps_act["UUID"]]["url2"]
          and "op=status" in rq2[ps_act["UUID"]]["status_url"]
          and rq2[ps_act["UUID"]].get("toggle") == "open"
          and rq2.get("__bus__", {}).get("ws") == PORT + 1
          and ri.get("__bus__", {}).get("ws") == PORT + 1
          and (idir / "imgs" / "st_queue.png").is_file())
    routes = json.loads((pdir / "routes.json").read_text(encoding="utf-8"))
    check("6c routes: every action UUID maps to one /trigger URL "
          "(dumb trigger, all guards live in the engine), one "
          "plugin.js per book",
          set(routes) - {"__bus__"} == {a["UUID"] for a in mf["Actions"]}
          and all("/trigger?" in r["url"] for k, r in routes.items()
                  if k != "__bus__")
          and (pdir / "bin" / "plugin.js").is_file()
          and (idir / "bin" / "plugin.js").is_file()
          and (pdir / "imgs" / "plugin.png").is_file())
    paths2, _ = deckgen.compile_plugins(
        proot, [("练题", ["提示", "解答"]), ("复盘", [])],
        ["报时"], PORT, js, launch=launch_spec, ws_port=PORT + 1,
        tag=WSTAG)
    check("6d plugin compile is idempotent (recompile yields the "
          "same output)",
          json.loads((pdir / "manifest.json")
                     .read_text(encoding="utf-8")) == mf
          and len(paths2) == 3)
    # book withdrawn -> that book's plugin becomes an orphan and gets
    # swept; the merged version is already swept; outsiders untouched
    paths3, swept3 = deckgen.compile_plugins(
        proot, [("练题", ["提示", "解答"])], ["报时"], PORT, js,
        tag=WSTAG)
    check("6e orphan sweep: merged version retired, sweeps on book "
          "withdrawal, never touches outside its own prefix",
          "com.intentos.deck.sdPlugin" in swept
          and not (proot / "com.intentos.deck.sdPlugin").exists()
          and f"{deckgen.proto_plugin_uuid('复盘', WSTAG)}.sdPlugin"
          in swept3
          and not (proot
                   / f"{deckgen.proto_plugin_uuid('复盘', WSTAG)}"
                     f".sdPlugin").exists()
          and (proot / "com.other.vendor.sdPlugin").exists()
          and pdir.exists() and idir.exists())
    # audit 2026-08-25: UUIDs used to be md5(book name) with no
    # workspace identity, so a second workspace on the same machine
    # compiled into the SAME directories -- its sweep deleted this
    # one's books and a same-named book repointed its keys at
    # whichever engine compiled last. Namespaces must be disjoint and
    # neither sweep may reach across.
    TAG2 = deckgen.ws_tag("D:/other-ws")
    paths4, swept4 = deckgen.compile_plugins(
        proot, [("练题", ["提示"])], ["报时"], PORT + 100, js,
        tag=TAG2)
    check("6f a second workspace gets its own plugin namespace and "
          "its sweep never reaches the first workspace's books",
          TAG2 != WSTAG
          and not any(WSTAG in s for s in swept4)
          and pdir.exists() and idir.exists()
          and (proot / f"{deckgen.proto_plugin_uuid('练题', TAG2)}"
                       f".sdPlugin").exists()
          and (proot / "com.other.vendor.sdPlugin").exists())

    # ---- 9. v18 procedure rewiring (user's ruling 2026-08-23): a
    # prelude is attached via the intent declaration (optional by
    # API), matched by name against the engine word list at
    # registration time; at trigger time the engine runs the prelude
    # first, then posts the task; if it blows up, report to the human
    # and don't post. The word list is patched for the test (entry
    # supports an absolute path -- pathlib concatenation with an
    # absolute path just overrides).
    from commander import defaults as _dft
    pscript = ws_root / "测采.py"
    pscript.write_text("def run(ctx):\n    ctx.say('现场材料甲', "
                       "label='采样')\n", encoding="utf-8")
    bscript = ws_root / "炸采.py"
    bscript.write_text("def run(ctx):\n    raise RuntimeError("
                       "'sampler broke')\n", encoding="utf-8")
    _dft.PHYS_PROCEDURES["测采"] = {"desc": "测试用采样前奏",
                                    "entry": str(pscript)}
    _dft.PHYS_PROCEDURES["炸采"] = {"desc": "测试用失败前奏",
                                    "entry": str(bscript)}
    r = post({"verb": "intent_submit", "name": "带奏", "scenario": "采样",
              "steps": "1. report 材料已在,复述一句",
              "procedures": ["没这个"], "token": eng.token})
    check("9a submitting with an out-of-word-list prelude rejects "
          "the whole ticket, rejection carries the available word "
          "list",
          "outside the word list" in r.get("error", "")
          and "测采" in r.get("error", ""))
    r = post({"verb": "intent_submit", "name": "带奏", "scenario": "采样",
              "steps": "1. report 材料已在,复述一句",
              "procedures": ["测采"], "token": eng.token})
    r = post({"verb": "workspace_submit", "name": "带奏",
              "token": eng.token})
    check("9b registration-time match passes the word list, batch "
          "card staged lists the prelude",
          "task" in r and any("测采" in s for s in r.get("files", [])))
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.intent("带奏") or {})
             .get("status") == "provisioned")
    check("9c declaration lands in the store (intents.procedures "
          "column, JSON array)",
          "测采" in str((eng.store.intent("带奏") or {})
                        .get("procedures")))
    d9 = _ws.wdir(eng, "带奏")
    check("9c2 N1 primer ships: schema.md lands with the workspace "
          "(intent one table, protocol two -- field teaching happens "
          "at the write site, doesn't occupy standing context)",
          (d9 / wspace.SCHEMA_MD_NAME).is_file()
          and "procedures" in (d9 / wspace.SCHEMA_MD_NAME).read_text(
              encoding="utf-8")
          and (_ws.wdir(eng, "练题")
               / wspace.SCHEMA_MD_NAME).is_file()
          and "Member intent.json field sheet"
          in (_ws.wdir(eng, "练题")
              / wspace.SCHEMA_MD_NAME).read_text(encoding="utf-8"))
    n9 = len(xfake.delivered)
    trigger("intent=%E5%B8%A6%E5%A5%8F")               # 带奏
    d9 = wait_for(lambda: len(xfake.delivered) > n9, timeout=30)
    check("9d trigger runs the prelude before posting the task: "
          "materials render into the package's Materials section",
          d9 is not None
          and "Materials" in xfake.delivered[-1][1]
          and "现场材料甲" in xfake.delivered[-1][1])
    t9 = next((t for t in eng.store.tasks_recent(30)
               if t.get("intent") == "带奏"), None)
    check("9e prelude.ok lands in the task directory (repost "
          "doesn't rerun)",
          t9 is not None
          and (ws_root / "runtime" / "tasks" / str(t9["id"])
               / "prelude.ok").is_file())
    r = post({"verb": "intent_submit", "name": "带炸", "scenario": "采样",
              "steps": "1. report 一句",
              "procedures": ["炸采"], "token": eng.token})
    r = post({"verb": "workspace_submit", "name": "带炸",
              "token": eng.token})
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.intent("带炸") or {})
             .get("status") == "provisioned")
    n9f = len(xfake.delivered)
    trigger("intent=%E5%B8%A6%E7%82%B8")               # 带炸
    tf = wait_for(lambda: next(
        (t for t in eng.store.tasks_recent(30)
         if t.get("intent") == "带炸" and t["status"] == "failed"),
        None), timeout=30)
    check("9f prelude blows up, reports to human, doesn't post the "
          "task: task judged dead, zero delivery to exec seat, "
          "intent stays active (no recycling, no surgery)",
          tf is not None and len(xfake.delivered) == n9f
          and (eng.store.intent("带炸") or {}).get("status")
          == "provisioned"
          and "prelude 炸采" in eng.journal._path.read_text(
              encoding="utf-8"))
    # 9g change of ruling (user's ruling 2026-08-24, overturns v18):
    # member-step preludes unblocked -- a member's in-word-list
    # procedures are accepted for the whole book (pressing the member
    # key makes the engine run the prelude first, the step envelope's
    # tail carries a materials pointer; the closed-loop flow is
    # covered in test_seed); out-of-word-list is still rejected.
    md9 = _ws.member_decl("采一步", scenario="复盘二",
                          steps="1. report 一句")
    md9["procedures"] = ["测采"]
    r = _ws.proto_ready(post, eng, "复盘二", "# 复盘二\n多轮。", [md9],
                        scenario="复盘二")
    check("9g member declaration with in-word-list procedures "
          "accepted for the whole book (member-step prelude, "
          "unblocked 2026-08-24)", "task" in r and "error" not in r)
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    wait_for(lambda: (eng.store.proto_get("复盘二") or {})
             .get("status") == "provisioned")
    check("9g2 member procedures lands in the store "
          "(intents.procedures column)",
          "测采" in str((eng.store.intent("采一步") or {})
                        .get("procedures")))
    md9b = _ws.member_decl("采坏步", scenario="复盘三",
                           steps="1. report 一句")
    md9b["procedures"] = ["没这个"]
    r = _ws.proto_ready(post, eng, "复盘三", "# 复盘三\n多轮。", [md9b],
                        scenario="复盘三")
    check("9g3 member out-of-word-list prelude rejects the whole "
          "book, rejection carries the word list",
          "outside the word list" in r.get("error", ""))

    # ---- 8. Engine power-toggle key (user's ruling 2026-08-23)------------------------
    ans = trigger("engine=start")
    check("8a Engine·Start reaching a live engine = idempotent "
          "receipt (cold start belongs to plugin launch)",
          ans.get("note") == "already running")
    ans = trigger("engine=shutdown")
    check("8b Engine·Shutdown: cascading wrap-up -- sidecar gets "
          "/exit, engine stops itself",
          ans.get("ok") is True
          and wait_for(lambda: "/exit" in fake.sent,
                       timeout=20) is not None
          and wait_for(lambda: eng._stop.is_set(),
                       timeout=20) is not None)

    c.close()
    time.sleep(0.8)

# ---- 7. dual-face law: the bridge surface is trimmed per face -------------------------------------
import os                                                # noqa: E402
import queue                                             # noqa: E402


def bridge_tools(face_args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    br = subprocess.Popen(
        [sys.executable, "-m", "commander.mcp", str(Path(tempfile
                                                         .gettempdir()))]
        + face_args,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    bq: queue.Queue = queue.Queue()

    def pump():
        for line in br.stdout:
            try:
                bq.put(json.loads(line.decode("utf-8", "replace")))
            except ValueError:
                pass

    threading.Thread(target=pump, daemon=True).start()

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

    rpc(1, "initialize")
    tl = rpc(2, "tools/list")
    names = {t["name"] for t in tl["result"]["tools"]}
    descs = {t["name"]: t["description"] for t in tl["result"]["tools"]}
    wrong = rpc(3, "tools/call", {"name": "intent_submit",
                                  "arguments": {"name": "x",
                                                "scenario": "x"}})
    br.kill()
    return names, wrong, descs


exec_names, exec_wrong, exec_descs = bridge_tools(["--face", "exec"])
check("7a exec face = the three-piece set (S2: step_done is a "
      "bracket-seat verb, solo face no longer pays for dead tools)",
      exec_names == {"task_done", "ask_user_through_os", "perm_gate"})
check("7b wrong-face verb rejected (intent_submit not on exec "
      "face)",
      "not on this seat's face"
      in exec_wrong["result"]["content"][0]["text"])
proto_names, _, proto_descs = bridge_tools(["--face", "proto"])
check("7b2 proto face = the three-piece set + step_done, ask_user_through_os "
      "swaps to bracket-law copy (C1: no E on the host seat, real "
      "forks phrasing)",
      proto_names == {"task_done", "ask_user_through_os", "perm_gate",
                      "step_done"}
      and "real forks" in proto_descs["ask_user_through_os"]
      and "E explicitly says" not in proto_descs["ask_user_through_os"]
      and "E explicitly says" in exec_descs["ask_user_through_os"])
admin_names, _, _ = bridge_tools(["--face", "admin"])
check("7c admin face = create/query/settle, zero exec verbs (no "
      "perm_gate/ask_user_through_os/step_done)",
      "intent_submit" in admin_names and "workspace_submit" in admin_names
      and "perm_gate" not in admin_names and "ask_user_through_os" not in admin_names
      and "step_done" not in admin_names)
legacy_names, _, _ = bridge_tools(["exec"])
check("7d legacy positional-arg exec stays compatible",
      legacy_names == exec_names)

print()
print("M26 PASS" if not FAILS else f"M26 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
