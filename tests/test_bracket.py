"""§2 bracket-surface guard (v16 rewrite, user's ruling 2026-08-16
night).

The original segments 1/3/6/7 (mechanical members run procedures /
implicit say / straight-line mechanical / prelude blows up -> retry)
have all retired together under the physical-layer ruling: procedure
= an engine built-in mount point, it no longer enters the delivery
chain; the fires dual-form side-by-side has been voided, members are
uniformly agent-shaped (the envelope is delivered to the host seat).
Guards that remain:

1. CASELAW 46: task_done landing on a protocol bracket task -> the
   engine hard-rejects it (when two skill books clash, the mechanical
   law wins).
2. A member trigger only posts an envelope, doesn't open a new task
   (one ledger for the whole bracket); a non-member rejection points
   the way.
3. CASELAW 41/48/51: injection acknowledgment -- the criterion is a
   user row, not mtime.

Run: PYTHONIOENCODING=utf-8 python tests/test_bracket.py
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
from commander.engine import Engine, ProtoInstance      # noqa: E402
from commander.kernel import prune_report               # noqa: E402

# point the injection-ack observation at tmp (the real transcript
# directory is irrelevant to the test)
_ackdir_holder = {}
prune_report.transcript_dir = lambda home: _ackdir_holder["d"]

FAILS = []


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
        "http://127.0.0.1:9894/api/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class FakeHost:
    def __init__(self):
        self.sent = []
        self.keys = []

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


def fake_instance(eng, ws_root, pname):
    """M26: stand-in for a protocol's seat -- a real ProtoInstance
    shell (queue/release gate stay real), the host swapped for
    FakeHost (doesn't spawn a real CLI)."""
    inst = ProtoInstance(pname, ws_root / "instances" / ("x·" + pname),
                         "sonnet", spawn=False)
    inst.host = FakeHost()
    inst._spawned = True
    eng._xhosts[pname] = inst
    return inst


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws_root = Path(tmp)
    ack = ws_root / "transcripts"
    ack.mkdir()
    _ackdir_holder["d"] = ack

    eng = Engine(ws_root, http_port=9894, ws_port=9895, spawn_host=False)
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
    time.sleep(1.5)

    from websockets.sync.client import connect
    c = connect("ws://127.0.0.1:9895", open_timeout=5)
    c.send(json.dumps({"type": "hello"}))

    # ---- prep: protocol 练琴 = a compilation unit (v17, user's
    # ruling 2026-08-16 late night): members are declared along with
    # the book, one gate compiles the whole book atomically -- all or
    # nothing.
    r = _ws.proto_ready(
        post, eng, "练琴", "# 练琴\n主持多轮。",
        [_ws.member_decl("铺谱", scenario="练琴",
                         steps="1. report 谱已铺,一句"),
         _ws.member_decl("陪聊", scenario="练琴",
                         steps="1. judge 陪用户聊练琴心得,一轮一句")],
        scenario="练琴")
    c.send(json.dumps({"type": "approve", "task": r["task"]}))
    okp = wait_for(lambda: (eng.store.proto_get("练琴") or {})
                   .get("status") == "provisioned")
    m1 = eng.store.intent("铺谱") or {}
    check("0a v17 whole-book one-gate: approve once, book + all "
          "members go live atomically",
          bool(okp) and m1.get("status") == "provisioned"
          and m1.get("proto") == "练琴"
          and (eng.store.intent("陪聊") or {}).get("proto") == "练琴")
    check("0b v17 member = material: pointer stamp lands with the "
          "compile (retrieval bridge skips a separate register)",
          m1.get("migrated_to") == "protocol:练琴")

    # ---- 1. open bracket (M26: the bracket lives in the x·练琴
    # seat), task_done landing on the bracket -> hard-rejected
    # (CASELAW 46)----
    qinst = fake_instance(eng, ws_root, "练琴")
    eng._on_intent("练琴·启", "开册")
    br = wait_for(lambda: eng._bracket_of("练琴", queued=False))
    check("1a ·启 opens the bracket right away (delivered to the "
          "x·练琴 instance seat)", br is not None
          and br.get("executor") == "x·练琴")
    envp = wait_for(lambda: next(
        (x for x in qinst.host.sent
         if "protocol 练琴" in x and "package:" in x), None))
    check("1a2 M26 open-book envelope = pointer-shaped, lands on "
          "the instance seat (bypasses sidecar)",
          envp is not None
          and not any("练琴" in s and "package" in s for s in fake.sent))
    rd = post({"verb": "task_done", "task": br["id"], "outcome": "ok",
               "summary": "干完了", "token": eng.token})
    check("2a CASELAW 46: task_done landing on the bracket is "
          "hard-rejected (error points to the Shutdown key, "
          "English side)",
          "error" in rd and "Shutdown key" in rd["error"])
    check("2b bracket still open after rejection (still a chance "
          "to clear it)",
          (eng._bracket_of("练琴") or {}).get("id") == br["id"])

    # ---- 2. member trigger inside the bracket: only posts an
    # envelope, doesn't open a new task (envelope goes to the
    # instance)--
    n1 = len(qinst.host.sent)
    eng._on_intent("铺谱", "铺谱 奇迹的山")
    env = wait_for(lambda: next(
        (x for x in qinst.host.sent[n1:]
         if "intent 铺谱" in x and "step" in x), None))
    check("1b member trigger only posts an envelope (step lands "
          "on the instance seat)",
          env is not None)
    check("1c envelope is filed under the bracket task (one "
          "ledger for the whole bracket)",
          f"[task {br['id']}]" in (env or ""))
    check("1d member trigger doesn't open a deliver chain (no "
          "new ticket inside the bracket)",
          not any(dict(r2)["spec"] == "deliver:铺谱"
                  for r2 in eng.store._db.execute(
                      "SELECT spec FROM tasks")))
    check("1e trigger input travels with the envelope (host seat "
          "gets the original words)",
          "奇迹的山" in (env or ""))

    # ---- 2a2. step serialization (user ruling 2026-08-26): the
    # previous envelope is a critical section — until it is claimed
    # (step_done), a new member key is refused, not queued ----
    n1b = len(qinst.host.sent)
    r = eng._proto_member("练琴", "陪聊", "插队试探")
    check("1f0 member key while the previous step is unclaimed → "
          "refused, zero envelope",
          "still open" in str(r.get("error") or "")
          and not any("intent 陪聊" in x
                      for x in qinst.host.sent[n1b:]))
    qinst.step_state = "done"          # the claim (step_done's effect)

    # second member, same law
    n2 = len(qinst.host.sent)
    eng._on_intent("陪聊", "聊聊手感")
    plain = wait_for(lambda: any(
        "intent 陪聊" in x and "step" in x
        for x in qinst.host.sent[n2:]))
    check("1f second member, same law (envelope, no new ticket; "
          "claimed step unlocks the next key)",
          bool(plain))
    qinst.step_state = "done"          # claim 陪聊 for the sections below

    # ---- 2b. aggregate warm-up (user's idea 2026-08-16, landed
    # 08-17): the open-book package renders member declarations along
    # with the book -- the host seat warm-opens the book, zero
    # intent_get round trips
    bpkg = (ws_root / "runtime" / "tasks" / str(br["id"])
            / "package.md").read_text(encoding="utf-8")
    check("1g open-book package carries a member-declaration "
          "section: each member's E + acceptance warm up with "
          "the book",
          "Member roster" in bpkg
          and "1. report 谱已铺,一句" in bpkg
          and "陪用户聊练琴心得" in bpkg
          and "intent_get needed" in bpkg)

    # ---- 3. injection acknowledgment (CASELAW 41/48/51: the
    # criterion is a user row, not mtime)--
    defaults.INJECT_ACK_S = 0.5
    eng._inject("这句话会被对话框吃掉(测试)")
    (ack / "s1.jsonl").write_text(
        '{"type":"assistant","message":{"content":[]}}\n',
        encoding="utf-8")
    lost = wait_for(lambda: not eng._inject_watch, timeout=10)
    jtxt = eng.journal._path.read_text(encoding="utf-8")
    check("4a transcript is moving but no user row with this line "
          "→ journal inject/lost row and NO card (user ruling "
          "2026-08-25: the terminal shows the miss at a glance, "
          "the card was noise; 51 still holds for the criterion)",
          bool(lost)
          and '"kind": "inject", "name": "lost"' in jtxt
          and not any("not have landed" in str(c2.get("title"))
                      for c2 in list(eng._cards.values())))

    def _lost_ids():
        with eng._card_lock:
            return {i for i, c2 in eng._cards.items()
                    if "not have landed" in str(c2.get("title"))}

    wait_for(lambda: not eng._inject_watch, timeout=10)
    time.sleep(0.8)
    before_lost = _lost_ids()
    msg = "这句话正常送达(测试)"
    eng._inject(msg)
    row = json.dumps({"type": "user", "message": {"content": msg}},
                     ensure_ascii=False)
    (ack / "s1.jsonl").write_text(row + "\n", encoding="utf-8")
    wait_for(lambda: not eng._inject_watch, timeout=10)
    time.sleep(0.5)
    check("4b this line's user row lands in the transcript → ack, "
          "no new not-delivered card",
          _lost_ids() <= before_lost)

    # ---- 3c. busy defers the verdict (CASELAW 60 companion case,
    # 2026-08-17): while the host is mid-turn (the transcript keeps
    # growing but has no user row) don't judge it lost -- keep
    # watching; once the user row lands, ack, and never open a new
    # not-delivered card in the process ----
    before_lost2 = _lost_ids()
    msg2 = "宿主忙时投的这句(测试)"
    eng._inject(msg2)
    t_end = time.time() + 2.5
    while time.time() < t_end:          # simulate a busy host: the transcript keeps growing
        with open(ack / "s1.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"type":"assistant","message":{"content":[]}}\n')
        time.sleep(0.4)
    check("4c host busy (transcript growing, no user row) → not "
          "judged lost (4 windows that used to force a "
          "not-delivered card have passed)",
          _lost_ids() == before_lost2 and eng._inject_watch)
    row2 = json.dumps({"type": "user", "message": {"content": msg2}},
                      ensure_ascii=False)
    with open(ack / "s1.jsonl", "a", encoding="utf-8") as fh:
        fh.write(row2 + "\n")
    wait_for(lambda: not eng._inject_watch, timeout=15)
    check("4d ack once the line lands in the transcript, zero "
          "new not-delivered cards throughout",
          not eng._inject_watch and _lost_ids() == before_lost2)

    # ---- 4. ·收 closes the bracket as normal (the closing receipt
    # goes to the instance)----
    eng._on_intent("练琴·收", "收工")
    closed = wait_for(lambda: eng._bracket_of("练琴") is None)
    check("5a ·收 settles the ledger, bracket closes", bool(closed))
    endnote = wait_for(lambda: any(
        "protocol 练琴 end" in x for x in qinst.host.sent))
    check("5b closing receipt lands on the instance (just wraps "
          "up, no task_done)",
          bool(endnote))

    # ---- 5. lazy-spawn retirement (user's ruling 2026-08-23,
    # overturns the 08-22 seat lazy spawn): pressing a member on a
    # closed book = rejection, zero book-opening, zero step, zero
    # executor-seat delivery, zero deliver chain ----
    n_del2 = len(xfake.delivered)
    n3 = len(qinst.host.sent)
    eng._on_intent("铺谱", "铺谱 单发试锁")
    time.sleep(1.2)                 # give a mistakenly-opened bracket a chance to surface
    check("6a member trigger on a closed book is rejected "
          "(lazy-spawn retired, doesn't reopen the book)",
          eng._bracket_of("练琴") is None)
    check("6b rejection has zero side effects: no step envelope "
          "reaches the seat",
          not any("intent 铺谱" in x and "step" in x
                  for x in qinst.host.sent[n3:]))
    check("6c member still doesn't post to the executor seat or "
          "open a deliver chain (locked-once-in-book still holds)",
          len(xfake.delivered) == n_del2
          and not any(dict(r2)["spec"] == "deliver:铺谱"
                      for r2 in eng.store._db.execute(
                          "SELECT spec FROM tasks")))

    # ---- 7. booklet retirement (user ruling 2026-08-26: the rename
    # live-fire left a stranded booklet — there was no protocol
    # retirement path at all. intent_retire now takes a booklet name;
    # approval retires the compile unit whole) ----
    r = eng._intent_retire({"name": "练琴", "why": "旧册退役(测试)"},
                           "sidecar")
    check("7a booklet retirement proposal opens a gate",
          bool(r.get("ok")) and r.get("task") is not None)
    eng._on_approve(r["task"])
    ok7 = wait_for(lambda: (eng.store.proto_get("练琴") or {})
                   .get("status") == "retired", timeout=10)
    check("7b approval retires the booklet (soft: row stays, "
          "status flips)", bool(ok7))
    check("7c declared members retire with the book (one compile "
          "unit, one fate)",
          all((eng.store.intent(m) or {}).get("status") == "retired"
              for m in ("铺谱", "陪聊")))
    r2 = eng._intent_retire({"name": "练琴"}, "sidecar")
    check("7d re-propose on a retired booklet is refused",
          "not on the shelf" in str(r2.get("error") or ""))

    c.close()

print()
print("BRACKET PASS" if not FAILS else f"BRACKET FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
