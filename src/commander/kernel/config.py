"""workspace config file (user ruling 2026-08-24: there needs to be
a dedicated config file layered on top of defaults).

Shape: `<workspace>/config.json`, a single top-level object; keys =
the **existing ALL-CAPS scalar constant names** in defaults.py
(int / float / str / bool), plus **str-list knobs** (e.g.
PERM_ALLOW — the always-allow ledger, permission-surface
consolidation 2026-08-24). Templates, tables, and tuples are not
accepted — those are behavior design, not knobs; changing them
still means changing source (CONFIG.md §9).

Discipline (same family as CASELAW 25): **unknown key / type
mismatch = reject on power-up**, silently swallowing a key is the
hardest class of bug to trace. Layering: defaults < config.json <
explicit CLI flags. Two writers: the user by hand; the engine only
appends to the PERM_ALLOW knob via grant() when a human approves
Always (read-modify-write, all other keys kept as-is).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import defaults

NAME = "config.json"


class ConfigError(SystemExit):
    """A hard rejection with a message — dies at startup, never
    runs sick."""


_MISSING = object()


def _knob(k: str):
    """Name → defaults' current value; reports _MISSING if it isn't
    a tunable knob. str only accepts **single-line and ≤80 chars**
    (model name / seat name / tool string) — templates and scripts
    are also str, but those are behavior design, not exposed to the
    config surface (CONFIG.md §9). list only accepts all-str
    elements (rule-ledger shape; each element single-line ≤200)."""
    if not (isinstance(k, str) and k.isupper() and hasattr(defaults, k)):
        return _MISSING
    cur = getattr(defaults, k)
    if isinstance(cur, str) and ("\n" in cur or len(cur) > 80):
        return _MISSING
    if isinstance(cur, list):
        return (cur if all(isinstance(x, str) for x in cur)
                else _MISSING)
    return cur if isinstance(cur, (bool, int, float, str)) else _MISSING


def load(ws_root: Path) -> dict:
    """Read + full validation; changes no state. Missing file =
    {} (config is purely optional)."""
    p = Path(ws_root) / NAME
    if not p.is_file():
        return {}
    try:
        # utf-8-sig: tolerate a BOM (live-fire 2026-08-24, setup
        # from scratch: both Notepad and PowerShell 5.1's utf8
        # write a BOM, the hand-edit surface must accept it)
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise ConfigError(f"[intentos] {NAME}: bad JSON — {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"[intentos] {NAME}: top level must be an object")
    out: dict = {}
    for k, v in raw.items():
        cur = _knob(k)
        if cur is _MISSING:
            raise ConfigError(
                f"[intentos] {NAME}: unknown knob '{k}' — knobs are the "
                f"ALL-CAPS scalar constants in commander/defaults.py "
                f"(see docs/CONFIG.md); templates/tables are not "
                f"file-configurable")
        if isinstance(cur, bool):
            ok = isinstance(v, bool)
        elif isinstance(cur, list):
            ok = (isinstance(v, list)
                  and all(isinstance(x, str) and "\n" not in x
                          and 0 < len(x) <= 200 for x in v))
        elif isinstance(cur, float):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            v = float(v) if ok else v
        elif isinstance(cur, int):
            ok = isinstance(v, int) and not isinstance(v, bool)
        else:                                   # str
            ok = isinstance(v, str)
        if not ok:
            raise ConfigError(
                f"[intentos] {NAME}: '{k}' wants "
                f"{type(cur).__name__}, got {type(v).__name__}")
        out[k] = v
    return out


def apply(overrides: dict) -> None:
    for k, v in overrides.items():
        setattr(defaults, k, v)


def grant(ws_root: Path, rules: list[str]) -> list[str]:
    """always-allow ledger write (permission-surface consolidation
    2026-08-24): dedupes and appends the rules into config.json's
    PERM_ALLOW, and syncs defaults.PERM_ALLOW (takes effect
    immediately at runtime). All other keys kept as-is; **does not
    rescue** a file with broken JSON (that's the user's hand-edit
    surface, an engine overwrite would swallow their edits) —
    raises OSError for the caller to log. Returns the full ledger
    after appending."""
    rules = [r for r in rules
             if isinstance(r, str) and r and "\n" not in r
             and len(r) <= 200]
    p = Path(ws_root) / NAME
    raw: dict = {}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            raise OSError(f"{NAME} is not valid JSON — fix it by hand "
                          f"before granting ({e})")
        if not isinstance(raw, dict):
            raise OSError(f"{NAME} top level must be an object")
    ledger = [x for x in (raw.get("PERM_ALLOW") or [])
              if isinstance(x, str)]
    for r in rules:                 # dedupe (rules can self-dupe too)
        if r not in ledger:
            ledger.append(r)
    raw["PERM_ALLOW"] = ledger
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    defaults.PERM_ALLOW = ledger
    return ledger
