"""CLI — run / stop / seed."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import defaults


def main(argv=None) -> int:
    # Console prints carry user-named intents (any script, any
    # language); a cp1252 Windows console would otherwise crash the
    # whole engine on the first non-Latin name (live fault
    # 2026-08-25: the [migrate] line). Never let an encoding error
    # kill the process — worst case is a replaced glyph.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="intentos")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="start the engine")
    p.add_argument("--workspace", default=".", help="workspace dir")
    # Port/model default to None: layered as defaults <
    # <workspace>/config.json < explicit flag (reconciled in
    # Engine.__init__ — config.json isn't read yet at argparse time)
    p.add_argument("--http", type=int, default=None,
                   help=f"HTTP port (default {defaults.HTTP_PORT}, "
                        f"config.json overridable)")
    p.add_argument("--ws", type=int, default=None,
                   help=f"WS port (default {defaults.WS_PORT}, "
                        f"config.json overridable)")
    p.add_argument("--no-host", action="store_true",
                   help="infra only, no CLI spawn (tests)")
    p.add_argument("--model", default=None,
                   help="general host-seat model (x·<booklet>; "
                        "default sonnet). The sidecar compile seat is "
                        "pinned to SIDECAR_MODEL and ignores this flag.")

    p = sub.add_parser("stop", help="graceful stop via channel")
    p.add_argument("--ws", type=int, default=defaults.WS_PORT)

    p = sub.add_parser("seed", help="seed the built-in templates "
                                    "(timecheck intent + translator "
                                    "booklet; skips the creation chain, "
                                    "lands provisioned — cold-start / "
                                    "demo / format exemplars)")
    p.add_argument("--workspace", default=".")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        from .engine import Engine
        return Engine(Path(args.workspace).resolve(),
                      http_port=args.http, ws_port=args.ws,
                      spawn_host=not args.no_host,
                      model=args.model).run()

    if args.cmd == "stop":
        from websockets.sync.client import connect
        try:
            with connect(f"ws://127.0.0.1:{args.ws}",
                         open_timeout=5) as ws:
                ws.send(json.dumps({"type": "stop"}))
        except OSError:
            # Nobody listening = the engine is already down. The
            # command's goal state holds either way; a raw
            # traceback here reads as "stop is broken" (audit
            # 2026-08-25 §4-other).
            print(f"no engine on ws port {args.ws} — nothing to stop")
            return 1
        print("stop sent")
        return 0

    if args.cmd == "seed":
        return _seed(Path(args.workspace).resolve())
    return 2


# ---- built-in templates (format exemplars, 2026-08-24) ---------------
# The system ships two built-ins: timecheck (a standalone intent) +
# translator (an interactive bracket booklet). They also double as
# **live teaching material for the format** — this version's thesis:
# vocabulary (closed verb set) + word-count budget + schema table =
# schema-based language (a pseudocode function body); the authoring
# format determines reproduction quality. So the templates must pass
# their own gate (self-check below), and carry zero local paths
# (environment agnostic).

TPL_TIMECHECK = {
    "name": "timecheck",
    "title": "Report the current time",
    "scenario": "timecheck",
    "steps": ("1. call PowerShell Get-Date for the current date and "
              "time\n"
              "2. report the time to the user in one short sentence "
              "— no files, no windows"),
    "acceptance": ("ok: the time was reported in chat\n"
                   "failed: Get-Date did not run"),
}

TPL_LANGUAGE = {
    "name": "language",
    "title": "Pick the target language",
    "scenario": "translate",
    "steps": ("1. ask target language — offer English, 中文, 日本語, "
              "Español, Français, Deutsch; free typing welcome\n"
              "2. report confirm the choice — it holds for this "
              "whole bracket"),
    "acceptance": ("ok: a target language is confirmed on the card\n"
                   "failed: the card was never answered"),
}

TPL_TRANSLATE = {
    "name": "translate",
    "title": "Translate the screen under the mouse",
    "scenario": "translate",
    "procedures": ["screenshot"],
    "steps": ("1. read the screenshot file named on the step's "
              "materials line\n"
              "2. judge translate every piece of visible text into "
              "the bracket's target language (English if none "
              "picked)\n"
              "3. ask the translation itself as one card — options: "
              "Done, Again"),
    "acceptance": ("ok: the translation card was answered\n"
                   "failed: screenshot unreadable"),
}

TPL_PROTO = {
    "name": "translator",
    "scenario": "translate",
    "subtype": "interactive",
    "members": ["language", "translate"],
}

TPL_SKILL = """\
# translator — booklet skill

You host the **translator** booklet: live translation of whatever is
on the user's screen, one keypress per shot.

State you carry across the bracket: **the target language** — default
English until the `language` step sets it.

## Steps

- **language** — run its E: raise one card (ask_user) offering common
  languages plus free typing; remember the answer as the bracket's
  target language and confirm it in one line.

- **translate** — the step envelope ends with `materials: <path>`: a
  screenshot the engine just took of the monitor under the user's
  mouse. Read that image, then translate **all visible text** into
  the target language. Deliver the translation as one card
  (ask_user): question = the translation itself (compact — lead with
  what matters if space is tight), options = Done, Again. If "Again"
  comes back, redo the translation with more care — the card returned,
  so assume something was off or truncated.

## Discipline

- Write no files; everything stays in the bracket and on cards.
- One step, one card — never stack extra questions.
- The target language survives the whole bracket; a new `language`
  step replaces it.
"""


def _seed(ws_root: Path) -> int:
    from .kernel import wspace
    from .kernel.procrun import text_hash
    from .kernel.store import Store

    # Template self-check: system templates must pass their own
    # registration gate (schema + E-grammar) — if the teaching
    # material is broken, what it teaches is wrong; refuse to seed on
    # any fault, naming each one.
    probs = list(wspace.validate(TPL_TIMECHECK, "intent"))
    probs += wspace.validate(TPL_PROTO, "protocol")
    for d in (TPL_TIMECHECK, TPL_LANGUAGE, TPL_TRANSLATE):
        probs += [f"{d['name']}: {p}" for p in
                  wspace.validate(d, "intent")]
        _, ep = wspace.parse_steps(d["steps"])
        probs += [f"{d['name']}: {p}" for p in ep]
    bad_procs = [p for p in TPL_TRANSLATE["procedures"]
                 if p not in defaults.PHYS_PROCEDURES]
    if bad_procs:
        probs.append(f"translate: procedures not in the engine library: "
                     f"{bad_procs}")
    if probs:
        print("seed refused — a template fails its own gates:")
        for p in probs:
            print("  · " + p)
        return 1

    # Workspace canonical copy (format exemplar: the directory is the
    # source, registration = compiling; the engine never deletes a
    # user's directory, provision is idempotent and only fills in
    # missing files)
    home = ws_root / defaults.INSTANCES_DIRNAME / defaults.OS_MODULE
    wspace.provision(home, "timecheck", TPL_TIMECHECK)
    pd = wspace.wdir(home, "translator")
    pd.mkdir(parents=True, exist_ok=True)
    (pd / wspace.PROTO_DECL_NAME).write_text(
        json.dumps(TPL_PROTO, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (pd / wspace.SKILL_NAME).write_text(TPL_SKILL, encoding="utf-8")
    wspace.write_schema_md(pd, "protocol")
    for d in (TPL_LANGUAGE, TPL_TRANSLATE):
        md = wspace.member_dir(pd, d["name"])
        md.mkdir(parents=True, exist_ok=True)
        (md / wspace.DECL_NAME).write_text(
            json.dumps(d, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    # Store side (registration = the mechanical equivalent of
    # compiling; seed artifacts skip human approval — they ship with
    # the engine). Templates are engine property: re-running seed
    # ALWAYS refreshes their rows to the current template text (the
    # upgrade path after a word-list change — no legacy fallback).
    st = Store(ws_root / "state.db")
    # Collision guard (audit 2026-08-26): templates are engine
    # property and re-seeding always refreshes THEM — but a USER
    # asset that happens to share a template's name must never be
    # silently overwritten. Engine property is marked scope='seed'
    # (a column the MCP revision channel can never write), so the
    # distinction is mechanical, not guesswork.
    row = st.intent("timecheck")
    if row is not None and (row.get("scope") or "") != "seed":
        print("seed: skipped 'timecheck' — a user asset already "
              "owns that name (rename it and re-run seed to get "
              "the template).")
    else:
        if row is None:
            st.intent_create("timecheck", title=TPL_TIMECHECK["title"],
                             scenario=TPL_TIMECHECK["scenario"],
                             steps=TPL_TIMECHECK["steps"], fires=1,
                             scope="seed")
        st.intent_revise("timecheck", status="provisioned",
                         title=TPL_TIMECHECK["title"],
                         scenario=TPL_TIMECHECK["scenario"],
                         steps=TPL_TIMECHECK["steps"],
                         instructions=TPL_TIMECHECK["acceptance"],
                         scope="seed")
        st.compile_delivery("timecheck")
    pr = st.proto_get("translator")
    if pr is not None and sorted(pr.get("members") or [])             != sorted([TPL_LANGUAGE["name"], TPL_TRANSLATE["name"]]):
        print("seed: skipped 'translator' — an existing booklet by "
              "that name has a different roster (yours); rename it "
              "and re-run seed to get the template.")
        st.close()
        print("seeded: timecheck refreshed where engine-owned; "
              "translator left untouched.")
        return 0
    st.proto_stage("translator", subtype="interactive",
                   scenario=TPL_PROTO["scenario"],
                   staged_hash=text_hash(TPL_SKILL))
    st.proto_compile_unit("translator",
                          [TPL_LANGUAGE, TPL_TRANSLATE],
                          owner=defaults.OS_MODULE)
    sk = wspace.utility_skill_path(ws_root, "translator")
    sk.parent.mkdir(parents=True, exist_ok=True)
    sk.write_text(TPL_SKILL, encoding="utf-8")
    st.close()
    print("seeded: timecheck (intent) + translator (booklet: "
          "language / translate — screenshot prelude). Both are "
          "format exemplars; sources under the sidecar home.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
