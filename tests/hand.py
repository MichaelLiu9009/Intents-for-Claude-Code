"""WS robot hand -- the batch-approve/logging hand for §2u's first live
fire (TESTPLAN-scoreopen §2).

**Does not touch engine code**: it's just a WS client, riding the frame
table the channel already receives. Three jobs, all mechanical:

1. **Full trace** -- every frame into `frames.jsonl`, the host terminal
   into `cli.log`, every card/every gate's full text into
   `30-approvals/`. Batching fast doesn't mean skipping the trail.
2. **Batch-approve** -- permission cards answered automatically per
   the boundary rule; registration gates (the `gated` rows in the
   chains ledger) read `runtime/tasks/<id>/template.md`, copy the full
   text, then approve.
3. **Call the human** -- anything that genuinely needs a human call
   (ask_user_through_os / AskUserQuestion mirror cards, out-of-bounds writes,
   denials) is never answered on their behalf; it's written into
   `NEEDS-ME.md`.

Boundary rule (TESTPLAN §2 + §6.5):
- **Reads: all allowed.** Exploring a messy directory requires
  reading it.
- **Write/rename/delete: only allowed inside playground.** The score
  library is the user's real asset -- reading is fine, not a single
  byte may be changed -- hit it and it's an automatic deny + call the
  human.

How to run:
  PYTHONIOENCODING=utf-8 python tests/hand.py record --out <evidence-dir>
  PYTHONIOENCODING=utf-8 python tests/hand.py send '{"type":"chat",...}'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# Local paths go through env vars (de-hardcoded 2026-08-23: hand.py is a
# manual live-fire fixture, it shouldn't carry anyone's local paths).
# Defaults only exist so import doesn't blow up; a real run must set them.
WS_URL = os.environ.get("INTENTOS_WS", "ws://127.0.0.1:9701")
PLAYGROUND = Path(os.environ.get("INTENTOS_PLAYGROUND", "playground"))
SCORES = Path(os.environ.get("INTENTOS_ASSET_DIR", "assets"))

# Where writes are allowed: inside playground + the "works" subdirectory
# under the asset library (per the user's 2026-08-15 ruling: recordings
# get saved there, and this directory gets newly created). The rest of
# the asset library stays **read-only** -- existing files are the
# user's real assets, not a single byte may be changed.
WORKS = SCORES / "作品"
WRITE_OK_ROOTS = [str(PLAYGROUND).lower(), str(WORKS).lower()]

WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit"}
SHELL_TOOLS = {"bash", "powershell"}
# Mutating verbs inside a shell command (matched lowercase)
MUTATORS = [
    "remove-item", "rm ", "rm -", "del ", "erase ", "rmdir",
    "move-item", "mv ", "rename-item", "ren ", "set-content",
    "add-content", "out-file", "new-item", "copy-item", "cp ",
    ">", ">>", "git checkout", "git reset", "git clean",
]

PATH_RE = re.compile(r"[A-Za-z]:\\\\[^\s\"'\|;,\)\]]+|[A-Za-z]:[\\/][^\s\"'\|;,\)\]]+")


class Hand:
    def __init__(self, out: Path):
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "30-approvals").mkdir(exist_ok=True)
        self.frames = (self.out / "frames.jsonl").open("a", encoding="utf-8")
        self.cli = (self.out / "cli.log").open("a", encoding="utf-8")
        self.log_f = (self.out / "hand.log").open("a", encoding="utf-8")
        self.needs = self.out / "NEEDS-ME.md"
        self.ws = None
        self.seen_gates: set[int] = set()
        self.n_cards = 0
        self.n_gates = 0
        self.lock = threading.Lock()

    # ---- trace -----------------------------------------------------------

    def log(self, msg: str) -> None:
        line = time.strftime("%H:%M:%S") + " " + msg
        self.log_f.write(line + "\n")
        self.log_f.flush()
        try:
            print(line, flush=True)
        except Exception:
            pass

    def alert(self, title: str, body: str) -> None:
        """Call the human: anything not answered on their behalf lands
        here (per the user: "call me when it needs intervention")."""
        with self.needs.open("a", encoding="utf-8") as f:
            f.write(f"\n## {time.strftime('%H:%M:%S')} {title}\n\n{body}\n")
        self.log("!! calling the human: " + title)

    def snap(self, fname: str, text: str) -> None:
        (self.out / "30-approvals" / fname).write_text(text, encoding="utf-8")

    # ---- boundary rule -----------------------------------------------------

    @staticmethod
    def _paths(text: str) -> list[str]:
        return PATH_RE.findall(text or "")

    def judge(self, tool: str, body: str) -> tuple[bool, str]:
        """Returns (allow?, reason). Reads always allowed; writes only
        allowed inside playground."""
        t = (tool or "").strip().lower()
        low = (body or "").lower()
        mutating = t in WRITE_TOOLS or (
            t in SHELL_TOOLS and any(m in low for m in MUTATORS))
        if not mutating:
            return True, "read / no mutating verb — allow"
        bad = [p for p in self._paths(body)
               if not any(p.lower().startswith(r) for r in WRITE_OK_ROOTS)]
        # Call out the score library by name (the one that matters most)
        hits = [p for p in bad if str(SCORES).lower() in p.lower()]
        if hits:
            return False, ("mutating verb aims at the **score library** "
                           "(the user's real asset, read-only): "
                           + " ".join(hits[:3]))
        if bad:
            return False, ("mutating verb aims outside the playground: "
                           + " ".join(bad[:3]))
        return True, "write lands inside the playground — allow"

    # ---- outbound -----------------------------------------------------------

    def send(self, frame: dict) -> None:
        with self.lock:
            try:
                self.ws.send(json.dumps(frame, ensure_ascii=False))
            except Exception as e:
                self.log(f"send failed {frame.get('type')}: {e!r}")

    # ---- automatic replies to cards --------------------------------------------------

    def on_card(self, f: dict) -> None:
        cid, kind = f.get("id"), f.get("kind")
        title, body = f.get("title") or "", f.get("body") or ""
        self.n_cards += 1
        stem = f"card-{cid:03d}-{kind}"
        self.snap(stem + ".md",
                  f"# [{kind}] {title}\n\n(card id={cid} t={f.get('t')})\n\n"
                  f"```\n{body}\n```\n\noptions: "
                  f"{json.dumps(f.get('options'), ensure_ascii=False)}\n")
        opts = f.get("options") or []
        acts = {str(o.get("action")) for o in opts if isinstance(o, dict)}

        # 1) Host permission request (M18 approval): tool name is
        # after the colon in the title
        if kind == "approval":
            tool = title.split(":", 1)[-1].strip()
            ok, why = self.judge(tool, body)
            ans = "allow" if ok else "deny"
            self.send({"type": "card_answer", "id": cid,
                       "action": "perm", "data": ans})
            self.log(f"card {cid} approval [{tool}] → {ans} ({why})")
            with (self.out / "30-approvals" / (stem + ".md")).open(
                    "a", encoding="utf-8") as fh:
                fh.write(f"\n**machine-approved**: {ans} — {why}\n")
            if not ok:
                self.alert(f"denied approval card {cid}: {tool}",
                           f"{why}\n\n```\n{body[:1500]}\n```")
            return

        # 2) Execution-layer permission gate (perm_gate) / question
        # gate (ask_user_through_os)
        if kind == "perm" and ("allow" in acts or "deny" in acts):
            ok, why = self.judge(body.split("——", 1)[0], body)
            self.send({"type": "card_answer", "id": cid,
                       "action": "allow" if ok else "deny"})
            self.log(f"card {cid} executor perm request → "
                     f"{'allow' if ok else 'deny'} ({why})")
            if not ok:
                self.alert(f"denied executor perm request {cid}",
                           f"{why}\n\n```\n{body[:1500]}\n```")
            return

        # 3) Host tail-recorded card (native popup fallback): digit 1
        # = first item (usually allow)
        if kind == "perm" and "key" in acts:
            self.send({"type": "card_answer", "id": cid,
                       "action": "key", "data": "1"})
            self.log(f"card {cid} host tail-recorded card → key 1")
            return

        # 4) Anything genuinely needing a human call: a question with
        # options is never answered on its behalf (no options = a
        # notice card, e.g. an "execution layer failed" diagnostic
        # panel -- copying it counts as handled, don't call the human)
        if kind in ("ask", "question") and opts:
            self.alert(f"[{kind}] card {cid} needs a human call: {title}",
                       f"```\n{body[:2000]}\n```\n\n"
                       f"options: {json.dumps(opts, ensure_ascii=False)}"
                       f"\n\nanswer with: `send '{{\"type\":"
                       f"\"card_answer\",\"id\":{cid},"
                       f"\"action\":\"opt:0\"}}'`")
            return

        self.log(f"card {cid} [{kind}] {title} — no answer needed "
                 f"(copied)")

    # ---- registration gates (the `gated` rows in the chains ledger) ----------------------------

    def on_chains(self, f: dict) -> None:
        for r in f.get("ledger") or []:
            if r.get("status") != "gated":
                continue
            tid = r.get("task")
            if not isinstance(tid, int) or tid in self.seen_gates:
                continue
            self.seen_gates.add(tid)
            self.n_gates += 1
            tpl = PLAYGROUND / "runtime" / "tasks" / str(tid) / "template.md"
            text = tpl.read_text(encoding="utf-8") if tpl.is_file() \
                else "(no template.md)"
            stem = f"gate-{tid:03d}-{(r.get('spec') or '?').replace('·', '-')}"
            self.snap(stem + ".md",
                      f"# human gate task#{tid} [{r.get('gate')}]\n\n"
                      f"- spec: {r.get('spec')}\n"
                      f"- intent: {r.get('intent')}\n"
                      f"- chain: {r.get('chain')}\n\n---\n\n{text}\n")
            ok, why = self.judge("", text)
            if ok:
                self.send({"type": "approve", "task": tid})
                self.log(f"gate #{tid} [{r.get('gate')}] "
                         f"{r.get('intent')} → approved")
                with (self.out / "30-approvals" / (stem + ".md")).open(
                        "a", encoding="utf-8") as fh:
                    fh.write(f"\n**machine-approved** — {why}\n")
            else:
                self.alert(f"human gate #{tid} NOT approved: "
                           f"{r.get('gate')}",
                           f"{why}\n\n```\n{text[:2000]}\n```\n\n"
                           f"to approve: `send '{{\"type\":\"approve\","
                           f"\"task\":{tid}}}'`")

    # ---- main loop --------------------------------------------------------

    def poll(self) -> None:
        while True:
            time.sleep(3)
            try:
                self.send({"type": "chains"})
            except Exception:
                return

    def run(self) -> None:
        from websockets.sync.client import connect
        self.ws = connect(WS_URL, open_timeout=10, max_size=None)
        self.log("=== machine hand online "
                 + time.strftime("%Y-%m-%d %H:%M:%S") + " ===")
        self.send({"type": "hello"})
        self.send({"type": "cli_sub"})
        self.send({"type": "chains"})
        threading.Thread(target=self.poll, daemon=True).start()
        for raw in self.ws:
            try:
                f = json.loads(raw)
            except Exception:
                continue
            if not isinstance(f, dict):
                continue
            kind = f.get("type")
            if kind == "cli":
                self.cli.write(str(f.get("data") or ""))
                self.cli.flush()
                continue
            self.frames.write(json.dumps(
                {"t": time.strftime("%H:%M:%S"), **f}, ensure_ascii=False)
                + "\n")
            self.frames.flush()
            if kind == "card":
                self.on_card(f)
            elif kind == "cards":
                for c in f.get("rows") or []:
                    self.on_card(c)
            elif kind == "chains":
                self.on_chains(f)
            elif kind == "chat":
                self.log(f"[chat/{f.get('name')}] "
                         + str(f.get("text") or "")[:400].replace("\n", " ⏎ "))
            elif kind == "feed":
                self.log(f"[feed/{f.get('kind')}] "
                         + str(f.get("text") or "")[:300])


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record")
    p.add_argument("--out", required=True)
    p = sub.add_parser("send")
    p.add_argument("frame")
    a = ap.parse_args()

    if a.cmd == "record":
        h = Hand(Path(a.out))
        while True:
            try:
                h.run()
            except Exception as e:
                h.log(f"connection lost: {e!r} — reconnecting in 3s")
                time.sleep(3)
    if a.cmd == "send":
        from websockets.sync.client import connect
        with connect(WS_URL, open_timeout=10) as ws:
            ws.send(a.frame)
            time.sleep(0.4)
        print("sent: " + a.frame)
    return 0


if __name__ == "__main__":
    sys.exit(main())
