"""Workspace config file guard (user ruling 2026-08-24) --

`<workspace>/config.json`: keys = ALL-CAPS **scalar** constants that
already exist in defaults (str accepts only single-line <=80 chars;
templates/tables not accepted) + str list knobs (PERM_ALLOW ledger,
permission surface consolidation 2026-08-24); unknown key / type
mismatch = rejected on boot (CASELAW 25 family); layering:
defaults < config.json < explicit kwargs.
grant() = the engine's sole write path (appends to PERM_ALLOW with
one call when a human approves Always).

Run: PYTHONIOENCODING=utf-8 python tests/test_config.py
"""
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from commander import defaults                          # noqa: E402
from commander.engine import Engine                     # noqa: E402
from commander.kernel import config as wsconfig         # noqa: E402

FAILS = []


def check(label, cond):
    print(("OK  " if cond else "FAIL") + " " + label)
    if not cond:
        FAILS.append(label)


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ws = Path(tmp)
    cfg = ws / wsconfig.NAME

    check("1 missing file = empty override "
          "(config purely optional)",
          wsconfig.load(ws) == {})

    cfg.write_text(json.dumps({"STEP_QUIET_S": 7, "HTTP_PORT": 9123,
                               "SIDECAR_MODEL": "sonnet"}),
                   encoding="utf-8")
    ov = wsconfig.load(ws)
    check("2 scalar knobs all pass (int / float takes int / "
          "single-line str)",
          ov["HTTP_PORT"] == 9123
          and ov["STEP_QUIET_S"] == 7.0
          and isinstance(ov["STEP_QUIET_S"], float)
          and ov["SIDECAR_MODEL"] == "sonnet")

    def rejects(payload) -> bool:
        cfg.write_text(payload if isinstance(payload, str)
                       else json.dumps(payload), encoding="utf-8")
        try:
            wsconfig.load(ws)
            return False
        except SystemExit:
            return True

    check("3 unknown key rejected (hard reject by name, "
          "no silent swallow)",
          rejects({"NOT_A_KNOB": 1}))
    check("4 lowercase key rejected (knob names = all-caps "
          "constant names)",
          rejects({"step_quiet_s": 7}))
    check("5 templates/tables not accepted (dict isn't scalar; "
          "multi-line long str is a script)",
          rejects({"E_VERBS": {}})
          and rejects({"HOME_CLAUDE_MD": "x"})
          and rejects({"PROTOCOL_PACKAGE_MD": "y"}))
    check("6 type gate (str posing as int rejected; "
          "bool doesn't pass as int)",
          rejects({"HTTP_PORT": "9700"})
          and rejects({"HTTP_PORT": True})
          and rejects({"SIDECAR_MODEL": 3}))
    check("7 bad JSON / non-object top level rejected",
          rejects("not json {") and rejects("[1, 2]"))
    # BOM tolerance (live incident 2026-08-24, fresh setup: Notepad/
    # PS5.1's utf8 both write BOM -- hand-edited files must be accepted)
    cfg.write_bytes(b'\xef\xbb\xbf{"SIDECAR_MODEL": "sonnet"}')
    check("7a BOM'd config still reads fine (utf-8-sig)",
          wsconfig.load(ws)["SIDECAR_MODEL"] == "sonnet")

    # ---- PERM_ALLOW ledger (perm surface consolidation 2026-08-24) ----
    cfg.write_text(json.dumps(
        {"PERM_ALLOW": ["mcp__foo__bar", "Read(//d/notes/**)"]},
        ensure_ascii=False), encoding="utf-8")
    ov = wsconfig.load(ws)
    check("7b list knob: all-str list passes the gate",
          ov["PERM_ALLOW"] == ["mcp__foo__bar", "Read(//d/notes/**)"])
    check("7c list type gate: mixed types / multi-line / "
          "over-length entries rejected",
          rejects({"PERM_ALLOW": [1]})
          and rejects({"PERM_ALLOW": ["a\nb"]})
          and rejects({"PERM_ALLOW": ["x" * 201]})
          and rejects({"PERM_ALLOW": "Read"}))
    old_ledger = defaults.PERM_ALLOW
    try:
        cfg.write_text(json.dumps(
            {"SIDECAR_MODEL": "sonnet", "PERM_ALLOW": ["WebSearch"]},
            ensure_ascii=False), encoding="utf-8")
        got = wsconfig.grant(ws, ["mcp__x__y", "WebSearch",
                                  "mcp__x__y"])
        raw = json.loads(cfg.read_text(encoding="utf-8"))
        check("7d grant appends: dedupe-merge, other keys kept "
              "as-is, defaults synced",
              got == ["WebSearch", "mcp__x__y"]
              and raw["PERM_ALLOW"] == ["WebSearch", "mcp__x__y"]
              and raw["SIDECAR_MODEL"] == "sonnet"
              and defaults.PERM_ALLOW == ["WebSearch", "mcp__x__y"])
        cfg.unlink()
        got2 = wsconfig.grant(ws, ["Read(//d/a/**)"])
        check("7e grant works with no file too "
              "(config.json created fresh)",
              got2 == ["Read(//d/a/**)"]
              and json.loads(cfg.read_text(encoding="utf-8"))
              ["PERM_ALLOW"] == ["Read(//d/a/**)"])
        cfg.write_text("{oops", encoding="utf-8")
        try:
            wsconfig.grant(ws, ["Bash"])
            bad_ok = False
        except OSError:
            bad_ok = True
        check("7f grant doesn't rescue bad JSON (won't swallow "
              "a user's hand-edit, raises to caller to log)",
              bad_ok)
        cfg.unlink()
    finally:
        defaults.PERM_ALLOW = old_ledger

    # ---- Engine reconciliation: config applies + explicit kwargs win ----
    old = (defaults.STEP_QUIET_S, defaults.HTTP_PORT, defaults.WS_PORT)
    cfg.write_text(json.dumps({"STEP_QUIET_S": 7, "HTTP_PORT": 9123,
                               "WS_PORT": 9124}), encoding="utf-8")
    eng = Engine(ws, spawn_host=False)          # no explicit port
    check("8 config takes effect (defaults overwritten) + "
          "None arg picks up config port",
          defaults.STEP_QUIET_S == 7.0
          and eng.http_port == 9123 and eng.ws_port == 9124
          and eng._cfg.get("HTTP_PORT") == 9123)
    check("8b the channel is armed with the **resolved** ports, not "
          "the raw args (audit 2026-08-25: the documented flag-less "
          "`intentos run --workspace <dir>` handed Channel port=None "
          "-> serve() TypeError before anything bound, and "
          "origin_port=None armed the WS origin gate with 0 — which "
          "is also the only path by which these two config knobs can "
          "take effect at all)",
          eng.channel.port == 9124 and eng.channel.origin_port == 9123)
    eng2 = Engine(ws, http_port=9223, ws_port=9224, spawn_host=False)
    check("9 explicit arg wins over config (CLI flags / "
          "test ports not overridden)",
          eng2.http_port == 9223 and eng2.ws_port == 9224)
    (defaults.STEP_QUIET_S, defaults.HTTP_PORT, defaults.WS_PORT) = old

    ws2 = ws / "ws2"
    ws2.mkdir()
    eng3 = Engine(ws2, http_port=9323, ws_port=9324, spawn_host=False)
    check("10 workspace without config has zero effect "
          "(_cfg empty)",
          eng3._cfg == {} and defaults.STEP_QUIET_S == old[0])

print()
if FAILS:
    print("CONFIG FAIL:", FAILS)
    sys.exit(1)
print("CONFIG PASS")
