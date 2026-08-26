"""M1 guard: provisioning / journal / channel / HTTP (--no-host mode).

Run: PYTHONIOENCODING=utf-8 python tests/test_m1.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commander import defaults                             # noqa: E402
from commander.engine import Engine                        # noqa: E402
from commander.kernel.provision import provision_home      # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


with tempfile.TemporaryDirectory() as tmp:
    ws_root = Path(tmp)

    # ---- provision: idempotent, engine-owned files rewritten each
    # boot (CASELAW 28/29) ------------------------------------------
    home = provision_home(ws_root)
    claude_md = (home / "CLAUDE.md").read_text(encoding="utf-8")
    # Memory slot (user ruling 2026-08-12): engine **only reserves the
    # slot, writes nothing** -- environment detail is his private
    # property. The old assertion "no memory file" was a fossil from
    # draft twelve (dynamic memory fully owned by harness), voided by
    # this ruling; dark artifacts still aren't provisioned.
    check("1 casts three things: CLAUDE.md / scratch / memory "
          "slot (empty -- engine only reserves the slot, writes "
          "nothing; no dark artifacts)",
          claude_md.startswith("# sidecar") and (home / "scratch").is_dir()
          and (home / "memory").is_dir()
          and not list((home / "memory").iterdir()))
    (home / "scratch" / "junk.txt").write_text("草稿", encoding="utf-8")
    (home / "memory" / "MEMORY.md").write_text("- 他记的事",
                                               encoding="utf-8")
    home2 = provision_home(ws_root)
    check("2 recast: **private memory** untouched, not one "
          "character (not evaporating on boot is the promise); "
          "scratch short-life rule (user ruling 2026-08-13) -- the "
          "draft table is wiped on every cast, put things where "
          "they belong",
          home2 == home
          and not (home / "scratch" / "junk.txt").exists()
          and (home / "scratch").is_dir()
          and (home / "memory" / "MEMORY.md").read_text(
              encoding="utf-8") == "- 他记的事")

    # ---- engine infra (no-host) ---------------------------------
    eng = Engine(ws_root, http_port=9750, ws_port=9751, spawn_host=False)
    th = threading.Thread(target=eng.run, daemon=True)
    th.start()
    time.sleep(1.5)

    page = urllib.request.urlopen(
        "http://127.0.0.1:9750/observe", timeout=5).read().decode("utf-8")
    disc = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9750/api/discover", timeout=5).read())
    check("3 observe page and discover endpoint are up",
          "INTENTOS" in page
          and disc.get("ws") == 9751)
    hubpage = urllib.request.urlopen(
        "http://127.0.0.1:9750/hub", timeout=5).read().decode("utf-8")
    check("3b hub shell page is up; /bind removed along with the "
          "key-binding module (404)",
          "flow_open" in hubpage and "iframe" in hubpage)
    try:
        urllib.request.urlopen("http://127.0.0.1:9750/bind", timeout=5)
        bind_gone = False
    except urllib.error.HTTPError as e:
        bind_gone = e.code == 404
    check("3c /bind route decommissioned", bind_gone)

    from websockets.sync.client import connect
    with connect("ws://127.0.0.1:9751", open_timeout=5) as c:
        c.send(json.dumps({"type": "hello"}))
        f = json.loads(c.recv(timeout=5))
        check("4 hello immediately replies with surface, engine "
              "owns the vocabulary",
              f.get("type") == "surface" and f.get("focus") == "sidecar"
              and f["peers"]["sidecar"]["phase"] == "off")
        c.send(json.dumps({"type": "chat", "text": "你好琴谱"}))
        f2 = json.loads(c.recv(timeout=5))
        while f2.get("type") != "chat":    # hello also sends intents frame
            f2 = json.loads(c.recv(timeout=5))
        check("5 chat broadcast mirrors verbatim (CJK as-is, no "
              "host, no crash)",
              f2.get("type") == "chat" and f2.get("text") == "你好琴谱")
        c.send(json.dumps({"type": "stop"}))
    th.join(timeout=10)
    check("6 stop frame shuts down gracefully", not th.is_alive())

    jpath = next((ws_root / "records" / "sidecar").iterdir()) / "events.jsonl"
    rows = [json.loads(x) for x in
            jpath.read_text(encoding="utf-8").splitlines()]
    kinds = [(r["kind"], r["name"]) for r in rows]
    check("7 journal: start / chat / end all present, UTF-8 "
          "verbatim",
          ("lifecycle", "start") in kinds and ("lifecycle", "end") in kinds
          and any(r.get("text") == "你好琴谱" for r in rows))

print()
print("M1 PASS" if not FAILS else f"M1 FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
